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
      `docker-compose.<ENG>-content.yml` монтирует данные в `/data/<ENG>`
      (отдельный диск хоста, не каталог на корневой ФС).
- [ ] **`SEEDING_STORAGE_REQUIRE_MOUNT=1`** (дефолт `docker-compose.engine.yml`).
      Движок не стартует, пока `/data/<ENG>` не другой filesystem, чем `/data`.
      `0` только для dev без своего диска. Спека ниже, раздел G.
- [ ] Host-local compose, который **не** подключает `docker-compose.engine.yml`
      (`docker-compose.a-host.yml`, `docker-compose.c-host.yml`), задаёт
      `SEEDING_STORAGE_REQUIRE_MOUNT: "1"` в своём `environment` сам. Иначе
      entrypoint считает настройку выключенной.
- [ ] Контейнер движка запускается как `user: "1000:1000"`. Это тот же uid, что у
      FTP на хосте (`rudub` / `rudub2` / `rudub3`). `docker-compose.engine.yml`
      задаёт `user` сам. `docker-compose.a-host.yml` и `docker-compose.c-host.yml`
      должны задать его на сервисе отдельно: они этот файл не подключают.
      Иначе libtorrent создаёт файлы `root:root`, и FTP не может их удалить.
      `chown` после импорта и upload остаётся подстраховкой, если процесс всё же
      стартовал от root.
- [ ] `/data/.state`, `/data/.fastresume` и `/data/.torrents` доступны на запись
      uid 1000. Сам каталог `/data` может остаться у root.

## B. Запуск

- [ ] Поднять явными `-f` (чтобы не подхватился случайный `override.yml`):

  ```bash
  cd ~/seeding-engine
  docker compose --env-file .env.engine \
    -f docker-compose.engine.yml [-f docker-compose.<ENG>-content.yml] up -d --build
  ```

- [ ] Контейнер `Up ... (healthy)`: `docker ps | grep <ENG>`.
- [ ] Процесс — uid 1000, новый файл в каталоге раздачи тоже:

  ```bash
  docker exec <ENG>-seeding id
  # uid=1000 gid=1000
  docker exec <ENG>-seeding sh -c 'touch /data/<ENG>/.uid-test && ls -l /data/<ENG>/.uid-test && rm /data/<ENG>/.uid-test'
  # -rw-r--r-- 1 1000 1000
  ```

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
- [ ] В покое раздачи в статусе `seeding`, не `error`. Сразу после рестарта
      волна `checking` — это проход по файлам, не скачивание и не сбой
      (спека: `docs/TORRENT_STATUS.md`). Одновременно идёт не больше лимита
      «Проверок на движке», остальные «Ждёт проверки»
      (`docs/WORK_QUEUES.md`). Волна должна сойти в `seeding`.
      Счётчик отдачи в UI растёт по мере подключения личеров
      (снимок рантайма обновляется ~10 c).
- [ ] Для импортированного контента: на одной раздаче форс-recheck доходит до 100% без
      ошибок (подтверждает корректность `save_path`).

## G. Диск раздачи (место и порог алерта) ⚠️

Движок считает свободное место по **каталогу раздачи** (`storage_prefix` = `/data/<ENG>`),
а **не** по корню контейнера `/data`. На выделенном хосте под каждый движок примонтирован
свой диск именно в `/data/<ENG>`; если смонтировать не туда (или в `/data`), UI покажет
место мелкого корня CT, а не тома данных (был такой баг: показывало 227 ГиБ корня вместо 19 ТБ диска).

- [ ] Том раздачи смонтирован в `/data/<ENG>` (см. `docker-compose.<ENG>-content.yml`,
      путь вида `/host/storage/<ENG>:/data/<ENG>`) — это и есть `storage_prefix`.
- [ ] **Диск в fstab можно оставить с `nofail`** (машина грузится, даже если диск умер),
      но тогда обязателен `SEEDING_STORAGE_REQUIRE_MOUNT=1`. Иначе после выключения
      света Docker стартует раньше `mount`, bind видит пустой каталог на системном
      диске, libtorrent пишет в fastresume «файлов нет» и качает библиотеку заново
      на SSD. С настройкой `1` entrypoint выходит с ошибкой, `restart: unless-stopped`
      повторяет старт, и контейнер поднимается уже на настоящем диске.
      Проверка: `stat` устройства `/data/<ENG>` и `/data` должен отличаться
      (`scripts/require-mount.sh /data/<ENG> /data`).
- [ ] FTP, WebDAV и прочие контейнеры с bind на тот же каталог хоста — та же гонка.
      Их entrypoint тоже должен выходить с ошибкой, пока `require-mount.sh` не
      успешен. Иначе после включения света в FTP нет каталога раздачи, хотя диск
      уже смонтирован на хосте. Второй аргумент скрипта — bind каталога на
      системном диске хоста (тот же device, что docker-том `/data`), без
      вложенных mount. Корень контейнера (overlay) для этого не годится: его
      device другой, даже когда данные лежат в пустом каталоге на SSD.
- [ ] Внутри контейнера `/data/<ENG>` — отдельная ФС (своя строка в `df`, не та же, что `/data`):

  ```bash
  docker exec <ENG>-seeding df -h /data /data/<ENG>
  # /data — корень CT (мелкий); /data/<ENG> — диск с контентом (терабайты)
  ```

- [ ] Тип диска (`disk_kind`) считается по `/data/<ENG>`, не по `/data`. На 171
      корень — NVMe, контент — HDD: b* должны быть `hdd`, иначе hold хеша не
      включится. a* — `ssd`. Спека: `docs/CREATOR_UPLOAD_HOLD.md`.
      После рестарта (engine ≥ 1.6.16): если в БД нет лимита, `session/stats`
      не должен показывать постоянные `upload_limit`/`desired` = 1048576 при
      `creator_upload_hold=false`.

  ```bash
  docker exec <ENG>-seeding wget -qO- http://127.0.0.1:8081/health
  # disk_kind=hdd|ssd, version≥1.6.16
  ```

- [ ] `sysinfo`/UI («Диск раздачи»/«Путь раздачи») показывают том `/data/<ENG>` и его размер,
      совпадающие с `df` выше (а не размер корня CT):

  ```bash
  curl -s -H "X-API-Key: $KEY" <ORCH>/api/v1/engines/<ENG>/info | \
    python3 -c 'import sys,json;d=json.load(sys.stdin)["sysinfo"];print(d["storage_path"], d["disk_total"], d["disk_free"])'
  ```

- [ ] Порог алерта «мало места»: критично, когда свободно **< 100 ГиБ**
      (`SEEDING_DISK_ALERT_GB`, по умолчанию 100). Процентный порог `SEEDING_DISK_ALERT_PCT`
      выключен (0) — на сидбоксе диски намеренно забиты под завязку, проценты не показательны.

## H. После

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
