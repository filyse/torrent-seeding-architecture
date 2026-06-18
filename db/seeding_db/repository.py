from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from seeding_db.models import (
    ApiKeyRecord,
    AppSetting,
    AuditRecord,
    EngineRecord,
    LabelQuota,
    MigrationJob,
    SessionRecord,
    TorrentMeter,
    TorrentRecord,
    TorrentStatus,
    UserRecord,
)


class TorrentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        display_name: str,
        save_path: str,
        magnet_uri: str | None = None,
        info_hash: str | None = None,
        status: str | None = None,
        engine_id: str = "default",
        label: str = "",
    ) -> TorrentRecord:
        row = TorrentRecord(
            display_name=display_name,
            save_path=save_path,
            magnet_uri=magnet_uri,
            info_hash=info_hash,
            engine_id=engine_id,
            label=label,
        )
        if status is not None:
            row.status = status
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, torrent_id: int) -> TorrentRecord | None:
        return await self._session.get(TorrentRecord, torrent_id)

    async def list_all(self) -> list[TorrentRecord]:
        result = await self._session.execute(select(TorrentRecord).order_by(TorrentRecord.id))
        return list(result.scalars())

    async def count_by_status(self) -> dict[str, int]:
        result = await self._session.execute(
            select(TorrentRecord.status, func.count()).group_by(TorrentRecord.status)
        )
        return {str(status): int(count) for status, count in result.all()}

    async def list_by_engine(self, engine_id: str) -> list[TorrentRecord]:
        stmt = (
            select(TorrentRecord)
            .where(TorrentRecord.engine_id == engine_id)
            .order_by(TorrentRecord.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_queued_for_engine(self, engine_id: str) -> list[TorrentRecord]:
        stmt = (
            select(TorrentRecord)
            .where(
                TorrentRecord.engine_id == engine_id,
                TorrentRecord.status == TorrentStatus.queued.value,
            )
            .order_by(TorrentRecord.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_for_engine_restore(self) -> list[TorrentRecord]:
        """Торренты, которые должны быть в рантайме движка после перезапуска."""
        active = (
            TorrentStatus.downloading.value,
            TorrentStatus.seeding.value,
            TorrentStatus.paused.value,
        )
        stmt = (
            select(TorrentRecord)
            .where(
                TorrentRecord.status.in_(active),
                TorrentRecord.magnet_uri.isnot(None),
                TorrentRecord.magnet_uri != "",
                TorrentRecord.save_path != "",
            )
            .order_by(TorrentRecord.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_for_torrent_file_restore(self) -> list[TorrentRecord]:
        """Активные торренты, добавленные через .torrent (без magnet в БД)."""
        active = (
            TorrentStatus.downloading.value,
            TorrentStatus.seeding.value,
            TorrentStatus.paused.value,
        )
        stmt = (
            select(TorrentRecord)
            .where(
                TorrentRecord.status.in_(active),
                TorrentRecord.save_path != "",
                (TorrentRecord.magnet_uri.is_(None)) | (TorrentRecord.magnet_uri == ""),
            )
            .order_by(TorrentRecord.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def update_status(self, torrent_id: int, status: str) -> TorrentRecord | None:
        row = await self.get_by_id(torrent_id)
        if row is None:
            return None
        row.status = status
        await self._session.flush()
        return row

    async def update_info_hash(self, torrent_id: int, info_hash: str) -> TorrentRecord | None:
        row = await self.get_by_id(torrent_id)
        if row is None:
            return None
        row.info_hash = info_hash
        await self._session.flush()
        return row

    async def update_engine(
        self, torrent_id: int, engine_id: str, save_path: str
    ) -> TorrentRecord | None:
        """Сменить движок/путь раздачи (после успешного переноса между движками)."""
        row = await self.get_by_id(torrent_id)
        if row is None:
            return None
        row.engine_id = engine_id
        row.save_path = save_path
        await self._session.flush()
        return row

    async def update_label(self, torrent_id: int, label: str) -> TorrentRecord | None:
        row = await self.get_by_id(torrent_id)
        if row is None:
            return None
        row.label = label
        await self._session.flush()
        return row

    async def list_labels(self) -> list[str]:
        result = await self._session.execute(
            select(TorrentRecord.label).where(TorrentRecord.label != "").distinct()
        )
        return sorted({r for r in result.scalars() if r})

    async def get_by_ids(self, torrent_ids: list[int]) -> list[TorrentRecord]:
        if not torrent_ids:
            return []
        result = await self._session.execute(
            select(TorrentRecord).where(TorrentRecord.id.in_(torrent_ids))
        )
        return list(result.scalars())

    async def delete(self, torrent_id: int) -> bool:
        row = await self.get_by_id(torrent_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


class ApiKeyRepository:
    """Именованные API-ключи с ролями (Фаза 5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, name: str, key_hash: str, prefix: str, role: str) -> ApiKeyRecord:
        row = ApiKeyRecord(name=name, key_hash=key_hash, prefix=prefix, role=role, enabled=True)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_hash(self, key_hash: str) -> ApiKeyRecord | None:
        result = await self._session.execute(
            select(ApiKeyRecord).where(ApiKeyRecord.key_hash == key_hash)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, key_id: int) -> ApiKeyRecord | None:
        return await self._session.get(ApiKeyRecord, key_id)

    async def list_all(self) -> list[ApiKeyRecord]:
        result = await self._session.execute(select(ApiKeyRecord).order_by(ApiKeyRecord.id))
        return list(result.scalars())

    async def count_enabled(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(ApiKeyRecord).where(ApiKeyRecord.enabled.is_(True))
        )
        return int(result.scalar_one() or 0)

    async def count_admins(self, *, exclude_id: int | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(ApiKeyRecord)
            .where(ApiKeyRecord.enabled.is_(True), ApiKeyRecord.role == "admin")
        )
        if exclude_id is not None:
            stmt = stmt.where(ApiKeyRecord.id != exclude_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def update(
        self, key_id: int, *, role: str | None = None, enabled: bool | None = None
    ) -> ApiKeyRecord | None:
        row = await self.get_by_id(key_id)
        if row is None:
            return None
        if role is not None:
            row.role = role
        if enabled is not None:
            row.enabled = enabled
        await self._session.flush()
        return row

    async def delete(self, key_id: int) -> bool:
        row = await self.get_by_id(key_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def touch(self, key_id: int) -> None:
        row = await self.get_by_id(key_id)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
            await self._session.flush()


class UserRepository:
    """Пользователи с логином/паролем (Фаза 5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, username: str, password_hash: str, role: str) -> UserRecord:
        row = UserRecord(
            username=username, password_hash=password_hash, role=role, enabled=True
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_username(self, username: str) -> UserRecord | None:
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> UserRecord | None:
        return await self._session.get(UserRecord, user_id)

    async def primary_id(self) -> int | None:
        """ID самого первого (основного) аккаунта — его роль/доступ защищены."""
        result = await self._session.execute(select(func.min(UserRecord.id)))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[UserRecord]:
        result = await self._session.execute(select(UserRecord).order_by(UserRecord.id))
        return list(result.scalars())

    async def count_admins(self, *, exclude_id: int | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(UserRecord)
            .where(UserRecord.enabled.is_(True), UserRecord.role == "admin")
        )
        if exclude_id is not None:
            stmt = stmt.where(UserRecord.id != exclude_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def update(
        self,
        user_id: int,
        *,
        role: str | None = None,
        enabled: bool | None = None,
        password_hash: str | None = None,
    ) -> UserRecord | None:
        row = await self.get_by_id(user_id)
        if row is None:
            return None
        if role is not None:
            row.role = role
        if enabled is not None:
            row.enabled = enabled
        if password_hash is not None:
            row.password_hash = password_hash
        await self._session.flush()
        return row

    async def delete(self, user_id: int) -> bool:
        row = await self.get_by_id(user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def touch_login(self, user_id: int) -> None:
        row = await self.get_by_id(user_id)
        if row is not None:
            row.last_login_at = datetime.now(timezone.utc)
            await self._session.flush()


class SessionRepository:
    """Сессии входа (Фаза 5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, token_hash: str, user_id: int, username: str, role: str, expires_at: datetime
    ) -> SessionRecord:
        row = SessionRecord(
            token_hash=token_hash,
            user_id=user_id,
            username=username,
            role=role,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_hash(self, token_hash: str) -> SessionRecord | None:
        result = await self._session.execute(
            select(SessionRecord).where(SessionRecord.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def delete_by_hash(self, token_hash: str) -> bool:
        row = await self.get_by_hash(token_hash)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def delete_for_user(self, user_id: int) -> None:
        rows = await self._session.execute(
            select(SessionRecord).where(SessionRecord.user_id == user_id)
        )
        for row in rows.scalars():
            await self._session.delete(row)
        await self._session.flush()

    async def touch(self, session_id: int) -> None:
        row = await self._session.get(SessionRecord, session_id)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
            await self._session.flush()


class AuditRepository:
    """Аудит-лог действий (Фаза 5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        actor: str,
        role: str,
        method: str,
        path: str,
        status: int,
        ip: str,
        summary: str,
    ) -> None:
        self._session.add(
            AuditRecord(
                actor=actor[:64],
                role=role[:16],
                method=method[:8],
                path=path,
                status=status,
                ip=ip[:64],
                summary=summary,
            )
        )
        await self._session.flush()

    async def list_recent(
        self, *, limit: int = 200, actor: str | None = None
    ) -> list[AuditRecord]:
        stmt = select(AuditRecord).order_by(AuditRecord.id.desc())
        if actor:
            stmt = stmt.where(AuditRecord.actor == actor)
        stmt = stmt.limit(max(1, min(limit, 1000)))
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def trim_older_than(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        result = await self._session.execute(
            sa_delete(AuditRecord).where(AuditRecord.created_at < cutoff)
        )
        await self._session.flush()
        return int(result.rowcount or 0)


class MigrationRepository:
    """Состояние переносов между движками (Фаза 4, возобновляемость)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, torrent_id: int) -> MigrationJob | None:
        return await self._session.get(MigrationJob, torrent_id)

    async def list_active(self) -> list[MigrationJob]:
        result = await self._session.execute(
            select(MigrationJob).where(MigrationJob.state.in_(["running", "failed"]))
        )
        return list(result.scalars())

    async def upsert(self, torrent_id: int, **fields) -> MigrationJob:
        row = await self._session.get(MigrationJob, torrent_id)
        if row is None:
            row = MigrationJob(torrent_id=torrent_id)
            self._session.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def set_progress(
        self, torrent_id: int, *, phase: str, copied: int, total: int
    ) -> None:
        row = await self._session.get(MigrationJob, torrent_id)
        if row is None:
            return
        row.phase = phase
        row.copied = int(copied)
        row.total = int(total)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def set_state(
        self, torrent_id: int, state: str, *, phase: str | None = None, error: str | None = None
    ) -> None:
        row = await self._session.get(MigrationJob, torrent_id)
        if row is None:
            return
        row.state = state
        if phase is not None:
            row.phase = phase
        if error is not None:
            row.last_error = error[:500]
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def bump_attempts(self, torrent_id: int) -> None:
        row = await self._session.get(MigrationJob, torrent_id)
        if row is None:
            return
        row.attempts = int(row.attempts or 0) + 1
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def delete(self, torrent_id: int) -> bool:
        row = await self._session.get(MigrationJob, torrent_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


class QuotaRepository:
    """Квоты по объёму на метку + счётчики отданного по торрентам (Фаза 5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- квоты ---
    async def list_quotas(self) -> list[LabelQuota]:
        result = await self._session.execute(select(LabelQuota).order_by(LabelQuota.label))
        return list(result.scalars())

    async def get_quota(self, label: str) -> LabelQuota | None:
        return await self._session.get(LabelQuota, label)

    async def upsert_quota(
        self, label: str, *, upload_quota: int | None, enabled: bool
    ) -> LabelQuota:
        row = await self._session.get(LabelQuota, label)
        if row is None:
            row = LabelQuota(label=label, uploaded_total=0)
            self._session.add(row)
        row.upload_quota = upload_quota if (upload_quota and upload_quota > 0) else None
        row.enabled = enabled
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def add_uploaded(self, label: str, delta: int) -> None:
        row = await self._session.get(LabelQuota, label)
        if row is None or delta <= 0:
            return
        row.uploaded_total = int(row.uploaded_total or 0) + int(delta)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def set_exceeded(self, label: str, exceeded: bool, paused_ids: str) -> None:
        row = await self._session.get(LabelQuota, label)
        if row is None:
            return
        row.exceeded = exceeded
        row.paused_ids = paused_ids
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def reset_quota(self, label: str) -> LabelQuota | None:
        row = await self._session.get(LabelQuota, label)
        if row is None:
            return None
        row.uploaded_total = 0
        row.exceeded = False
        row.paused_ids = ""
        row.since = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return row

    async def delete_quota(self, label: str) -> bool:
        row = await self._session.get(LabelQuota, label)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # --- счётчики по торрентам ---
    async def get_meters(self) -> dict[int, int]:
        result = await self._session.execute(select(TorrentMeter))
        return {m.torrent_id: int(m.last_uploaded or 0) for m in result.scalars()}

    async def set_meter(self, torrent_id: int, value: int) -> None:
        row = await self._session.get(TorrentMeter, torrent_id)
        if row is None:
            row = TorrentMeter(torrent_id=torrent_id)
            self._session.add(row)
        row.last_uploaded = int(value)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()


class EngineRepository:
    """Динамический реестр движков в БД (Фаза 4.5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        engine_id: str,
        url: str,
        storage_prefix: str,
        media_path: str | None = None,
        listen_port: int | None = None,
    ) -> EngineRecord:
        """Зарегистрировать/обновить движок и отметить его «живым» (last_seen=now)."""
        row = await self._session.get(EngineRecord, engine_id)
        if row is None:
            row = EngineRecord(id=engine_id)
            self._session.add(row)
        row.url = url
        row.storage_prefix = storage_prefix
        row.media_path = media_path or None
        row.listen_port = listen_port
        row.enabled = True
        row.last_seen = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_all(self) -> list[EngineRecord]:
        result = await self._session.execute(select(EngineRecord).order_by(EngineRecord.id))
        return list(result.scalars())

    async def list_enabled(self) -> list[EngineRecord]:
        result = await self._session.execute(
            select(EngineRecord).where(EngineRecord.enabled.is_(True)).order_by(EngineRecord.id)
        )
        return list(result.scalars())

    async def set_enabled(self, engine_id: str, enabled: bool) -> EngineRecord | None:
        row = await self._session.get(EngineRecord, engine_id)
        if row is None:
            return None
        row.enabled = enabled
        await self._session.flush()
        return row

    async def set_limits(
        self, engine_id: str, download_limit: int | None, upload_limit: int | None
    ) -> EngineRecord | None:
        """Сохранить постоянные лимиты движка. Значение <= 0 трактуем как «без лимита» (NULL)."""
        row = await self._session.get(EngineRecord, engine_id)
        if row is None:
            return None
        row.download_limit = download_limit if (download_limit and download_limit > 0) else None
        row.upload_limit = upload_limit if (upload_limit and upload_limit > 0) else None
        await self._session.flush()
        return row

    async def touch(self, engine_id: str) -> None:
        row = await self._session.get(EngineRecord, engine_id)
        if row is not None:
            row.last_seen = datetime.now(timezone.utc)
            await self._session.flush()


class SettingsRepository:
    """Глобальные настройки приложения (key-value)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> str | None:
        row = await self._session.get(AppSetting, key)
        return row.value if row is not None else None

    async def set(self, key: str, value: str) -> None:
        row = await self._session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value, updated_at=datetime.now(timezone.utc))
            self._session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
