Запуск из каталога `db/`:

```bash
# Linux/macOS
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost/seeding
alembic upgrade head
```

Windows (PowerShell):

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/seeding"
alembic upgrade head
```

Для миграций в `env.py` подставляется синхронный URL (`postgresql://` или `sqlite:///…`).
