"""Версия и метка времени сборки API — для отображения в настройках («Информация»)."""

import os

from seeding_api import __version__

_BUILD_TIME_FILE = os.getenv("BUILD_TIME_FILE", "/app/BUILD_TIME")


def build_time() -> str | None:
    """Метка времени сборки образа (пишется Dockerfile'ом в /app/BUILD_TIME)."""
    try:
        with open(_BUILD_TIME_FILE, encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


def version_info() -> dict:
    return {"version": __version__, "built_at": build_time()}
