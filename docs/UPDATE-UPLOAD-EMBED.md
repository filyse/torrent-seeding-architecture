# Обновление: загрузка файлов вшита в движок

> Выкат **2026-08-16 выполнен** (`3620b5d` и этот follow-up). Код: ветка `main` репозитория
> [torrent-seeding-architecture](https://github.com/filyse/torrent-seeding-architecture).
> Контракт и схема: [`FILE_UPLOAD.md`](FILE_UPLOAD.md).
> Топология хостов: [`DEPLOYMENT_STATE.md`](DEPLOYMENT_STATE.md).

Это **операционный runbook**: что меняется, в каком порядке трогать 171 / 243 / CT400,
как проверить и как откатиться. NPM-локации `/u/b/` и `/u/a/` **не менять**.

## Зачем

До выката браузер лил файлы в Python-sidecar `seeding-upload` на `:8090`. Sidecar
монтировал те же каталоги, что и движки, и писал на диск сам.

После выката:

1. `/upload/v1` живёт **в процессе каждого движка** (тот же том `/data`).
2. На `:8090` стоит тонкий **upload-edge** (nginx). Он знает только
   `/{engine_id}/upload/v1/` и `/{engine_id}/health`. **`/internal/v1` наружу не отдаёт.**
3. API при `SEEDING_UPLOAD_PER_ENGINE=1` дописывает `/{engine_id}` к `upload_base_url`.
   Браузер ходит на `https://seedbox2.hw-s.ru/u/b/b1/upload/v1/…` — NPM по-прежнему
   режет префикс `/u/b/` и отдаёт на `171:8090` путь `/b1/upload/v1/…`.
4. Admin правит параллель и число чанков в **Настройки → Лимиты** (не в `.env`).

Sidecar остаётся в репо только как откат (`docker-compose.upload.yml`).

## Версии

| Компонент | Версия | Где смотреть |
|-----------|--------|----------------|
| web | 1.19.0 | `web/src/version.ts`, подвал UI |
| api | 1.11.0 | `GET /api/v1/health` / `seeding_api.__version__` |
| engine | 1.3.0 | `docker exec <id>-seeding python -c "from seeding_engine import __version__; print(__version__)"` |
| upload (пакет) | 0.2.0 | вшит в образ движка; sidecar 0.2.0 если поднят |

Лимиты: `GET/POST /api/v1/settings/upload` (POST только **admin**). Пока в БД пусто —
дефолты `SEEDING_UPLOAD_MAX_PARALLEL` / `SEEDING_UPLOAD_CHUNK_CONCURRENCY` (оба 4).
На HDD (b*) после выката поставь **1** файл и **2–4** чанка.

## Что не трогаем

- NPM на seedbox2: `/u/b/` → `192.168.1.171:8090`, `/u/a/` → `192.168.2.243:8090`.
- Порт `:8090` на data-host (сначала sidecar, потом edge — тот же порт).
- Тома контента, `docker-compose.bN-content.yml`, `docker-compose.a-host.yml`.
- `SEEDING_UPLOAD_TICKET_SECRET` — тот же, что уже у api/sidecar. **Не печатать.**
- Relay на RU VDS: upstream как был; в path просто появляется `/{engine_id}`.
- CT400: только `scripts/deploy-ct400.sh` (всегда с media-оверреем).

## Порядок (менять нельзя)

Сначала **git push в `origin/main`**, потом data-plane, потом флаг на API, потом гашение sidecar.

```
1. Коммит + push в GitHub
2. 171: pull → секрет в .env.engine* → пересборка b1–b6 → сеть → edge up → sidecar down
3. 243: то же для a1–a3
4. CT400: SEEDING_UPLOAD_PER_ENGINE=1 → deploy-ct400.sh up -d --build (api + web)
5. Смоук: :8090/health, :8090/b1/health, :8090/a1/health, UI «Файл» на b* и a*
6. Admin: Настройки → Лимиты → 1 / 2–4 на время обкатки HDD
```

Пока API ещё со старым флагом `PER_ENGINE=0`, билеты указывают на `/u/b` без id —
это путь sidecar. Поэтому **сначала** поднимаем edge+движки (они понимают и
`/{id}/upload/v1`, и старый sidecar ещё слушает `:8090`), **потом** переключаем API,
**потом** гасим sidecar. На практике edge занимает `:8090`, поэтому sidecar
глушим на том же хосте сразу после `edge up` (шаг 2/3), а API переключаем когда
оба хоста уже на edge.

Итого на каждом data-host: rebuild движков → attach в `seeding-upload` →
`upload-edge up` → `upload down`. API — когда оба edge живы.

## 0. Git (с рабочей машины)

Репозиторий отдельный, не `c:\Scripts`:

```bash
cd torrent-seeding-architecture   # локально: c:\Scripts\torrent-seeding
git push origin main
```

На хостах код берётся **из этого чекаута**, образы собираются на месте.
`git reset --hard origin/main` не трогает untracked секреты (`.env*`,
`docker-compose.*-content.yml`, `docker-compose.a-host.yml`, `certs/`).

На **CT400** перед этим выкатом в дереве уже лежали локальные патчи sidecar-эпохи
(`upload.py`, куски `main.ts`). Их заменяет этот коммит — `reset --hard` после
push как раз нужен. Caddy `/u/b` `/u/a` теперь **в git** (`Caddyfile`), иначе
reset снял бы прокси загрузки. Untracked (favicon, `.env.bak*`) останутся.

Перед выравниванием на хосте:

```bash
git diff origin/main > ~/predeploy-upload-$(date +%Y%m%d).patch || true
git status --porcelain > ~/predeploy-upload-$(date +%Y%m%d).status
```

## 1. Секрет на движки (не печатать)

У api и sidecar секрет уже есть. У `.env.engine*` на 171/243 его не было —
без него `/upload/v1` в движке выключен.

С хоста, где крутится sidecar `seeding-upload`:

```bash
# значение в переменную, в stdout не echo
set +o history
SECRET=$(docker exec seeding-upload printenv SEEDING_UPLOAD_TICKET_SECRET)
# если sidecar уже снят — взять с CT400 (не печатать):
# SECRET=$(pct exec 400 -- bash -lc 'grep ^SEEDING_UPLOAD_TICKET_SECRET= /opt/containerd/.env | cut -d= -f2-')

ensure_secret() {
  local f="$1"
  [ -f "$f" ] || return 0
  if grep -q '^SEEDING_UPLOAD_TICKET_SECRET=.' "$f"; then
    echo "$f already set"
    return 0
  fi
  if grep -q '^SEEDING_UPLOAD_TICKET_SECRET=' "$f"; then
    # пустое значение — заменить
    tmp=$(mktemp)
    grep -v '^SEEDING_UPLOAD_TICKET_SECRET=' "$f" > "$tmp"
    printf 'SEEDING_UPLOAD_TICKET_SECRET=%s\n' "$SECRET" >> "$tmp"
    mv "$tmp" "$f"
  else
    printf '\nSEEDING_UPLOAD_TICKET_SECRET=%s\n' "$SECRET" >> "$f"
  fi
  echo "$f updated"
}

# 171:
for f in .env.engine .env.engine.b2 .env.engine.b3 .env.engine.b4 .env.engine.b5 .env.engine.b6; do
  ensure_secret "$f"
done
unset SECRET
set -o history
```

На 243 секрет тот же. Проще скопировать одну строку с 171 на 243 по SSH
(`grep ^SEEDING_UPLOAD_TICKET_SECRET=`) или взять из sidecar на 243, если он есть.

Проверка **без** раскрытия: `grep -c '^SEEDING_UPLOAD_TICKET_SECRET=.' .env.engine*`
должно быть 6 на 171 и столько же env-файлов/сервисов на 243.

## 2. b-host `192.168.1.171` (`rudub`, SSH `:24`, `~/seeding-engine`)

```bash
cd ~/seeding-engine
git fetch origin
git reset --hard origin/main

docker network create seeding-upload 2>/dev/null || true

# b1
docker compose -p seeding-engine --env-file .env.engine \
  -f docker-compose.engine.yml \
  -f docker-compose.b1-content.yml \
  -f docker-compose.engine.upload.yml \
  up -d --build

# b2..b6
for n in 2 3 4 5 6; do
  docker compose -p seeding-engine-b$n --env-file .env.engine.b$n \
    -f docker-compose.engine.yml \
    -f docker-compose.b$n-content.yml \
    -f docker-compose.engine.upload.yml \
    up -d --build
done

bash scripts/upload-edge-attach.sh

# занять :8090: гасим sidecar по имени (compose down требует env sidecar'а)
docker stop seeding-upload && docker rm seeding-upload
docker compose -f docker-compose.upload-edge.yml up -d

curl -sS http://127.0.0.1:8090/health
curl -skS http://127.0.0.1:8090/b1/health
# ожидание: {"ok":true,"role":"upload-edge"} и health движка (backend=libtorrent)
```

Контейнеры должны остаться `bN-seeding` + `seeding-upload-edge`. `seeding-upload` — нет.
Тома контента не трогаем (`down` sidecar без `-v`).

Краткий простой движков на время recreate — минуты на первый build, дальше кэш слоёв.

## 3. a-host `192.168.2.243` (`rudub2`, прыжок `-J root@192.168.1.10`)

Путь: `~/torrent-seeding-architecture`. Хостовый файл `docker-compose.a-host.yml`
**untracked** — `reset --hard` его не сотрёт. Сервисы там называются `a1`/`a2`/`a3`,
не `engine`, поэтому оверрей `docker-compose.engine.upload.yml` **не подключать** —
он не попадёт ни в один сервис.

В `a-host.yml` руками (файл на хосте, не в git):

1. Сборка из корня репо (нужен пакет `upload/seeding_upload`):
   ```yaml
   x-engine-common: &engine-common
     build:
       context: .
       dockerfile: engine/Dockerfile
     image: seeding-engine-a:latest
   ```
2. В `x-engine-env` добавить (секрет — через `.env` рядом, не вписывать в yaml):
   ```yaml
   SEEDING_UPLOAD_TICKET_SECRET: ${SEEDING_UPLOAD_TICKET_SECRET:-}
   UPLOAD_CORS_ORIGINS: "https://seedbox2.hw-s.ru,https://seedbox.hw-s.ru"
   UPLOAD_CONTENT_UID: "1000"
   UPLOAD_CONTENT_GID: "1000"
   ```
3. В `.env` в корне репо на 243 — та же строка `SEEDING_UPLOAD_TICKET_SECRET=…`,
   что у sidecar/api. Не `echo` в терминал.

```bash
cd ~/torrent-seeding-architecture
git fetch origin
git reset --hard origin/main
# a-host.yml после reset на месте; правки build/env из пунктов 1–2 сохранить
# (это untracked — reset их не откатит).

docker network create seeding-upload 2>/dev/null || true

docker compose -p seeding-engines-a -f docker-compose.a-host.yml up -d --build
bash scripts/upload-edge-attach.sh

docker stop seeding-upload && docker rm seeding-upload
docker compose -f docker-compose.upload-edge.yml up -d

curl -sS http://127.0.0.1:8090/health
curl -skS http://127.0.0.1:8090/a1/health
```

## 4. CT400 (`192.168.1.101`, `pct exec 400`, `/opt/containerd`)

Только канонический скрипт. Без media-оверрея движки на CT400 (если они есть в
compose) встанут на пустые тома.

```bash
cd /opt/containerd
git fetch origin
git reset --hard origin/main

# флаг per-engine; секрет уже должен быть
if grep -q '^SEEDING_UPLOAD_PER_ENGINE=' .env; then
  sed -i 's/^SEEDING_UPLOAD_PER_ENGINE=.*/SEEDING_UPLOAD_PER_ENGINE=1/' .env
else
  echo 'SEEDING_UPLOAD_PER_ENGINE=1' >> .env
fi
# UI загрузки должна остаться включена
if grep -q '^SEEDING_UPLOAD_ENABLED=' .env; then
  sed -i 's/^SEEDING_UPLOAD_ENABLED=.*/SEEDING_UPLOAD_ENABLED=1/' .env
else
  echo 'SEEDING_UPLOAD_ENABLED=1' >> .env
fi

bash scripts/deploy-ct400.sh up -d --build
```

`--build` пересоберёт api и web. Не добавляй `--remove-orphans`, если не уверен,
что список файлов compose совпадает с живым стеком.

Проверка:

```bash
curl -sS http://127.0.0.1:8000/api/v1/health
# в ответе/логах api версия 1.11.0; web в UI — 1.19.0
```

`GET /api/v1/upload/features` (с ключом) должен дать `"per_engine": true`.

## 5. Смоук

С data-host:

```bash
curl -sS http://127.0.0.1:8090/health          # role=upload-edge
curl -skS http://127.0.0.1:8090/b1/health      # 171
curl -skS http://127.0.0.1:8090/a1/health      # 243
curl -sS http://127.0.0.1:8090/internal/v1/meta   # 404 от edge, не движок
```

Из браузера на `https://seedbox2.hw-s.ru/`:

1. Меню **Файл** → залить небольшой файл на **b1** (direct).
2. То же на **a1**.
3. Обрыв + докачка (пауза в очереди / повтор).
4. «Создать торрент» на каталог.
5. Admin: **Настройки → Лимиты → Загрузка файлов** — сохранить 1 и 2, обновить
   страницу, снова открыть очередь: лимиты те же.

Релей (если пользуетесь): тот же файл с маршрутом «через релей».

## 6. Откат

1. CT400: `SEEDING_UPLOAD_PER_ENGINE=0`, `bash scripts/deploy-ct400.sh up -d api`
   (или полный `up -d --build`, если менялся только env — достаточно recreate api).
2. На 171 и 243:
   ```bash
   docker compose -f docker-compose.upload-edge.yml down
   docker compose -f docker-compose.upload.yml -f docker-compose.upload.b-host.yml up -d   # 171
   docker compose -f docker-compose.upload.yml -f docker-compose.upload.a-host.yml up -d   # 243
   # sidecar compose требует SEEDING_UPLOAD_TICKET_SECRET и UPLOAD_ENGINE_ROOTS в env
   ```
3. Уже залитые файлы не трогаем. Движки можно не откатывать: лишний `/upload/v1`
   на движке при работе sidecar не мешает.
4. Код: `git reset --hard <старый SHA>` на хосте, если нужна старая сборка api/web.

## 7. Новый движок после выката

В `.env.engine` того же `SEEDING_UPLOAD_TICKET_SECRET`, что у api.
`./scripts/deploy-engine.sh` сам создаст сеть `seeding-upload` и подключит оверрей.
Edge резолвит `{id}-seeding` по Docker DNS — перезапуск edge не нужен.

## 8. Частые сбои

| Симптом | Что проверить |
|---------|----------------|
| UI «загрузка выключена» | `SEEDING_UPLOAD_ENABLED=1`, секрет задан, `BASE_URLS` не пустые |
| 403 на чанках | ticket.eng ≠ id движка, или секрет api ≠ секрет движка |
| 404 на `/b1/upload/v1` | API ещё с `PER_ENGINE=0`, либо edge не поднят, либо NPM режет лишний префикс |
| 502 от edge | контейнер `{id}-seeding` не в сети `seeding-upload`; `upload-edge-attach.sh` |
| Диск b* «трещит» | Настройки → Лимиты: 1 параллель, 2–4 чанка |
| После rebuild раздачи пустые | Собрали **без** `*-content.yml` / media overlay — тома не те. Не делать `down -v` |
