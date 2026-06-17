from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TorrentStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    seeding = "seeding"
    paused = "paused"
    migrating = "migrating"
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


class ApiKeyRecord(Base):
    """Именованный API-ключ с ролью (Фаза 5). Сам ключ не хранится — только его SHA-256
    и короткий префикс для отображения. Роли: viewer < operator < admin."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    role: Mapped[str] = mapped_column(String(16), default="operator")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EngineRecord(Base):
    """Динамический реестр движков (Фаза 4.5): движок может зарегистрироваться сам по
    API-ключу, без правки статического `engines.json`. Статический конфиг остаётся базой —
    записи из БД дополняют/переопределяют его в `EnginePool`."""

    __tablename__ = "engines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    url: Mapped[str] = mapped_column(Text, default="")
    storage_prefix: Mapped[str] = mapped_column(Text, default="")
    media_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    listen_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
