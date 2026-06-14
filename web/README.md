# Веб-клиент

SPA на **Vite + TypeScript** (без React): список торрентов, добавление по magnet, пауза/возобновление, удаление, периодическое обновление списка.

## Локальная разработка

Из каталога `web/`:

```bash
npm install
npm run dev
```

Откройте URL из вывода Vite (обычно `http://127.0.0.1:5173`). Запросы к **`/api`** проксируются на `http://127.0.0.1:8000` (см. `vite.config.ts`) — поднимите API отдельно.

Клиент использует относительный префикс **`/api/v1`**, без `VITE_*`: в Docker тот же путь обслуживает **Nginx** и проксирует на сервис `api`.

## Сборка и Docker

Из **корня** репозитория:

```bash
docker build -f web/Dockerfile .
```

Контекст — корень, потому что Dockerfile копирует `web/`.

## E2E (Playwright)

```bash
npm install
npx playwright install
set PLAYWRIGHT_BASE_URL=http://127.0.0.1:5173
npm run test:e2e
```

## План

«Агент 3» в [`docs/PLAN_BY_AGENT.md`](../docs/PLAN_BY_AGENT.md): детальная карточка торрента, SSE/WebSocket, обработка 401/403.
