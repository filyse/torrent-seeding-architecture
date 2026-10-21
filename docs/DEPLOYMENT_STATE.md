# Состояние деплоя и реконсилиация git ↔ прод

> **Исторический снимок 2026-07-18.** Не читать как текущий прод.
> С тех пор на CT 400 живёт creator (`/api/v1/creator`), upload-edge/relay и download.
> С 2026-10-21: engine 1.6.19 на всех десяти движках, api 1.26.15, web 1.64.4 (§7я7–§7я41; правила вида — [`WEB_UI.md`](WEB_UI.md)).
> Живые версии — [`CHANGELOG.md`](../CHANGELOG.md) (на 2026-10-06: web 1.54.2 / api 1.26.11 / queue 1.0.2 / engine 1.6.16, libtorrent 2.1.2 на всех девяти движках; с 2026-10-16 десятый — c1, §7я4).
> Источник истины кода — `main` (web-шапка — после коммита этой спеки).

Документ фиксирует **фактическую топологию продакшена**, расхождение рабочих
деревьев на живых хостах относительно `origin/main` и порядок приведения git к
нормальному виду с последующим чистым деплоем.

## 1. Топология

| Роль | Хост / доступ | Путь репозитория | Что крутится |
|------|----------------|------------------|--------------|
| Оркестратор (control plane) | CT 400 (Proxmox LXC, `192.168.1.101`), доступ через PVE `192.168.1.10` → `pct exec 400` | `/opt/containerd` | `api`, `queue_worker`, `web`, `db` (postgres), `redis`, `caddy`, стек наблюдаемости (prometheus/grafana/cadvisor/node-exporter) |
| Движки b1–b6 (data plane) | seedbox b-host `rudub@192.168.1.171:24` | `/home/rudub/seeding-engine` | контейнеры `b1-seeding`…`b6-seeding` (по тому на диск), плюс легаси `torrent-api`, `rtorrent-rutorrent`, `ftpd`, `webdav` |
| Движки a1–a3 (data plane) | seedbox a-host `rudub2@192.168.2.243` (LAN2; SSH только через прыжок `-J root@192.168.1.10`) | `/home/rudub2/torrent-seeding-architecture` | контейнеры `a1-seeding`…`a3-seeding` (один compose-проект `seeding-engines-a`) |
| Движок c1 (data plane) | c-сидбокс `rudub3@192.168.1.172` (LAN1, вход по паролю через PVE `192.168.1.10`) | `/home/rudub3/torrent-seeding-architecture` | контейнер `c1-seeding` (compose-проект `seeding-engines-c`) + `seeding-upload-edge` |

Деплой-механизм — не `git pull` в CI, а **сборка docker-образов из локального
чекаута репозитория на самом хосте**:

- CT400: `scripts/deploy-ct400.sh up -d --build` (поднимает base + media + tls + observability оверреи).
- b-host: сборка движков из `engine/` + пер-движковые оверреи `docker-compose.bN-content.yml`.

## 2. Как возникло расхождение

Прод исторически **правился «горячими патчами»** прямо в рабочем дереве хоста
(файлы копировались/редактировались без коммитов, ср. россыпь `*.bak150`,
`*.bak140`, `*.bak141`). Указатель git при этом либо оставался на старом коммите
(b-host: `0513c60`), либо был вручную переставлен на свежий (CT400: `ef97d9c`),
из-за чего `git status` показывал «грязное» дерево, а часть файлов из `origin/main`
числилась как *untracked* или *deleted*, хотя физически присутствовала.

Итог сверки (после игнора пробелов/CRLF — большие «diff» оказались шумом от
переносов строк на Windows-клоне):

- **Контент рабочих деревьев обоих хостов ≈ `origin/main`, но ПОЗАДИ него.**
  `origin/main` — канонический и более полный (в т.ч. фича `creator`, которой на
  проде нет: `routers/creator.py`, `engine/seeding_engine/creator.py`).
- **Единственное реальное «прод впереди git»** — политика `restart: unless-stopped`
  на сервисах оркестратора (`db`, `redis`, `api`, `queue_worker`, `web`) в
  `docker-compose.multi-engine.yml`. Внесено в git этим коммитом.
- Локальные правки движков на b-host (`internal_api.py`, `sysinfo.py`,
  `torrent_runtime.py`, `engine/Dockerfile` BUILD_TIME, `TLS_CA`, версия `1.0.3`)
  **уже присутствуют в `origin/main`** (равны либо перекрыты более полной версией) —
  теряться при выравнивании нечему.

## 3. Что должно сохраняться на хостах (untracked, НЕ в git)

Секреты и хостоспецифичная конфигурация — не трекаются, при выравнивании
сохраняются (их не трогает `git reset --hard`, т.к. их нет в целевом коммите):

- CT400: `.env*`, `certs/`, данные наблюдаемости (в docker-томах, не в репо).
- b-host: `.env.engine`, `.env.engine.b2`…`.b6`, `docker-compose.b1-content.yml`…`b6-content.yml`, `certs/`.

Мусор для удаления: `*.bak`, `*.bak140`, `*.bak141`, `*.bak150`.

## 4. Порядок реконсилиации и деплоя

1. **git = прод** (этот коммит): внести `restart: unless-stopped` в
   `docker-compose.multi-engine.yml`, запушить в `origin/main`.
2. **Бэкап предеплой** на каждом хосте: `git diff origin/main > predeploy.patch`,
   `git status --porcelain > predeploy.status`, архив хост-конфига/секретов.
3. **Выравнивание**: `git fetch origin && git reset --hard origin/main`
   (подтягивает `creator` + restart-политику; untracked-секреты сохраняются),
   затем удалить `*.bak*`.
4. **Пересборка**:
   - CT400: `scripts/deploy-ct400.sh up -d --build`.
   - b-host: пересборка движков b1–b6 из свежего `engine/`.
5. **Проверка здоровья**: `api/v1/health`, наличие эндпоинтов `creator`,
   реестр движков, healthcheck контейнеров.

## 5. Runbook пересборки движков (точные команды)

Движки собираются из локального чекаута `engine/` на своём хосте. Хостоспецифичные
оверреи (`docker-compose.*-content.yml`, `docker-compose.a-host.yml`) и `.env.engine*`
— untracked, их сохраняет `git reset --hard` (в целевом коммите их нет).

**b-host** (`rudub@192.168.1.171:24`, `cd ~/seeding-engine`):

```bash
git fetch origin && git reset --hard origin/main
# b1 — проект без суффикса, .env.engine
docker compose -p seeding-engine    --env-file .env.engine    -f docker-compose.engine.yml -f docker-compose.b1-content.yml up -d --build
# b2..b6 — проект с суффиксом и свой .env
for n in 2 3 4 5 6; do
  docker compose -p seeding-engine-b$n --env-file .env.engine.b$n \
    -f docker-compose.engine.yml -f docker-compose.b$n-content.yml up -d --build
done
```

**a-host** (`rudub2@192.168.2.243` через `-J root@192.168.1.10`, `cd ~/torrent-seeding-architecture`):

```bash
git fetch origin && git reset --hard origin/main
# a1..a3 — один compose-проект, инлайн-env (без --env-file)
docker compose -p seeding-engines-a -f docker-compose.a-host.yml up -d --build
```

**CT400** (`pct exec 400`, `cd /opt/containerd`):

```bash
git fetch origin && git reset --hard origin/main
scripts/deploy-ct400.sh up -d --build
```

Проверка: `curl -s http://127.0.0.1:8000/api/v1/health` (все движки `true`);
на движке — `docker inspect <name> --format '{{.State.Health.Status}}'` = `healthy`,
`docker exec <name> cat /app/BUILD_TIME` = свежая метка.

## 6. Выполнено — 2026-07-18

- Нормализация git: `restart: unless-stopped` внесён в `docker-compose.multi-engine.yml`,
  запушен в `origin/main` (тип `6409faa`). Остальное расхождение — прод был позади git;
  фича `creator` доставлена на все хосты.
- Все три хоста выровнены `git reset --hard origin/main` (untracked-секреты сохранены,
  мусор `*.bak*` удалён), пересобраны и здоровы:
  - CT400: `api` healthy, БД ok, все 9 движков `true`, роуты `/api/v1/creator/*` активны.
  - b1–b6: `healthy`, свежий BUILD_TIME, роуты движка `/internal/v1/creator/*`, `/internal/v1/fs/browse` активны.
  - a1–a3: `healthy`, свежий BUILD_TIME.
- Предеплой-бэкапы: `~/predeploy-*.patch`, `~/predeploy-*.status`,
  `~/predeploy-hostcfg-*.tar.gz` на CT400 и b-host; `~/predeploy-a-host.yml.bak` на a-host.

## 7а. Выкат загрузки в движок — 2026-08-16

Вшивание `/upload/v1` в engine + `upload-edge` вместо Python-sidecar.
Версии: web 1.19.0, api 1.11.0, engine 1.3.0.
Полный runbook: [`UPDATE-UPLOAD-EMBED.md`](UPDATE-UPLOAD-EMBED.md).

**Сделано 2026-08-16** (`3620b5d`): b1–b6 и a1–a3 = engine 1.3.0 + `seeding-upload-edge`;
sidecar снят; CT400 api 1.11.0 / web 1.19.0, `SEEDING_UPLOAD_PER_ENGINE=1`;
health всех 9 движков ok. NPM не меняли.

Кратко на следующий раз: push → секрет в `.env.engine*` → rebuild движков →
`docker stop/rm seeding-upload` → edge `:8090` → на CT400 флаг и
`scripts/deploy-ct400.sh`.

## 7б. Скачивание по WAN — 2026-08-16

web **1.20.0**: чип «Скачивание» → `/network/download`, те же WAN1/WAN2, плюс
скорость очереди «Файл». Только CT400, только `web`.

## 7в. Всего отдано по WAN — 2026-08-16

web **1.21.0**: чип «Всего отдано» → `/network/uploaded`, те же WAN1/WAN2,
`by_engine.total_uploaded`. Только CT400, только `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Движки на 171/243 не трогать. После выката — Ctrl+F5, клик по «Всего отдано».

## 7г. Creator → MPW (`creator.task.deleted`) — 2026-08-23

api **1.13.0**, engine **1.3.1**. Пуш в Kafka при DELETE и при TTL.
Топик `creator.task.deleted`, брокер `SEEDING_KAFKA_BOOTSTRAP` (на CT400
дефолт `192.168.1.223:9092`). Движки новых env не требуют: TTL уходит на
`POST /api/v1/creator/events/deleted` с уже заданным `X-Register-Key`.

```bash
# CT400
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api

# b-host / a-host — как в §5 (пересборка engine)
```

Проверка: `GET /api/v1/health`; в контейнере api есть `SEEDING_KAFKA_BOOTSTRAP`;
`DELETE /api/v1/creator/tasks/{engine}/{id}` → в топике сообщение с `task_key`.

## 7д. Фильтр «Переносящиеся» — 2026-08-24

web **1.23.0**, api **1.14.0**. В «Фильтры → Состояние» пункт для раздач
со статусом `migrating`. Движки не трогать. Только CT400, `api` + `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api web
```

Проверка: Ctrl+F5; в поповере есть «Переносящиеся»;
`GET /api/v1/torrents?state=migrating` и `facets.states.migrating`.

## 7е. Графики отдачи на /network/uploaded — 2026-08-24

web **1.24.0**, api **1.15.0**. Таблица `upload_samples` (alembic 0015 при старте api),
cron воркера раз в 15 мин, `GET /network/uploaded-history`. Движки не трогать.
Только CT400, `api` + `queue_worker` + `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api queue_worker web
```

Проверка: Ctrl+F5 на `/network/uploaded`; сверху график и День/Неделя/Месяц;
`GET /api/v1/network/uploaded-history?period=day`; в логе воркера `upload sample written=`.
Первые столбики — после cron (сразу при старте воркера и далее :00/:15/:30/:45).

## 7ж. Вкладка «История» — 2026-08-24

web **1.26.0**. Большой график «Отдача за период» на `/network/history`;
на «Всего отдано» остались карточки и мини-графики. API не трогаем, движки не трогать.
Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; вкладки Отдача / Скачивание / Всего отдано / История;
`/network/history` — стек WAN1+WAN2 и День/Неделя/Месяц; на `/network/uploaded`
большого графика нет. F5 на `/network/history` не даёт 404.

## 7з. Мини-графики с наведением — 2026-08-24

web **1.27.0**. На «Всего отдано» мини-график канала как большой: ось, подписи,
тултип (интервал, доля, движки). API не трогаем, движки не трогать.
Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5 на `/network/uploaded`; наведение на столбик мини-графика
показывает интервал и движки; пустые корзины штрихом.

## 7и. Графики в отдельной рамке — 2026-08-24

web **1.28.0**. На «Всего отдано» карточки снова без графика; мини-графики
ниже, в отдельной рамке. API не трогаем, движки не трогать. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5 на `/network/uploaded`; в карточке WAN нет столбиков;
под сеткой рамка «Отдача за период».

## 7к. Шапка + графики на Отдаче/Скачивании — 2026-08-24

web **1.29.0**, api **1.17.0**. Профиль не переносится на «Скачивании».
Мини-графики под карточками на Отдаче и Скачивании; колонка `downloaded`
(alembic 0016). Движки не трогать. CT400: `api` + `queue_worker` + `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api queue_worker web
```

Проверка: Ctrl+F5; на `/network/download` кабинет в одной строке с вкладками;
под карточками рамка графиков. `GET /api/v1/network/uploaded-history?metric=downloaded`.

## 7л. Чипы флагов пиров — 2026-08-24

web **1.30.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; деталь раздачи → Пиры: чипы вместо простыни флагов.

## 7м. Пиры простым языком — 2026-08-24

web **1.31.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; деталь → Пиры: нет слов choke/BEP-10.

## 7н. Смысл флагов пиров — 2026-08-24

web **1.32.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; деталь → Пиры: при ↑ > 0 нет чипа «очередь»; одно слово на чип.

## 7о. Короткие источники пиров — 2026-08-24

web **1.33.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; деталь → Пиры: «трекер · вошёл», адрес без переноса порта.

## 7п. Свой пароль и русские роли — 2026-08-24

web **1.34.0**, api **1.18.0**. CT400, `api` + `web` (движки не трогать):

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api web
```

Проверка: Ctrl+F5; профиль → «Сменить пароль»; в Пользователях роли по-русски.

## 7р. Цвета WAN в карточках — 2026-08-24

web **1.35.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5 `/network/uploaded` — полоски WAN2 зелёные; подписи мини-графиков без цвета.

## 7с. Тултип мини-графика целиком — 2026-08-24

web **1.36.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5 `/network/uploaded` — наведение на столбик сразу показывает полную карточку.

## 7т. Без дубля «Аккаунт» — 2026-08-24

web **1.37.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5; Настройки → нет панели «Аккаунт»; «Пользователи» только у admin.

## 7у. Кабинет `/cabinet` — 2026-08-25

web **1.38.0**, api **1.19.0**. CT400, `api` + `web` (движки не трогать):

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api web
```

Проверка: Ctrl+F5; профиль → «Кабинет»; `/cabinet` после F5 не 404;
Настройки → Информация без темы. Откат: `docs/CABINET.md`.

## 7ф. Кабинет без шапочного профиля — 2026-08-25

web **1.39.0**. Только CT400, `web`:

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5 `/cabinet` — справа в шапке нет аватара.

## 7х. Раздача сразу многим — 2026-08-30

api **1.20.0** (на CT400 бамп от 1.19.0), engine **1.4.0**, web **1.40.0**
затем **1.41.0** (стиль панели). Спека: [`UNCHOKE.md`](UNCHOKE.md).

Прод-деревья длиннее `origin/main` — **не** `git reset --hard`.
Код фичи вшит точечно; политика в БД: 32 / `round_robin`.

```bash
# только web после правки панели (CT400)
cd /opt/containerd
bash scripts/deploy-ct400.sh up -d --build web
```

Движки уже 1.4.0. Откат политики — кнопка в UI, без пересборки.

Проверка: Ctrl+F5; Настройки → Лимиты → поля в одну строку с
«Загрузка файлов»; `GET /api/v1/settings/unchoke`.

## 7ц. Hold отдачи на хеше HDD — 2026-09-02

engine **1.6.0**, api **1.23.0**, web **1.44.0**.
Спека: [`CREATOR_UPLOAD_HOLD.md`](CREATOR_UPLOAD_HOLD.md).

Один образ engine, тип диска движок читает сам (`storage_path()`, не `/data`).
Compose и `.env.engine*` не размечать. Прод-деревья длиннее `origin/main` —
**не** `git reset --hard`. Подтянуть файлы фичи и пересобрать.

Порядок: **171 → 243 → CT400 api+web+queue_worker**. Воркер не `down`, только
пересборка: вместе с hold уходит фикс дельты графиков (провал ≠ отдача).

```bash
# b-host rudub@192.168.1.171:24
cd ~/seeding-engine
# подтянуть engine 1.6.0 (fetch + точечные файлы, не hard reset)
docker compose -p seeding-engine --env-file .env.engine \
  -f docker-compose.engine.yml -f docker-compose.b1-content.yml up -d --build
for n in 2 3 4 5 6; do
  docker compose -p seeding-engine-b$n --env-file .env.engine.b$n \
    -f docker-compose.engine.yml -f docker-compose.b$n-content.yml up -d --build
done

# a-host rudub2@192.168.2.243 через -J root@192.168.1.10
cd ~/torrent-seeding-architecture
docker compose -p seeding-engines-a -f docker-compose.a-host.yml up -d --build

# CT400
cd /opt/containerd
bash scripts/deploy-ct400.sh up -d --build api web queue_worker
```

Проверка: health b* `disk_kind=hdd` version `1.6.0`; a* `disk_kind=ssd`;
создание на b* — чип в очереди; на «Сети» шкала как раньше; a* без капа.
История: провал счётчика не рисует десятки ТБ за день.

## 7ш. Hold отдачи на recheck HDD — 2026-09-05

engine **1.6.1**. Спека: [`CREATOR_UPLOAD_HOLD.md`](CREATOR_UPLOAD_HOLD.md).

Тот же кап, что на создании: b* режутся на время `checking` / `force_recheck`
после переноса. Compose и `.env` не трогаем. Прод-деревья длиннее `origin/main` —
**не** `git reset --hard`.

Порядок: **171 → 243**. api/web/queue на CT400 не трогаем. Воркер не `down`.

```bash
# b-host rudub@192.168.1.171:24
cd ~/seeding-engine
# подтянуть engine 1.6.1 (fetch + точечные файлы, не hard reset)
docker compose -p seeding-engine --env-file .env.engine \
  -f docker-compose.engine.yml -f docker-compose.b1-content.yml up -d --build
for n in 2 3 4 5 6; do
  docker compose -p seeding-engine-b$n --env-file .env.engine.b$n \
    -f docker-compose.engine.yml -f docker-compose.b$n-content.yml up -d --build
done

# a-host rudub2@192.168.2.243 через -J root@192.168.1.10
cd ~/torrent-seeding-architecture
docker compose -p seeding-engines-a -f docker-compose.a-host.yml up -d --build
```

Проверка: health b* `disk_kind=hdd` version `1.6.1`; a* `disk_kind=ssd`;
перенос на b* — на «Сети» «хеш», отдача ~1 МБ/с до конца checking.

## 7ч. Аватары в списке пользователей — 2026-09-02

web **1.45.0**, api **1.24.0**. Только CT400, `api` + `web`.
Прод-деревья длиннее `origin/main` — **не** `git reset --hard`.

```bash
cd /opt/containerd
# подтянуть api/seeding_api/routers/auth.py, api/seeding_api/__init__.py,
# web/src/{main.ts,style.css,version.ts}
bash scripts/deploy-ct400.sh up -d --build api web
```

Проверка: Ctrl+F5; Настройки → Пользователи — кружок слева от имени.
Ключи без аватара. Смена по-прежнему в кабинете.

## 7щ. Скачать `.torrent` раздачи — 2026-09-08

web **1.46.0**, api **1.25.0**, engine **1.6.2**. Спека: [`TORRENT_FILE.md`](TORRENT_FILE.md).
Коммит `ce91e5c`.

Кнопка в карточке рядом с «Переанонс». Байты идут через CT400 `api`
(`GET /api/v1/torrents/{id}/torrent-file`), не через `/u/b/`.

**Сделано 2026-09-08:**
- CT400: точечный checkout `origin/main` (дерево грязное, не `reset --hard`) +
  `deploy-ct400.sh up -d --build api web`. api 1.25.0, web 1.46.0, health 9/9.
- 171 и 243 были на `007c5c1`, dirty, 24 коммита позади. Бэкап
  `~/predeploy-engines-*` (diff/status/hostcfg). Конфликтные untracked
  `download_http.py` / `unchoke.py` убраны в бэкап, затем
  `git reset --hard origin/main` → `ce91e5c`. Секреты и оверреи живы
  (`.env.engine*`, `docker-compose.bN-content.yml`, `docker-compose.a-host.yml`).
- 171: b1–b6 по одному, engine **1.6.2** `BUILD_TIME=2026-09-07T21:20:28Z`, healthy.
- 243: a1–a3 одним compose, **1.6.2** `2026-09-07T21:20:31Z`, healthy.
- Повторный health CT400: все 9 движков `true`.

## 7ы. Плашка статистики в шапке — 2026-09-08

web **1.49.6.3**. Спека: [`HEADER_STATS.md`](HEADER_STATS.md). API/движки не трогаем.

CT400: точечный `pct push` `web/src/{main.ts,style.css,version.ts}` +
`deploy-ct400.sh up -d --build web`. Проверка: Ctrl+F5, в футере 1.49.6.3;
в покое только «Обновлено», выезд влево, строки ↓/↑/отдано → WAN-карточки.

## 7э. Статус «Проверка» — 2026-09-29

web **1.53.85**, api **1.26.7**. Спека: [`TORRENT_STATUS.md`](TORRENT_STATUS.md).
Движки не трогать: состояние `checking_files` уже отдаёт libtorrent, подпись
пишет API и воркер. `queue_worker` тоже вызывает `status_from_runtime`, его
пересобрать вместе с api, иначе он затрёт `checking` обратно в `downloading`.

```bash
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api web queue_worker
```

Проверка: Ctrl+F5, в футере 1.53.85; фильтр статусов содержит «Проверка»;
`GET /api/v1/health` — api 1.26.7.

## 7ю. Очередь проверки после рестарта — 2026-09-29

engine **1.6.8**, web **1.53.86**. API не менялся: лимит тот же
`hash_per_engine`. Спека: [`WORK_QUEUES.md`](WORK_QUEUES.md).

Без пересборки движков волна `checking_files` снова читает диск пачкой.
CT400 — только `web`. Движки b1–b6 и a1–a3 — по §5, образ engine 1.6.8.

```bash
# CT400
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build web
```

Проверка: Ctrl+F5, в футере 1.53.86; на движке `/app` версия 1.6.8;
сразу после рестарта одновременно не больше лимита проверок, остальные
в снимке `queued_for_checking` («Ждёт проверки»).

## 7я. Скорость проверки — 2026-09-29

web **1.53.89**, api **1.26.10**, engine **1.6.9**. Спека: [`TORRENT_STATUS.md`](TORRENT_STATUS.md).
Колонка `torrents.check_rate` (alembic 0019 при старте api).

Движки пересобрать: скорость считается в процессе движка по
`piece_finished_alert`. CT400 — `api` + `web`.

```bash
# CT400
cd /opt/containerd
git fetch origin && git reset --hard origin/main
bash scripts/deploy-ct400.sh up -d --build api web

# b-host и a-host — как в §5
```

Проверка: Ctrl+F5, футер 1.53.89; у раздачи в статусе «Проверка» рядом с
процентом. У раздачи в статусе «Проверка» скорость рядом с бейджем.

## 7э. Hold не залипает в session.state — 2026-10-06

engine **1.6.16**, web **1.54.2** (модалка удаления: перенос длинных имён).
Спека: [`CREATOR_UPLOAD_HOLD.md`](CREATOR_UPLOAD_HOLD.md) § session.state.
Также: сеть `seeding-upload` вшита в `docker-compose.engine.yml` /
`deploy-engine.sh` (скачивание без 502 после recreate).

Симптом до фикса: b1/b4/b6 `upload_limit=1048576`, `creator_upload_hold=false`,
БД `upload_limit NULL`. Снято live `POST …/session/limits` → 0; чтобы не
повторилось после рестарта во время хеша — выкат 1.6.16.

Порядок: **push → 171 → 243 → CT400 web** (api/queue не трогаем).

```bash
# b-host rudub@192.168.1.171:24
cd ~/seeding-engine
git fetch origin && git pull --ff-only origin main
docker network create seeding-upload >/dev/null 2>&1 || true
docker compose -p seeding-engine --env-file .env.engine \
  -f docker-compose.engine.yml -f docker-compose.b1-content.yml up -d --build
for n in 2 3 4 5 6; do
  docker compose -p seeding-engine-b$n --env-file .env.engine.b$n \
    -f docker-compose.engine.yml -f docker-compose.b$n-content.yml up -d --build
done
# проверка: version 1.6.16, upload_limit_desired 0 (если в БД нет лимита)

# a-host rudub2@192.168.2.243 через -J root@192.168.1.10
cd ~/torrent-seeding-architecture
git fetch origin && git pull --ff-only origin main
docker network create seeding-upload >/dev/null 2>&1 || true
docker compose -p seeding-engines-a -f docker-compose.a-host.yml up -d --build

# CT400 — только web (модалка)
cd /opt/containerd
git fetch origin && git pull --ff-only origin main
bash scripts/deploy-ct400.sh up -d --no-deps --build web
```

## 7ю. Фикс SIGSEGV алертов — engine 1.6.17 — 2026-10-11

Ветка `main`, libtorrent 2.1.2. Причина и фикс — CHANGELOG engine 1.6.17,
спека — [`TORRENT_STATUS.md`](TORRENT_STATUS.md) «Алерты не хранить».
Бэкапы до выката: `~/backups/20261011-pre-segfix/` на 171 и 243 (образы,
inspect, тома состояния без медиа, код), CT400 `/root/backups/20261011-pre-segfix/`
+ vzdump CT400; образы a-host помечены `seeding-engine-a:pre-segfix-aN`.

Порядок: a2 (канарейка) → наблюдение → a1, a3 → b1–b6. CT400 не трогаем.
Проверка: `version` 1.6.17 в `/internal/v1/session/stats`, `RestartCount`
не растёт, в `journalctl -k` нет `segfault ... libtorrent`.

## 7я. Прод на ветке hotfix 11.10 — что из неё уже есть в main

Прод 11.10 живёт на `hotfix/1.6.13-hold-stick` (libtorrent 2.0.11). Всё, что
выкачено туда за 11.10, есть в main как отдельные коммиты:

| hotfix | main | что |
|---|---|---|
| `b04f11b` engine 1.6.13.1 | `3938b84` engine 1.6.16 | hold не залипает на 1 MB/s |
| `9fc01a2` engine 1.6.13.2 | `f4188f9` engine 1.6.17 | алерты не живут после `pop_alerts` (SIGSEGV) |
| `9adf3cd` engine 1.6.13.3 | `6df03dd` engine 1.6.17 | recheck/pause не трогают удалённую раздачу |
| `4dc622d` api/queue | `296d88c` (был в main) | queue_worker не затирает `migrating` |
| `e68502d` api 1.26.10.1 / web 1.53.93 | `dbf5d98` api 1.26.12 / web 1.54.3 | очередь переноса `migrate_queued` |
| `bb606d6` api 1.26.10.2 / web 1.53.94 | этот коммит, api 1.26.13 / web 1.54.4 | «Движок недоступен», «Пауза…/Запуск…/Проверка…» |

Записи выката (время по движкам, бэкапы, откат CT400) — в `DEPLOYMENT_STATE.md`
ветки hotfix, §7ю–7я2. При переходе прода на main: движки → engine 1.6.17 на
libtorrent 2.1.2, CT400 → main (api 1.26.13 / web 1.54.4 / queue 1.0.3).

## 7я3. Прод переведён на main — libtorrent 2.1.2 — 2026-10-12

Ветка прода теперь `main` (`8fab5e7`); `hotfix/1.6.13-hold-stick` — только для отката.

| компонент | было (hotfix) | стало (main) |
|---|---|---|
| a1–a3, b1–b6 | engine 1.6.13.3 / libtorrent 2.0.11 | engine 1.6.17 / libtorrent 2.1.2 |
| CT400 api / queue / web | 1.26.10.2 / 1.0.0.1 / 1.53.95 | 1.26.13 / 1.0.3 / 1.54.5 |

Выкат 2026-10-12 (Томск, UTC+7):
- перед выкатом переносов не было (`migration_jobs` пусто), у b6 ~73 ГБ свободно —
  новых переносов на b6 не ставить;
- стресс в образе 2.1.2 (`tests/lt_alert_stress.py fixed 30`,
  `tests/lt_engine_stress.py 30`) — оба `survived`;
- a2 канарейка 15:27, 15 мин без рестартов/segfault, синк CT400 в норме;
- a1, a3 ~15:43; b1 15:44, b2 15:45, b3 15:46, b4 15:47, b5 15:48, b6 15:49 —
  по одному, каждый healthy, `RestartCount=0`;
- CT400 ~15:52: `/opt/containerd` → ветка `main`, пересобраны api, queue_worker, web.
- ~15:51 все 9 healthy, `RestartCount=0`, segfault нет, синк по всем 9 движкам ok.
  После рестарта движки перепроверяют файлы: ~2500 раздач «Ждёт проверки», 10
  «Проверка» — очередь хеша (`hash_per_engine`), уходит сама.

Сборка движков: на rudub2 `docker build -f engine/Dockerfile -t seeding-engine-a:212 .`
→ тег `seeding-engine-a:latest`, `docker compose -f docker-compose.a-host.yml up -d
--no-deps --no-build aN`. На 192.168.1.171 один образ `seeding-engine-b:212` с тегом
`seeding-engine-engine:latest` (b1) и `seeding-engine-bN-engine:latest`, затем
`docker compose -p <project> --env-file .env.engine[.bN] -f docker-compose.engine.yml
-f docker-compose.bN-content.yml up -d --no-deps --no-build engine`.

Бэкапы до выката (`20261012-pre-212`):
- rudub2 `~/backups/20261012-pre-212/`: compose/.env, `git-head.txt`, inspect, тома
  `seeding-engines-a_aN_data`; образ `seeding-engine-a:pre-212`;
- 192.168.1.171 `~/backups/20261012-pre-212/`: то же, тома `*engine_data`; образы
  `seeding-engine-engine:pre-212`, `seeding-engine-bN-engine:pre-212`;
- CT400 `/root/backups/20261012-pre-212/`: `seeding-db.dump`, `containerd-git.tgz`,
  `.env`, compose; образы `containerd-{api,queue_worker,web}:pre-212`.

Откат одного движка: `docker tag <образ>:pre-212 <образ>:latest` и тот же `up -d
--no-deps --no-build`. Откат CT400: `git checkout hotfix/1.6.13-hold-stick`,
`docker tag containerd-<svc>:pre-212 containerd-<svc>:latest`, `up -d --no-deps`
для трёх сервисов. Схема БД не менялась.

Выкат 1.6.13.3 на hotfix 2026-10-11: a2 08:03 (после канарейки 1.6.13.2), a1 ~08:19,
a3 сразу после, b1–b6 по одному 08:21–08:26. В host-only `docker-compose.a-host.yml`
у a3 тег `seeding-engine-a:2.1.2` (на деле старый 1.6.13) заменён на `:latest`,
оригинал — `docker-compose.a-host.yml.bak-pre-segfix`.

## 7я4. Новый движок c1 на rudub3 — 2026-10-16

Хост `rudub3` (`192.168.1.172`, DHCP-резерв на R1 по MAC), Ubuntu 26.04.1, i5-8600,
14 ГБ RAM. Диск раздачи — Seagate Exos 20 ТБ (`ST20000NM007D`), ext4, метка `c1`,
fstab по UUID `defaults,nofail 0 2` → `/home/rudub3/storage/c1` → в контейнере `/data/c1`
(`disk_kind=hdd`).

| что | значение |
|---|---|
| движок | `c1`, engine 1.6.17 / libtorrent 2.1.2 (тот же образ, что у a*, `built_at 2026-10-12T08:25:05Z`) |
| образ | `seeding-engine-c:latest` (= `seeding-engine-c:212`), перенесён `docker save` с rudub2, не пересобирался |
| API | `https://192.168.1.172:8081` (TLS, cert `engine-c1` от seeding-ca, SAN `IP:192.168.1.172`) |
| BT-порт | `50021` tcp+udp (a* = 50011–50013, b* = 50001–50006) |
| upload-edge | `:8090` (`docker-compose.upload-edge.yml`), `/c1/health` отвечает |
| Docker | docker-ce 29.4.0, compose 5.1.3 из репо `noble` (для `resolute` пакетов нет), `apt-mark hold` |
| выход в интернет | WAN1 через R1 (`88.204.56.176`), не WAN2 как у a* |
| проброс | R1 `seeding_c1_172_wan1`: WAN1 50021 tcp+udp → `192.168.1.172:50021`, reflection |

Host-only файлы (untracked): `docker-compose.c-host.yml`, `.env.engine.c` (секреты, 600),
`certs/`. Cert c1 также лежит на CT400 `/opt/containerd/certs/engine-c.{crt,key}`.

Запуск / пересоздание:

```bash
cd ~/torrent-seeding-architecture
sudo docker compose -p seeding-engines-c --env-file .env.engine.c \
  -f docker-compose.c-host.yml up -d --no-build
```

Обновление образа: как у a* — собрать (`docker build -f engine/Dockerfile -t seeding-engine-c:<tag> .`)
или перенести образ, `docker tag … seeding-engine-c:latest`, затем команда выше.

Проверено 2026-10-16: `c1-seeding` healthy, `RestartCount=0`; libtorrent слушает 50021
tcp/udp (v4+v6); CT400 `/api/v1/health` — `c1: true`, оркестратор опрашивает движок;
Prometheus собирает `seeding_engine_*{engine="c1"}`; пакеты с WAN1 на 50021 доходят
через DNAT. Раздач на c1 пока нет.

Загрузка браузером (2026-10-16): на CT400 в `SEEDING_UPLOAD_BASE_URLS` добавлен
`"c1":"https://seedbox2.hw-s.ru/u/c"`, api пересоздан
(`scripts/deploy-ct400.sh up -d --no-deps --no-build api`, бэкап `/root/env.bak-20261016-c1`).
В NPM (CT105, `192.168.1.181`, proxy host 11 `seedbox2.hw-s.ru`) в advanced config
добавлен `location /u/c/` → `http://192.168.1.172:8090`, такой же, как `/u/a/`, `/u/b/`.
Правка внесена и в БД NPM, и в `11.conf`. Бэкапы:
`/data/database.sqlite.bak.20261016-c1`, `/root/11.conf.bak.20261016-c1`.
`https://seedbox2.hw-s.ru/u/c/c1/health` отвечает health движка.
Relay на RU VDS (`185.185.143.207`, systemd `seeding-upload-relay`, `/opt/seeding-upload-relay/relay.env`):
в `UPLOAD_RELAY_UPSTREAMS` добавлен контур `"c":"https://seedbox2.hw-s.ru/u/c"` (бэкап
`relay.env.bak-20261016-c1`), на CT400 в `SEEDING_UPLOAD_RELAY_BASE_URLS` —
`"c1":"https://185-185-143-207.sslip.io:8445/u/c"` (бэкап `/root/env.bak-20261016-c1-relay`).
Relay ходит к движку через NPM seedbox2, проброс на R1 для этого не нужен.
`https://185-185-143-207.sslip.io:8445/u/c/c1/health` отвечает health c1.

Права и файрвол — как на rudub2 (.243): `~/storage` 775, `certs/*.crt` и compose 664,
`engine.key` 600, пользователь в группе `docker`, своих правил sudo нет.
Отличие: секреты в `.env.engine.c` (600), а не в compose. ufw выключен, как на .243.
Подготовленный набор правил (SSH из LAN1; 8081 только с CT400, .171 и .243; 8090 только с
NPM .181; 50021 отовсюду; блок `DOCKER-USER`, потому что Docker обходит INPUT ufw)
сохранён в `/root/ufw.c1-enabled-20261016.tgz`, `/root/after*.rules.c1-docker-user`.
Скачивание (`/{engine}/download/v1/file`) идёт по тем же базам, что и заливка: NPM `/u/c/` и
relay `…:8445/u/c/`. Отдельной настройки для c1 не нужно. Проверено 2026-10-16: с
неверным ticket оба пути отвечают `403 malformed ticket` от движка, так же как у a1.

FTP на rudub3 (2026-10-16), копия `ftpd` с .243: `~/server/docker-compose.yml`
(проект `server`), `stilliard/pure-ftpd`, порт 21, пассивные 4660–4760, `PUBLICHOST=192.168.1.172`,
корень `/home/rudub3/storage`, флаги `-U 133:022 -H`. Пользователь puredb `rudub3`
(uid/gid 1000) с паролем системного пользователя. Хеш SHA-512 crypt лежит в
`~/server/ftp-passwd/pureftpd.passwd` (root, 600), `FTP_USER_PASS` не задан.
Из интернета: `88.204.56.176:2121`. На R1 проброшены WAN1 2121 → .172:21 и 4660–4760 → .172
с reflection, так же, как у .171 (26 → 21, 4559–4659). `PUBLICHOST` — LAN-адрес, как у .171:
клиенты снаружи берут адрес сервера вместо PASV-IP или используют EPSV.

Отличия rudub3 от rudub2, оставленные сознательно: нет `sb-cpu-powercap`
(ограничение частоты CPU под тихий кулер .243), нет `sysstat`, chrony вместо
timesyncd (дефолт 26.04), ufw выключен, как на .243. Действующие sysctl
(`vm.max_map_count`, `fq_codel`, `rp_filter=2`, буферы) и `daemon.json` (нет на обоих) совпадают.

## 7я5. Легаси ruTorrent остановлен на обоих сидбоксах — 2026-10-16

На 192.168.2.243 и 192.168.1.171 остановлены `torrent-api`, `rtorrent-rutorrent`,
`rtorrent-rutorrent-logs` (`docker update --restart=no` + `docker stop`). Перед этим:
у rtorrent 0 соединений с пирами, у `torrent-api` 0 запросов за 7 дней (на `:8000` висели только
сканеры из интернета). CT400 и MPW (`backend: seeding`) их не используют.
После каждого стопа проверялись: health CT400 (все 10 движков true), `seedbox2.hw-s.ru`,
`/u/a/a1`, `/u/b/b1`, FTP .171 и .243 — без изменений.

`seedbox.hw-s.ru` (NPM на .243, proxy host 4) теперь отдаёт 301 на
`https://seedbox2.hw-s.ru$request_uri`, кроме `/.well-known/acme-challenge/` (чтобы
продлевался сертификат). Правка в БД NPM (`advanced_config`) и в `4.conf`.
NPM host 3 (`npmsb.hw-s.ru`) не трогали.

Бэкапы: `~/backups/20261016-legacy-stop/` на обоих хостах (compose, `api/`, rtorrent
`/data` + passwd, `.session`, NPM `/data` + letsencrypt, дамп БД NPM, inspect, образы
`docker save`); копии `.session` на ПК: `C:\Scripts\Project\Backup\legacy-rutorrent-20261016\`.

Откат (на каждом хосте):

```bash
for c in rtorrent-rutorrent rtorrent-rutorrent-logs torrent-api; do
  docker update --restart=unless-stopped $c && docker start $c
done
# .243, вернуть ruTorrent на seedbox.hw-s.ru: advanced_config host 4 = '' в БД NPM,
# cp /data/nginx/disabled-20261016/4.conf /data/nginx/proxy_host/4.conf, nginx -s reload
```

Удаление контейнеров запланировано через неделю (~23.10). Не тронуты: FTP, движки,
данные, DNS, пробросы на роутерах (WAN 8000/50000 на .171/.243 остаются), конфиги CT400/MPW.
## 7я6. web 1.54.6 — строки выпадающих списков не сжимаются — 2026-10-21

Только CT400, только `web`: `git fetch origin && git reset --hard origin/main`,
`bash scripts/deploy-ct400.sh up -d --build web`. Бэкап образа —
`containerd-web:pre-1546`. Откат: `docker tag containerd-web:pre-1546
containerd-web:latest` и `up -d --no-deps --no-build web`. Ветка hotfix не
трогается (прод на main, §7я3).

## 7я7. ZIP всей раздачи — engine 1.6.19 / api 1.26.14 / web 1.55.0 — 2026-10-21

Спека — `FILE_DOWNLOAD.md` (врезка сверху). Edge и RU-релей не менялись
(`/{engine}/download/v1/` уже общий).

Выкат (Томск, UTC+7):
- 00:51 a2 канарейка engine 1.6.18, затем a1, a3, b1–b6 по одному (~00:55), все
  healthy, `RestartCount=0`;
- ~01:00 CT400 `deploy-ct400.sh up -d --build api web` (api 1.26.14, web 1.55.0);
- живой тест через `seedbox2.hw-s.ru/u/a/a2/…/zip`: раздача 3 файла 1.69 ГБ, 200,
  `Content-Length` = размер, ~60 МБ/с, `zipfile.testzip()` чисто; через RU-релей
  тот же ответ (~8 МБ/с, `Content-Length` релей не передаёт — прогресс без общего
  размера);
- найдено: после обрыва клиента слот ZIP не освобождался (429). Фикс engine 1.6.19
  (`35c5005`): ~01:12 a1, проверено 4 обрыва подряд + повтор — 200; ~01:25 a2, a3,
  b1–b6, все healthy, `RestartCount=0`.
- ~01:33 c1 (rudub3, .172; вход по паролю из `seedbox.md`, docker без sudo):
  `seeding-engine-c:pre-1618` ← старый latest, `git reset --hard origin/main`
  (`201bb8e`), сборка `seeding-engine-c:1619` → `latest`, `docker compose -p
  seeding-engines-c --env-file .env.engine.c -f docker-compose.c-host.yml up -d
  --no-build`; c1 healthy 1.6.19, `RestartCount=0`; ftpd и upload-edge не
  трогались, порты 21/50021/8081/8090 слушают. Живой ZIP через
  `seedbox2.hw-s.ru/u/c/c1/…/zip`: 8 файлов, 5.9 ГБ (ZIP64), 200, `Content-Length`
  совпал, ~39 МБ/с, `testzip()` чисто.

Бэкапы образов: a-host `seeding-engine-a:pre-1618`; c-host `seeding-engine-c:pre-1618`; b-host
`seeding-engine-engine:pre-1618`, `seeding-engine-bN-engine:pre-1618`; CT400
`containerd-api:pre-zip`, `containerd-web:pre-zip`. Схема БД не менялась.

Откат: быстрый — `SEEDING_DOWNLOAD_ENABLED=0` на api (выключит и файлы, и ZIP).
Полный: движок — `docker tag <образ>:pre-1618 <образ>:latest` и тот же
`up -d --no-deps --no-build`; CT400 — `docker tag containerd-{api,web}:pre-zip
containerd-{api,web}:latest`, `scripts/deploy-ct400.sh up -d --no-deps --no-build api web`.

## 7я8. Меню «Напрямую / Через RU-VDS» — web 1.55.3 — 2026-10-21

Только CT400, только `web`. ~01:52 web 1.55.1 (`bf5c143`: одна кнопка «Скачать» с
меню, «Скачать всё (ZIP)» над файлами убрана), ~01:55 1.55.2 (`b958799`, подписи),
~02:00 1.55.3 (`203e3ac`, финальная подпись RU). Подписи: «Напрямую» — «с движка»,
«Через RU-VDS» — «С удаленного прокси сервера».

Проверка: `seedbox2.hw-s.ru` 200, в бандле 1.55.3 и обе подписи. Маршруты (a1,
раздача 3 файла): ZIP напрямую 200 + `Content-Length`, ZIP через RU 200, файл
напрямую 200 + `Content-Length`, файл через RU 200; все обрывались через 3 с.

Бэкап: `containerd-web:pre-1551` (web 1.55.0). Откат: `docker tag
containerd-web:pre-1551 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я9. web 1.55.4 — кнопки таблицы файлов одного размера — 2026-10-21

Только CT400, только `web`, только CSS. Бэкап `containerd-web:pre-1554` (web 1.55.3).
Откат: `docker tag containerd-web:pre-1554 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я10. web 1.55.5 — шеврон у «Приоритета» в таблице файлов — 2026-10-21

Только CT400, только `web`, только CSS. Бэкап `containerd-web:pre-1555` (web 1.55.4).
Откат: `docker tag containerd-web:pre-1555 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я11. web 1.55.6 — «Приоритет» уже — 2026-10-21

Только CT400, только `web`, только CSS. Бэкап `containerd-web:pre-1556` (web 1.55.5).
Откат: `docker tag containerd-web:pre-1556 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я12. web 1.55.7 — пейджер списка в стиле таблицы файлов — 2026-10-21

Только CT400, только `web`, только CSS. Бэкап `containerd-web:pre-1557` (web 1.55.6).
Откат: `docker tag containerd-web:pre-1557 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я13. web 1.55.8 — «N / стр.» как кнопки пейджера — 2026-10-21

Только CT400, только `web`, только CSS. Бэкап `containerd-web:pre-1558` (web 1.55.7).
Откат: `docker tag containerd-web:pre-1558 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я14. api 1.26.15 / web 1.55.9 — 25/50/100/250 на страницу — 2026-10-21

CT400: `api` и `web`. Бэкапы `containerd-api:pre-12615` (api 1.26.14),
`containerd-web:pre-1559` (web 1.55.8). Откат: `docker tag containerd-api:pre-12615
containerd-api:latest`, `docker tag containerd-web:pre-1559 containerd-web:latest`,
`scripts/deploy-ct400.sh up -d --no-deps --no-build api web`.

## 7я15. web 1.56.0 — фон входа «потоки данных» + кэш-заголовки — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1560`.
- `web/src/loginBg.ts` → `loginStreams()` (canvas), старый `loginPaths` удалён.
- `web/nginx.conf`: `no-cache` для index/SPA, `immutable` год для `/assets/`.
- Откат: `docker tag containerd-web:pre-1560 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я16. web 1.57.0 — фон списка «пятна + сетка точек», стеклянные карточки — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1570`.
- `main.ts`: `ensurePageMesh()` + класс `body.page-mesh-on` только на экране списка; стили — в конце `style.css`.
- Откат: `docker tag containerd-web:pre-1570 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я17. web 1.57.1 — фон на всех экранах после входа + чёткие разделители — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1571`.
- Откат: `docker tag containerd-web:pre-1571 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я18. web 1.57.2 — градиентные разделители, бледнее пятна, рамки как в 1.56.0 — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1572`.
- Откат: `docker tag containerd-web:pre-1572 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я19. web 1.57.3 — пятна фона меньше — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1573`.
- Откат: `docker tag containerd-web:pre-1573 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я20. web 1.58.0 — единый стиль контролов в настройках и кабинете — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1580`.
- Откат: `docker tag containerd-web:pre-1580 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я22. Откат web 1.59.0 → 1.58.0 — 2026-10-21

- Причина: «История» зависала в скелетоне после автообновления; анимация графика неудачная.
- CT400: `docker tag containerd-web:pre-1590 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`. Образ 1.59.0 сохранён как `containerd-web:bad-1590`.
- `main`: коммит 1.59.0 ревертнут отдельным revert-коммитом (без force-push); запись §7я21 и строка CHANGELOG 1.59.0 ушли вместе с ревертом.

## 7я23. web 1.59.1 — «История»: переключатель как в шапке, живой график без зависаний — 2026-10-21

- CT400: пересобран только `web` (API без изменений). Бэкап образа: `containerd-web:pre-1591` (= 1.58.0).
- Проверено в стенде: неизменные данные, медленный ответ 2.5 с, 502 через раз, меняющиеся данные (по 10+ циклов), быстрое переключение периодов при запросах в полёте, 3 мин с реальным интервалом 30 с.
- Откат: `docker tag containerd-web:pre-1591 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я24. web 1.59.2 — «История»: рост столбиков при загрузке и смене периода — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1592` (= 1.59.1).
- Стенд: 4 сценария (неизменные данные, 2.5 с, 502 через раз, меняющиеся) + быстрое переключение периодов — без зависаний.
- Откат: `docker tag containerd-web:pre-1592 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я25. web 1.59.3 — «История»: одновременный плавный рост столбиков — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1593` (= 1.59.2).
- Стенд: 4 сценария + быстрое переключение периодов — без зависаний.
- Откат: `docker tag containerd-web:pre-1593 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я26. web 1.60.0 — «История»: столбики из «квадратиков» — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1600` (= 1.59.3).
- Стенд: 5 сценариев (неизменные, 2.5 с, 502 через раз, меняющиеся, быстрое переключение) + ~3 мин с реальным интервалом 30 с — без зависаний.
- Откат: `docker tag containerd-web:pre-1600 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я27. web 1.60.1 — «История»: чёткие «квадратики» — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1601` (= 1.60.0).
- Стенд: 5 сценариев зависаний — без зависаний; кропы DPR 1 / 1.25 / 2 — края клеток на целых физических пикселях.
- Откат: `docker tag containerd-web:pre-1601 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я28. web 1.61.0 — все графики «квадратиками» (общий cellChart.ts) — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1610` (= 1.60.1).
- Стенд: сценарии зависаний на «Истории» и «Всего отдано» — без зависаний.
- Откат: `docker tag containerd-web:pre-1610 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я29. web 1.62.0 — кнопка «‹ К списку раздач» вместо ссылки — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1620` (= 1.61.0).
- Проверено вживую: карточка раздачи, «Сеть», «Ферма», «Кабинет», «Настройки» (десктоп и телефон).
- Откат: `docker tag containerd-web:pre-1620 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я30. web 1.63.0 — мобильная вёрстка без горизонтального скролла — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1630` (= 1.62.0).
- Аудит 360/390/414/768: все страницы (вход, список, раздача + файлы, Сеть ×4, Ферма ×2, Кабинет, Настройки все вкладки, меню профиля) — scrollWidth = ширине окна. Десктоп 1440/1024 — раскладка совпадает с 1.62.0.
- Откат: `docker tag containerd-web:pre-1630 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я31. web 1.63.1 — мобильные поповеры в пределах экрана — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1631` (= 1.63.0).
- Аудит открытых поповеров 360/390/414 (тап): фильтры, Файл, + Торрент, сортировка, профиль, «…», «Скачать», приоритет — все в пределах экрана, scrollWidth = ширине окна.
- Откат: `docker tag containerd-web:pre-1631 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я32. web 1.63.2 — на тач-устройствах только системный пикер у select — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1632` (= 1.63.1).
- Проверка (эмуляция тача, tap): фильтры ×4, «N / стр.», приоритет файла, селекты настроек — 0 своих списков, системное меню не блокируется, выбор применяется. Мышь — 1 свой список, как раньше.
- Откат: `docker tag containerd-web:pre-1632 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я33. web 1.63.3 — телефон: полоса статистики сеткой — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1633` (= 1.63.2).
- Проверка 360/390/414 (тап, реалистичные значения): чипы и меню чипов в пределах экрана, scrollWidth = ширине окна.
- Откат: `docker tag containerd-web:pre-1633 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я34. web 1.63.4 — телефон: вкладки «Сеть/Ферма» сеткой 3×2 — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1634` (= 1.63.3).
- Проверка 360/390 (тап): ни одна вкладка не обрезана, прокрутки нет, подсветка совпадает с активной вкладкой и после перехода в другой ряд.
- Откат: `docker tag containerd-web:pre-1634 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я35. web 1.63.5 — телефон: одинаковые строки лимитов — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1635` (= 1.63.4).
- Проверка 360/390/414 с реальными «своб.» (TB и GB): 12 строк (движки + каналы) — одна и та же раскладка, без переполнения; 1440 — как раньше в одну строку.
- Откат: `docker tag containerd-web:pre-1635 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я36. web 1.63.6 — телефон: графики целиком без прокрутки — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1636` (= 1.63.5).
- Проверка 360/390/414: «История» День (24) / Неделя (28) / Месяц (30) и мини-графики (30) — svg = ширине блока, прокрутки нет, ни одна подпись не обрезана, подсказка в пределах карточки.
- Откат: `docker tag containerd-web:pre-1636 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я37. web 1.64.0 — тёмная тема — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1640` (= 1.63.6).
- Проверка: все экраны и поповеры в тёмной теме на 1440 и 390; светлая 1440 — попиксельно как 1.63.6 (кроме версии в подвале и времени).
- Откат: `docker tag containerd-web:pre-1640 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я38. web 1.64.1 — тёмная тема: фон ярче — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1641` (= 1.64.0).
- Откат: `docker tag containerd-web:pre-1641 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я39. web 1.64.2 — без синей обводки у селектов настроек — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1642` (= 1.64.1).
- Проверка: 7 селектов (Кабинет + все вкладки Настроек), светлая/тёмная, 1440/390 — после клика/тапа обводки нет; с клавиатуры — нейтральное кольцо.
- Откат: `docker tag containerd-web:pre-1642 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я40. web 1.64.3 — тёмная тема: фон между 1.64.0 и 1.64.1 — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1643` (= 1.64.2).
- Значения (тёмная тема, явная и системная): слой 0.78, пятна 0.32 / 0.26 (индиго) / 0.25 / 0.32, точки 0.19.
- Откат: `docker tag containerd-web:pre-1643 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7я41. web 1.64.4 — подсказка cell-графиков на таче — 2026-10-21

- CT400: пересобран только `web`. Бэкап образа: `containerd-web:pre-1644` (= 1.64.3).
- Причина: подсказка `position:fixed` ставилась по координатам pointermove и на таче не пряталась — при скролле висела на месте окна поверх других карточек (WAN1).
- Фикс: `wireTip()` в `web/src/uploadChart.ts` — тап привязывает подсказку к столбику (зажим 8px), скролл/pointercancel/тап вне/resize прячут; мышь как раньше.
- Проверка: headless Chromium, 390px, touch — тап → подсказка у столбика, скролл → скрыта; десктоп 1440 hover работает.
- Откат: `docker tag containerd-web:pre-1644 containerd-web:latest && scripts/deploy-ct400.sh up -d --no-deps --no-build web`.

## 7. Откат

- Код: `git reset --hard <старый-HEAD>` или `git apply predeploy.patch`.
- Образы: предыдущие образы docker остаются в кэше до `docker image prune`;
  `docker compose up -d` с прежним чекаутом возвращает прошлую версию.
- Данные раздач/fastresume/session.state и БД лежат в docker-томах и на `/mnt/media`
  — пересборка образов их не затрагивает.
