# Ручные сценарии (веб + десктоп)

Используйте при приёмке релиза или перед PR, затем кратко занесите результат в `docs/reports/YYYY-MM-DD-qa-agent9.md`.

**Автоматически (без браузера):** из корня проекта `pip install -e "./db[dev]" -e "./api[test]" -e "./engine" && pytest`; фронт: `cd web && npm install && npm run build`. Ожидание pytest: 18 passed, 3 skipped (без compose-интеграции).

## Предусловия

- Подняты **API** и **engine** (например `docker compose up` или локально uvicorn).
- База с миграциями / `SEEDING_AUTO_SCHEMA` для dev.

## Веб (`web/`)

- [ ] Открыть UI (Docker `:3000` или `npm run dev`).
- [ ] Список торрентов загружается без ошибки в красной строке статуса.
- [ ] Добавить magnet + `save_path` — запись появляется в таблице, статус обновляется.
- [ ] Пауза / старт для строки — статус и при необходимости `runtime` меняются.
- [ ] **Удалить** торрент — строка исчезает из списка; в движке торрент снят (проверка через детали/логи при необходимости).
- [ ] Клик по **id** — страница деталей, JSON с полем `runtime` (или `null`, если движок недоступен).
- [ ] «К списку» / hash `#/` — возврат к списку.
- [ ] После остановки API проверить понятное сообщение об ошибке сети (не пустой экран).

## Десктоп CLI (`desktop/`)

```bash
pip install -e ./desktop
python -m seeding_desktop --api-base http://127.0.0.1:8000 list
python -m seeding_desktop --api-base http://127.0.0.1:8000 add --magnet "magnet:?xt=..." --save-path /data
python -m seeding_desktop --api-base http://127.0.0.1:8000 get 1
python -m seeding_desktop --api-base http://127.0.0.1:8000 pause 1
python -m seeding_desktop --api-base http://127.0.0.1:8000 resume 1
python -m seeding_desktop --api-base http://127.0.0.1:8000 remove 1
```

При включённых ключах API: добавьте `--api-key …`.

- [ ] `list` возвращает JSON-массив.
- [ ] `add` возвращает 201 и объект торрента.
- [ ] `get` / `pause` / `resume` отрабатывают для существующего id.
- [ ] При неверном URL — ненулевой код выхода и сообщение в stderr.

## Нагрузка

- [ ] Не входит в минимальный чеклист; при необходимости — отдельный план агента 9.
