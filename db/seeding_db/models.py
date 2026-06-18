from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func
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


class UserRecord(Base):
    """Пользователь с логином/паролем (Фаза 5, обычная авторизация). Пароль хранится
    только в виде PBKDF2-хеша. Роль — те же уровни, что и у API-ключей."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(16), default="operator")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRecord(Base):
    """Сессия входа по логину/паролю. Токен (`ses_…`) не хранится — только его SHA-256.
    Роль и имя денормализованы, чтобы проверка не требовала JOIN."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditRecord(Base):
    """Аудит-лог действий (Фаза 5): кто и какое изменяющее действие выполнил.

    Заполняется middleware'ом для всех мутаций (POST/PUT/PATCH/DELETE) и для входа.
    Хранит актора, метод/путь, статус ответа, IP и человекочитаемую сводку."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(64), default="", index=True)
    role: Mapped[str] = mapped_column(String(16), default="")
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")


class MigrationJob(Base):
    """Состояние переноса раздачи между движками (Фаза 4, возобновляемость).

    Хранится в БД, чтобы перенос переживал перезапуск оркестратора и мог быть
    возобновлён после сбоя без полного отката: частичная копия на цели сохраняется,
    повтор докачивает недостающие файлы (см. `migrate.run_migration`)."""

    __tablename__ = "migration_jobs"

    torrent_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    source_engine_id: Mapped[str] = mapped_column(String(64))
    target_engine_id: Mapped[str] = mapped_column(String(64))
    source_save_path: Mapped[str] = mapped_column(Text, default="")
    target_save_path: Mapped[str] = mapped_column(Text, default="")
    src_content_path: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(512), default="")
    transport: Mapped[str] = mapped_column(String(16), default="media")
    # running | failed | done | cancelled
    state: Mapped[str] = mapped_column(String(16), default="running")
    phase: Mapped[str] = mapped_column(String(32), default="preparing")
    copied: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    total: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LabelQuota(Base):
    """Кумулятивная квота по объёму отдачи на метку (Фаза 5).

    `uploaded_total` копится воркером из дельт `total_uploaded` каждого торрента метки
    (переживает рестарт движка: при сбросе счётчика libtorrent дельта берётся от нуля).
    При достижении `upload_quota` активные раздачи метки ставятся на паузу; при сбросе
    квоты они возобновляются."""

    __tablename__ = "label_quotas"

    label: Mapped[str] = mapped_column(String(128), primary_key=True)
    upload_quota: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    uploaded_total: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    exceeded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    paused_ids: Mapped[str] = mapped_column(Text, default="")
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TorrentMeter(Base):
    """Базовая отметка отданного по торренту — для подсчёта дельт квот (Фаза 5)."""

    __tablename__ = "torrent_meter"

    torrent_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    last_uploaded: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # Постоянные лимиты сессии движка (байт/с); NULL = без ограничения. Переживают
    # перезапуск движка — переприменяются при саморегистрации.
    download_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upload_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    """Глобальные настройки приложения (key-value). Например, политика DHT/PEX/LSD."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
