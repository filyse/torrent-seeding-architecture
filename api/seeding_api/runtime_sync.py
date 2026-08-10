"""Сверка полей торрента в БД с snapshot движка."""

from __future__ import annotations

from seeding_db.models import TorrentRecord, TorrentStatus
from seeding_db.repository import TorrentRepository
from seeding_db.status_from_runtime import status_from_runtime


def accumulate_uploaded(total: int, seen: int, current: int) -> tuple[int, int]:
    """Свернуть сырое показание счётчика движка в накопитель «всего отдано».

    Счётчик libtorrent живёт в рамках одной инкарнации торрента: перенос на другой движок
    добавляет торрент заново и счётчик стартует с нуля (fastresume при переносе не
    передаётся — копия на цели проверяется рехэшем). Уход значения НАЗАД трактуем как
    новую инкарнацию: тогда весь `current` — это новая дельта, а прежний `seen` уже
    учтён в `total`.

    Возвращает (новый total, новый seen).
    """
    total = max(0, int(total or 0))
    seen = max(0, int(seen or 0))
    current = max(0, int(current or 0))
    delta = current - seen if current >= seen else current
    return total + delta, current


def uploaded_with_carry(total: int, seen: int, current: int) -> int:
    """«Всего отдано» для показа: накопленное на прошлых движках + живое показание.

    Read-time версия `accumulate_uploaded` — даёт актуальное значение между снимками,
    не дожидаясь фонового прохода.
    """
    return accumulate_uploaded(total, seen, current)[0]


def apply_uploaded_carry(row: TorrentRecord, runtime: dict | None) -> dict | None:
    """Подменить в рантайме `total_uploaded` на объём за всю жизнь раздачи.

    Движок знает только про себя, поэтому сырое значение обнуляется после переноса.
    Объём отданного привязан к раздаче, а не к движку, — отдаём накопленное.
    """
    if not runtime:
        return runtime
    runtime["total_uploaded"] = uploaded_with_carry(
        row.uploaded_total, row.uploaded_seen, runtime.get("total_uploaded") or 0
    )
    return runtime


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
