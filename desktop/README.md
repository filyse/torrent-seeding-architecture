# Десктоп-клиент (Windows)

Минимальный **CLI** и полнофункциональный **GUI** на Python (тот же REST, что у веба).

## CLI

```bash
pip install -e ./desktop
python -m seeding_desktop list
python -m seeding_desktop add --magnet "magnet:?xt=…" --save-path /data
python -m seeding_desktop add-file --torrent-file "C:\path\to\file.torrent" --save-path /data
python -m seeding_desktop get 1
python -m seeding_desktop pause 1
python -m seeding_desktop resume 1
python -m seeding_desktop remove 1
# или: seeding-desktop list
```

`--api-base` переопределяет URL (указывается **до** подкоманды); иначе читается конфиг
(см. ниже) или `http://127.0.0.1:8000`. `--api-key` — заголовок `X-API-Key`, если на API
задано `SEEDING_API_KEYS`.

## GUI

```bash
pip install -e ".[gui]"
python -m seeding_desktop.gui
# или: seeding-desktop-gui
```

В GUI есть:

- **Подключение**: поле API URL и API-ключ (`X-API-Key`); сохраняются в конфиг по кнопке
  «Подключить».
- **Сводка сессии**: число раздач/активных, скорость ↓/↑, всего отдано, онлайн-движки.
- **Тулбар**: поиск по имени/метке/hash, фильтр по статусу, сортировка (новые/имя/размер/ratio),
  кнопка «+ Добавить», обновление.
- **Добавление** в модальном окне: вкладки Magnet / URL / Файлы (**мультивыбор** `.torrent`),
  выбор **движка со свободным местом**, метка с подсказками, необязательные имя и свой путь.
- **Таблица** с прогресс-баром: id, имя, метка, статус, %, размер, ↓, ↑, сиды/пиры, ratio, ETA,
  движок. Мультивыбор строк.
- **Массовые действия** над выбранными: Пауза / Старт / Метка / Удалить (с опцией удаления файлов).
- **Детали** (двойной клик): список файлов и трекеров, перепроверка (recheck) и реанонс.
- **Автообновление** списка каждые 5 секунд в фоне (не морозит интерфейс).

## Сборка EXE (Windows, PyInstaller)

Из каталога `desktop/`:

```powershell
.\scripts\build_windows.ps1
```

Либо вручную: `pip install -e ".[build]"`, затем команда из скрипта (артефакт в
`dist/seeding-desktop.exe`).

## Конфигурация

- Windows: `%APPDATA%\TorrentSeeding\config.json`
- Linux/macOS: `~/.config/TorrentSeeding/config.json`

Пример: `{"api_base_url": "http://127.0.0.1:8000", "api_key": ""}`

## Запреты

- Нет прямого доступа к Redis, Postgres, внутреннему порту движка — только публичный `api`.
