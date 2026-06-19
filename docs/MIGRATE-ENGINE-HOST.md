# Перенос существующего движка на отдельный хост

Runbook: как «переехать» движком (напр. `b1`) с тестового CT400 на реальный
seedbox, сохранив id и не потеряв раздачи. Проверено на `b1 → 192.168.1.171`
(2026-06-19). Применимо к `b2..b6` по тому же шаблону.

Ключевая идея: движки на CT400 и на удалённом хосте **саморегистрируются под одним
id** и будут перетирать друг другу запись в реестре каждый heartbeat. Поэтому
**сначала убираем движок с CT400**, потом поднимаем его на новом хосте.

## 0. Предусловия

- Удалённый хост: Docker + Compose v2, сетевая достижимость до оркестратора
  (`http://<CT400-IP>:8000`) и обратно (оркестратор → `https://<host>:8081`).
- Порт API `8081` и BT-порт движка свободны на хосте.
- Тот же `SEEDING_ENGINE_REGISTER_KEY` и `SEEDING_ENGINE_API_TOKEN`, что у оркестратора.

## 1. Снять раздачи с движка (если есть)

Раздачи переносятся на другие движки той же машины через orchestrator API
(`transport=auto` сам выберет `media`/`direct`/`http`). Источник удаляется только
после подтверждённой копии.

```bash
# admin-ключ нужен; раздать round-robin по b2..b6
for id in <torrent_ids>; do
  curl -fsS -X POST -H "X-API-Key: $KEY" \
    "http://127.0.0.1:8000/api/v1/torrents/$id/migrate?engine_id=<target>&transport=auto"
  # опрос: GET /api/v1/torrents/$id/migrate-status  (ждём phase=done)
done
```

Убедиться, что на исходном движке `0` раздач, остальные `seeding`.

## 2. Убрать движок с CT400 (control plane)

1. Удалить блок `engine-b1` из `docker-compose.multi-engine.yml` (сервис, том
   `seeding_b1`, ссылки в `depends_on` у `api` и `queue_worker`) и из
   `docker-compose.multi-engine.media.yml`.
2. Удалить запись `b1` из `config/engines.ct400.json` (иначе останется как
   `static` источник в реестре).
3. Остановить контейнер и пересобрать стек без него:

   ```bash
   docker stop containerd-engine-b1-1
   bash scripts/deploy-ct400.sh up -d --remove-orphans
   docker restart containerd-api-1 containerd-queue_worker-1   # перечитать engines.json
   ```

После этого реестр не должен содержать `b1` (или только `dynamic` после шага 3).

## 3. TLS-сертификат для нового хоста (с той же CA)

CA (`ca.key`) живёт на CT400 и **не уезжает** на хосты. Генерируем серверный
cert с SAN на IP нового хоста, переиспользуя CA:

```bash
mkdir -p /tmp/certs-new && cp certs/ca.crt certs/ca.key /tmp/certs-new/
CERTS_DIR=/tmp/certs-new scripts/gen-certs.sh "DNS:localhost,IP:127.0.0.1,IP:<host-ip>"
# скопировать на хост ТОЛЬКО ca.crt + engine.crt + engine.key
```

## 4. Поднять движок на новом хосте

```bash
git clone --depth 1 https://github.com/filyse/torrent-seeding-architecture.git seeding-engine
cd seeding-engine && mkdir -p certs   # положить туда ca.crt/engine.crt/engine.key
cp .env.engine.example .env.engine    # заполнить (см. ниже) и:
bash scripts/deploy-engine.sh
```

`.env.engine` (пример для b1 на 192.168.1.171):

```
SEEDING_ENGINE_ID=b1
SEEDING_ORCHESTRATOR_URL=http://192.168.1.101:8000
SEEDING_ENGINE_REGISTER_KEY=ct400-engine-register
SEEDING_ENGINE_API_TOKEN=ct400-engine-api-token
SEEDING_ENGINE_ADVERTISE_URL=https://192.168.1.171:8081
SEEDING_ENGINE_LISTEN_PORT=50001
SEEDING_ENGINE_API_PORT=8081
SEEDING_ENGINE_TLS=1
```

Данные по умолчанию в docker-volume `engine_data` (пустой движок). Для раздач
с большим контентом смонтируй нужный каталог в `/data` (override compose).

## 5. Проверка

```bash
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/engines/registry        # b1: source=dynamic, in_pool=true
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/engines/b1/connectivity # reachable=true, bt listening=true
```

## 6. Открыть BT-порт на роутере (для движка с раздачами — обязательно)

Без входящего проброса на seedbox раздаётся **0 КБ/с**: оркестраторская проверка
(`connectivity`) ходит по LAN и зелёная, но интернет-пиры не достучатся. Симптом —
много раздач в `seeding`, но `up = 0` и нет established-соединений на BT-порту.

Проверить **реальный** порт libtorrent внутри контейнера (а не только публикацию Docker):

```bash
# 50001 = C351, 51413 = C8D5 (hex). Должен слушаться нужный порт:
docker exec <engine> sh -c 'grep -iE "C351|C8D5" /proc/net/tcp /proc/net/udp'
```

На роутере (OpenWrt/fw4) — DNAT WAN→`<host>:<BT-порт>`, tcp+udp. Если порт раньше
вёл на старый хост (напр. диапазон `50001-50006 → CT400`), **сузить старое правило**
и добавить новое на новый хост:

```sh
uci set firewall.<ct400_rule>.src_dport='50002-50006'   # было 50001-50006
uci set firewall.<ct400_rule>.dest_port='50002-50006'
S=$(uci add firewall redirect)
uci set firewall.$S.name='seeding_b1_171_wan1'
uci set firewall.$S.src='wan'; uci set firewall.$S.src_dport='50001'
uci set firewall.$S.dest='lan'; uci set firewall.$S.dest_ip='192.168.1.171'
uci set firewall.$S.dest_port='50001'
uci add_list firewall.$S.proto='tcp'; uci add_list firewall.$S.proto='udp'
uci set firewall.$S.target='DNAT'; uci set firewall.$S.reflection='1'
uci commit firewall && /etc/init.d/firewall reload
```

Проверка снаружи (с любого внешнего хоста):

```bash
(exec 3<>/dev/tcp/<WAN-IP>/50001) && echo OPEN || echo closed
# и на движке убедиться, что появились пиры:
docker exec <engine> sh -c 'grep -E " 01 " /proc/net/tcp | grep C351 | wc -l'
```

## Замечания и подводные камни

- **Flip-flop реестра**: если оставить старый контейнер движка работать, он и новый
  хост будут попеременно перетирать `url` в БД. Всегда сначала шаг 2.
- **BT-порт**: для входящих пиров из интернета нужен проброс (DNAT) на роутере на
  `<host-ip>:<BT-порт>` (см. шаг 6). Для пустого движка не критично, для движка с
  раздачами — обязательно, иначе `up = 0`.
- **Какой порт реально слушает libtorrent**: `docker-compose.engine.yml` выводит
  `LT_LISTEN_INTERFACES` из `SEEDING_ENGINE_LISTEN_PORT`, поэтому при штатном деплое
  bind = заданному порту. Но если запускать движок другим путём (или с чужим
  `LT_LISTEN_INTERFACES`), libtorrent уйдёт на дефолт `51413`. Сверяйтесь с `/proc`
  (см. шаг 6), а не только с публикацией Docker.
- **Stale `docker-compose.override.yml`**: `docker compose up` без явных `-f`
  автоматически подхватывает `override.yml`. Если там остался старый порт — он
  «перехватит» публикацию. Деплой движка всегда с явными `-f docker-compose.engine.yml
  -f docker-compose.b1-content.yml`.
- **Безопасность**: порт `8081` слушает `0.0.0.0`, но защищён TLS + `X-Engine-Token`.
  Желательно ограничить его фаерволом на IP оркестратора.
- **media_path**: у удалённого движка его нет (нет общего `/media`), поэтому
  переносы на него идут транспортом `http`/`direct`, не `media`.
