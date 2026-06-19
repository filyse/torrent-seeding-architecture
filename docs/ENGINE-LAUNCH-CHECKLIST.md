# Чек-лист запуска и проверки нового движка

Практический чек-лист для поднятия движка (`b1..b6`, `a1..` и т.д.) на отдельном
хосте и проверки, что он **реально раздаёт в интернет**, а не только «зелёный» в UI.

Появился после кейса `b1 → 192.168.1.171`, где движок был healthy и
`connectivity=reachable`, но отдача была `0 КБ/с` — потому что WAN-порт вёл на другой
хост. Поэтому проверки доведены до **внешнего теста порта и реальных пиров**.

Подстановки: `<ENG>` — id движка (`b2`), `<HOST>` — IP хоста (`192.168.1.171`),
`<WAN>` — внешний IP (`88.204.56.176`), `<PORT>` — BT-порт движка (`50002`),
`<ORCH>` — control plane (`http://192.168.1.101:8000`), `$KEY` — admin API-ключ.

---

## A. Перед запуском (конфигурация)

- [ ] `.env.engine`: `SEEDING_ENGINE_ID=<ENG>`, `SEEDING_ENGINE_LISTEN_PORT=<PORT>`,
      `SEEDING_ENGINE_API_PORT`, `SEEDING_ORCHESTRATOR_URL=<ORCH>`,
      `SEEDING_ENGINE_ADVERTISE_URL` (адрес, по которому **оркестратор** дойдёт до движка),
      `SEEDING_ENGINE_REGISTER_KEY` и `SEEDING_ENGINE_API_TOKEN` (совпадают с оркестратором).
- [ ] BT-порт `<PORT>` уникален на хосте (не занят rtorrent/другим движком). Соседние
      порты: rtorrent, `b1=50001`, и т.д.
- [ ] TLS (если включён у оркестратора): `SEEDING_ENGINE_TLS=1`, `certs/` на месте,
      `ADVERTISE_URL` на `https://`.
- [ ] Нет конфликта id: тот же `<ENG>` **не** работает одновременно на другом хосте
      (иначе heartbeat'ы перетирают `url` в реестре — см. MIGRATE-ENGINE-HOST.md).
- [ ] Если движок сидирует существующий контент: host-local
      `docker-compose.<ENG>-content.yml` монтирует данные **read-only** в `/data/<ENG>`.

## B. Запуск

- [ ] Поднять явными `-f` (чтобы не подхватился случайный `override.yml`):

  ```bash
  cd ~/seeding-engine
  docker compose --env-file .env.engine \
    -f docker-compose.engine.yml [-f docker-compose.<ENG>-content.yml] up -d --build
  ```

- [ ] Контейнер `Up ... (healthy)`: `docker ps | grep <ENG>`.
- [ ] В логах нет повторных рестартов/исключений: `docker logs <ENG>-seeding | tail`.

## C. Контрольная плоскость (оркестратор видит движок)

- [ ] В реестре, `source=dynamic`, `in_pool=true`:

  ```bash
  curl -s -H "X-API-Key: $KEY" <ORCH>/api/v1/engines/registry
  ```

- [ ] Connectivity зелёный (API + BT слушается по LAN):

  ```bash
  curl -s -H "X-API-Key: $KEY" <ORCH>/api/v1/engines/<ENG>/connectivity
  # reachable=true, bt listening=true
  ```

## D. Реальный BT-порт (а не только публикация Docker) ⚠️

`SEEDING_ENGINE_LISTEN_PORT` идёт только в регистрацию; фактический bind libtorrent
задаёт `LT_LISTEN_INTERFACES` (его выставляет `docker-compose.engine.yml` из
`SEEDING_ENGINE_LISTEN_PORT`). Сверяемся с ядром, а не с `docker ps`:

- [ ] libtorrent слушает именно `<PORT>` (TCP и UDP) внутри контейнера:

  ```bash
  # hex: 50001=C351, 50002=C352, 50003=C353, 51413=C8D5 (дефолт libtorrent — НЕ должен быть)
  docker exec <ENG>-seeding sh -c 'grep -iE "<HEX>" /proc/net/tcp /proc/net/udp'
  ```

- [ ] Docker публикует тот же `<PORT>` tcp+udp: `docker ps --format '{{.Names}} {{.Ports}}' | grep <ENG>`.

## E. Доступность из интернета (WAN) ⚠️ — без этого отдача = 0

- [ ] На роутере есть DNAT `WAN:<PORT> → <HOST>:<PORT>` (tcp+udp), и он **не**
      перекрыт диапазоном на другой хост. Проверить:

  ```bash
  ssh router 'nft list ruleset | grep <PORT>'   # dnat ip to <HOST>:<PORT>
  ```

- [ ] Внешний тест с любого хоста вне LAN (VDS):

  ```bash
  ssh vds '(exec 3<>/dev/tcp/<WAN>/<PORT>) && echo OPEN || echo closed'   # ожидаем OPEN
  ```

## F. Раздача реально идёт

- [ ] Появляются established-соединения пиров на BT-порту (не только локальные):

  ```bash
  docker exec <ENG>-seeding sh -c 'grep -E " 01 " /proc/net/tcp | grep <HEX> | wc -l'
  ```

- [ ] Трекеры анонсятся успешно (на раздаче `verified=true`, без ошибок анонса):
      выборочно `GET <ORCH>/api/v1/torrents/<id>/trackers`.
- [ ] Раздачи в статусе `seeding` (не `error`/`checking`); счётчик отдачи в UI растёт по
      мере подключения личеров (снимок рантайма обновляется ~10 c).
- [ ] Для импортированного контента: на одной раздаче форс-recheck доходит до 100% без
      ошибок (подтверждает корректность `save_path`).

## G. После

- [ ] Удалить временный admin-ключ, если выпускали.
- [ ] Зафиксировать изменения роутера/доков (правки роутера — в БД роутера через `uci commit`).
- [ ] Удалить стихийные `docker-compose.override.yml` со старыми портами на хосте.

---

### Быстрая команда «всё разом» (после запуска)

```bash
# на хосте движка
docker ps | grep <ENG>-seeding
docker exec <ENG>-seeding sh -c 'grep -iE "<HEX>" /proc/net/tcp'        # слушает <PORT>
# на control plane
curl -s -H "X-API-Key: $KEY" <ORCH>/api/v1/engines/<ENG>/connectivity
# снаружи
ssh vds '(exec 3<>/dev/tcp/<WAN>/<PORT>) && echo OPEN || echo closed'
```

См. также: `MIGRATE-ENGINE-HOST.md` (переезд движка между хостами),
`IMPORT-RTORRENT.md` (импорт раздач из rtorrent без копирования).
