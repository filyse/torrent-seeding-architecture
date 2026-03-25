# Десктоп-клиент (Windows)

## Текущая заготовка

Минимальный **CLI** на Python (тот же REST, что у веба):

```bash
pip install -e ./desktop
python -m seeding_desktop list
python -m seeding_desktop add --magnet "magnet:?xt=…" --save-path /data
python -m seeding_desktop get 1
python -m seeding_desktop pause 1
python -m seeding_desktop resume 1
python -m seeding_desktop remove 1
# или: seeding-desktop list
```

`--api-base` переопределяет URL (указывается **до** подкоманды: `python -m seeding_desktop --api-base http://host:8000 list`); иначе читается конфиг (см. ниже) или `http://127.0.0.1:8000`.

`--api-key` — заголовок `X-API-Key`, если на API задано `SEEDING_API_KEYS`.

## Сборка EXE (Windows, PyInstaller)

Из каталога `desktop/`:

```powershell
.\scripts\build_windows.ps1
```

Либо вручную: `pip install -e ".[build]"`, затем команда из скрипта (артефакт в `dist/seeding-desktop.exe`). На машине разработчика должен быть установлен Python и PyInstaller из extra `build`.

## Рекомендуемый стек (GUI позже)

- Python **3.11+** + **PySide6** или **PyQt6** (лицензия: учесть LGPL/commercial для Qt).
- Альтернатива по согласованию с координатором: **Tauri** + веб-вью + тот же REST API.

## Конфигурация

- Windows: `%APPDATA%\TorrentSeeding\config.json`
- Linux/macOS: `~/.config/TorrentSeeding/config.json`

Пример: `{"api_base_url": "http://127.0.0.1:8000"}`

## План

«Агент 2» в [`docs/PLAN_BY_AGENT.md`](../docs/PLAN_BY_AGENT.md).

## Запреты

- Нет прямого доступа к Redis, Postgres, внутреннему порту движка — только публичный `api`.
