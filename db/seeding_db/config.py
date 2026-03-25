import os


def get_database_url() -> str:
    """Async SQLAlchemy URL (asyncpg, aiosqlite, …)."""
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./seeding_dev.db")


def alembic_sync_url() -> str:
    """Sync URL for offline/online Alembic migrations."""
    u = get_database_url()
    u = u.replace("postgresql+asyncpg://", "postgresql://")
    if u.startswith("sqlite+aiosqlite:///"):
        return "sqlite:///" + u.removeprefix("sqlite+aiosqlite:///")
    if u.startswith("sqlite+aiosqlite://"):
        return "sqlite:///" + u.removeprefix("sqlite+aiosqlite://")
    return u
