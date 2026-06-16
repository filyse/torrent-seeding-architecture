"""Согласование статуса в БД с runtime движка (libtorrent)."""

from __future__ import annotations

from seeding_db.models import TorrentStatus


def status_from_runtime(
    runtime_status: str | None,
    lt_state: str | None,
    progress: float | None = None,
) -> str:
    rs = (runtime_status or "").strip().lower()
    st = (lt_state or "").strip().lower()
    # paused в runtime — это правда (ручная пауза). Не маскируем её под seeding,
    # иначе UI врёт и пауза «не прилипает». Корневой фикс рестарт-паузы — в движке
    # (auto_managed=False + неограниченные active_* лимиты) и в restore (авто-resume сидов).
    if rs == "paused":
        return TorrentStatus.paused.value
    if rs == "error":
        return TorrentStatus.error.value
    if st in {"seeding", "finished"}:
        return TorrentStatus.seeding.value
    if progress is not None and progress >= 0.999 and st != "downloading_metadata":
        return TorrentStatus.seeding.value
    if st in {"downloading", "downloading_metadata"}:
        return TorrentStatus.downloading.value
    return TorrentStatus.downloading.value
