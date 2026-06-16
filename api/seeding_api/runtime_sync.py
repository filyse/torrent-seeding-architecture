"""Сверка полей торрента в БД с snapshot движка."""

from __future__ import annotations

from seeding_db.models import TorrentRecord, TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime


async def merge_runtime_into_row(
    repo: TorrentRepository,
    row: TorrentRecord,
    runtime: dict | None,
) -> str:
    """Возвращает актуальный status для ответа API; при расхождении обновляет БД."""
    # Перенос между движками: статус «migrating» держится до завершения переноса и не
    # перетирается рантаймом (на источнике раздача может стоять на паузе во время копии).
    if row.status == TorrentStatus.migrating.value:
        return row.status
    if not runtime:
        return row.status
    target = status_from_runtime(
        runtime.get("runtime_status"),
        runtime.get("lt_state"),
        runtime.get("progress"),
    )
    if row.status != target:
        await repo.update_status(row.id, target)
        return target
    return row.status
