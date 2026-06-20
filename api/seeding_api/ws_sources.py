"""Сборка начальных снапшотов для каналов WebSocket (Фаза 7).

WS-1: реализован канал ``stats`` (агрегаты сессии, как у SSE). Остальные каналы
(``torrent:{id}``, ``engines``, ``migrate:{id}``, ``job:{id}``) получат начальные снапшоты
и publisher'ы дельт в WS-2; пока для них возвращаем ``None`` — клиент получит первую дельту
от фонового источника, а актуальные данные подтянет обычным REST-запросом.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def _stats_snapshot(app: Any) -> dict:
    pool = app.state.engine_pool
    by_engine = await pool.session_stats_all()
    return {"stats": pool.aggregate_session_stats(by_engine)}


async def initial_snapshot(app: Any, channel: str) -> Any | None:
    """Вернуть начальный снапшот для канала или None, если сборка не поддержана."""
    if channel == "stats":
        return await _stats_snapshot(app)
    return None
