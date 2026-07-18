# Состояние деплоя и реконсилиация git ↔ прод

> Дата сверки: 2026-07-18. Источник истины кода — ветка `main` на GitHub
> (`git@github.com:filyse/torrent-seeding-architecture.git`, тип `ef97d9c` на момент сверки).

Документ фиксирует **фактическую топологию продакшена**, расхождение рабочих
деревьев на живых хостах относительно `origin/main` и порядок приведения git к
нормальному виду с последующим чистым деплоем.

## 1. Топология

| Роль | Хост / доступ | Путь репозитория | Что крутится |
|------|----------------|------------------|--------------|
| Оркестратор (control plane) | CT 400 (Proxmox LXC, `192.168.1.101`), доступ через PVE `192.168.1.10` → `pct exec 400` | `/opt/containerd` | `api`, `queue_worker`, `web`, `db` (postgres), `redis`, `caddy`, стек наблюдаемости (prometheus/grafana/cadvisor/node-exporter) |
| Движки b1–b6 (data plane) | seedbox b-host `rudub@192.168.1.171:24` | `/home/rudub/seeding-engine` | контейнеры `b1-seeding`…`b6-seeding` (по тому на диск), плюс легаси `torrent-api`, `rtorrent-rutorrent`, `ftpd`, `webdav` |
| Движки a1–a3 (data plane) | seedbox a-host `rudub2@192.168.2.243` (LAN2; SSH только через прыжок `-J root@192.168.1.10`) | `/home/rudub2/torrent-seeding-architecture` | контейнеры `a1-seeding`…`a3-seeding` (один compose-проект `seeding-engines-a`) |

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

## 7. Откат

- Код: `git reset --hard <старый-HEAD>` или `git apply predeploy.patch`.
- Образы: предыдущие образы docker остаются в кэше до `docker image prune`;
  `docker compose up -d` с прежним чекаутом возвращает прошлую версию.
- Данные раздач/fastresume/session.state и БД лежат в docker-томах и на `/mnt/media`
  — пересборка образов их не затрагивает.
