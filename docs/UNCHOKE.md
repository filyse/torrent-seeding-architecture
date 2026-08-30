# Раздача сразу многим (unchoke)

Сколько пиров **сессия** libtorrent кормит одновременно и как выбирает,
кому отдавать. Это не лимит одной раздачи: на движке с тысячей торрентов
32 слота делятся между всеми, у кого есть желающие пиры.

Дефолт libtorrent 2.x — **8 слотов** и **`fastest_upload`**: один быстрый
забирает канал, остальные ждут. Эксперимент на проде: **32** и
**`round_robin`**. Откат — снова 8 / `fastest_upload`, без пересборки.

## Где в UI

Настройки → Лимиты → «Раздача сразу многим». Разметка как у
«Загрузка файлов»: `limits-form`, компактное поле слотов, селект, кнопки
в той же строке.

## API

Публично (как остальной `/api/v1`, ключ `X-API-Key`):

- `GET /api/v1/settings/unchoke` → `{unchoke_slots_limit, seed_choking_algorithm}`
- `POST /api/v1/settings/unchoke` тело частично:
  `{unchoke_slots_limit?, seed_choking_algorithm?}`
  Ответ плюс `applied` / `errors` по движкам.

Алгоритмы: `round_robin` | `fastest_upload` | `anti_leech`.
Слоты: −1…256 (−1 = авто libtorrent).

Пишется в `app_settings` ключ `unchoke_policy`. При саморегистрации
движка оркестратор шлёт политику снова
(`POST /internal/v1/session/unchoke-settings`).

Внутренний API движка:

- `GET/POST /internal/v1/session/unchoke-settings`

Env движка (только до первой рассылки с оркестратора):

- `LT_UNCHOKE_SLOTS_LIMIT`
- `LT_SEED_CHOKING_ALGORITHM`

Новой схемы БД нет.

Версии: api **1.21.0**, engine **1.4.0**, web **1.41.0**.
На проде после выката 2026-08-30: api **1.20.0** (бамп от живого 1.19.0),
engine **1.4.0**, web **1.41.0**.

## Откат политики (без отката кода)

`POST /api/v1/settings/unchoke` с
`{"unchoke_slots_limit": 8, "seed_choking_algorithm": "fastest_upload"}`
или кнопка «Откатить к 8 / fastest».

## Откат кода

1. `git revert` коммита `feat: unchoke policy…` на `main`, push.
2. На CT400 **не** `git reset --hard` целиком: на хосте живые патчи
   длиннее `origin/main`. Снять только файлы фичи или откатиться к
   `.bak-unchoke-*` на хосте.
3. Движки: пересобрать `engine/` на 171 и 243, как в
   [`DEPLOYMENT_STATE.md`](DEPLOYMENT_STATE.md) §5.
4. CT400: `bash scripts/deploy-ct400.sh up -d --build api web`.
