from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TorrentStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    seeding = "seeding"
    paused = "paused"
    error = "error"


class TorrentRecord(Base):
    """Логическая сущность торрента в системе (не путать с libtorrent handle)."""

    __tablename__ = "torrents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    info_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    magnet_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(String(512), default="")
    save_path: Mapped[str] = mapped_column(Text, default="")
    engine_id: Mapped[str] = mapped_column(String(32), default="default", index=True)
    label: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default=TorrentStatus.queued.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
