"""Согласование статуса в БД с runtime движка (libtorrent)."""

from __future__ import annotations

from seeding_db.models import TorrentStatus


def _is_complete_seed(lt_state: str | None, progress: float | None) -> bool:
    st = (lt_state or "").strip().lower()
    if st in {"seeding", "finished"}:
        return True
    return progress is not None and progress >= 0.999 and st not in {"downloading", "downloading_metadata"}


def status_from_runtime(
    runtime_status: str | None,
    lt_state: str | None,
    progress: float | None = None,
) -> str:
    rs = (runtime_status or "").strip().lower()
    st = (lt_state or "").strip().lower()
    # Готовый сид: не записываем paused из transient-состояния handle после restore
    if rs == "paused" and _is_complete_seed(st, progress):
        return TorrentStatus.seeding.value
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
