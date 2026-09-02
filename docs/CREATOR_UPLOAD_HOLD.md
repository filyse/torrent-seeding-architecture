# Hold отдачи на время хеша HDD

Пока движок считает SHA-1 сезона (`libtorrent set_piece_hashes`), случайные
чтения отдачи с того же шпинделя душат последовательный проход. На полной
отдаче создание почти стоит; при ручном минимуме — быстро. Автоhold делает
то же самое сам: только на том движке, где идёт хеш, и только если том —
HDD (или тип неизвестен).

SSD (a1–a3) не режем: там хеш и отдача не дерутся за головку.

Версии: engine **1.6.0**, api **1.23.0**, web **1.44.0**.
Спека очереди создания: [`CREATOR.md`](CREATOR.md).

## Правила

| Тема | Решение |
|------|---------|
| Кап | 1 МБ/с (`SEEDING_CREATOR_UPLOAD_LIMIT_BPS=1048576`) |
| Где | HDD и `unknown`. SSD — без капа |
| Детект | sysfs `queue/rotational` у **тома раздачи** `storage_path()` = `/data/<id>`, не корень `/data` |
| БД | `engines.upload_limit` не пишем. Hold только в RAM процесса |
| Heartbeat | `set_session_limits` запоминает новое desired, кап не снимает |
| Уже строже | не поднимаем лимит пользователя |
| Снятие | `finally`: успех, ошибка, отмена. Crash → следующий register вернёт лимит из БД |
| Образ | один на все 9 движков, compose руками не размечаем |

На 171 корень контейнера `/data` сидит на OS NVMe. Контент — в `/data/bN` на
HDD. Если смотреть rotational у `/data`, b* ложно станут SSD и hold не
включится.

## Env

| Переменная | Дефолт | Смысл |
|------------|--------|--------|
| `SEEDING_CREATOR_UPLOAD_LIMIT_BPS` | `1048576` | потолок отдачи на время хеша. `0` — hold выкл |
| `SEEDING_STORAGE_KIND` | пусто = авто | `hdd` / `ssd` / `unknown`, если sysfs врёт |

Новых полей в `.env.engine` на проде не нужно: дефолты покрывают b* и a*.

## Поток

```
CreatorService._run
  → runtime.set_creator_hold(True)
      HDD/unknown: SessionUploadGate.begin_create → session ≤ cap
      SSD: no-op, задача без upload_hold
  → set_piece_hashes (отдельный процесс)
  → finally set_creator_hold(False) → вернуть desired
```

`SessionUploadGate` живёт в `engine/seeding_engine/upload_hold.py`.
Детект — `sysinfo.storage_kind()` / `classify_storage_path()`.

Во время hold `session/stats.upload_limit` — **фактически применённый** кап.
`upload_limit_desired` — куда вернуть (лимит из БД / UI).
`creator_upload_hold` / `creator_upload_hold_bps` — флаг для панели.

## API / UI

- Задача creator: `upload_hold` в `CreateTaskOut` / `CreatorTaskOut`.
- Очередь создания: чип «отдача ограничена на время хеша (HDD)…».
- Сеть: шкала как в 1.43.3, «хеш» поверх бара. Чип в очереди создания.
- `GET /health` и internal health: `disk_kind`.
- `GET /internal/v1/session/stats` (и агрегат `by_engine`): `disk_kind`,
  `creator_upload_hold`, `creator_upload_hold_bps`, `upload_limit_desired`.

## Выкат

Один образ engine на 171, потом 243; затем api+web на CT400.
Оркестратор (`queue_worker`) не останавливать. Команды —
[`DEPLOYMENT_STATE.md`](DEPLOYMENT_STATE.md) §7ц.

Проверка после выката:

```bash
# b1 — HDD
docker exec b1-seeding python3 -c "from seeding_engine import __version__; print(__version__)"
docker exec b1-seeding wget -qO- http://127.0.0.1:8081/health
# disk_kind=hdd, version=1.6.0

# a1 — SSD
docker exec a1-seeding wget -qO- http://127.0.0.1:8081/health
# disk_kind=ssd
```

Создать торрент на b*: в очереди чип, на «Сети» подпись, отдача этого
движка ~1 МБ/с, остальные b* не режутся. На a* чипа нет.

## Откат

Без отката кода: на хосте движка `SEEDING_CREATOR_UPLOAD_LIMIT_BPS=0` и
пересоздать контейнер.

Откат кода: `git revert` коммита hold на `main`, push; пересобрать engine
на 171/243 и api+web на CT400. На прод-хостах **не** `git reset --hard`
целиком — деревья длиннее `origin/main`.
