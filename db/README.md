# Модуль БД (`seeding_db`)

## Зона ответственности

- SQLAlchemy-модели домена (`models.py`)
- Alembic: каталог [`alembic/`](alembic/), начальная ревизия `0001`; см. [`alembic/README.md`](alembic/README.md)
- Фабрика сессий и `init_models` — `session.py`
- Репозитории (`repository.py`): создание, список, обновление статуса, `update_info_hash`, `delete`, выборка для восстановления в движке (`list_for_engine_restore`)

## Использование

Устанавливается как пакет в `api`, `queue` (editable):

```bash
pip install -e ./db
```

Миграции из каталога `db/`:

```bash
cd db
alembic upgrade head
```

(`DATABASE_URL` — см. `seeding_db/config.py` и Alembic `env.py`.)

## Запреты

- Нет HTTP и libtorrent
- Не импортировать `api`, `engine`, `web`, `desktop`
