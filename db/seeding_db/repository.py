from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from seeding_db.models import TorrentRecord, TorrentStatus


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
    ) -> TorrentRecord:
        row = TorrentRecord(
            display_name=display_name,
            save_path=save_path,
            magnet_uri=magnet_uri,
            info_hash=info_hash,
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

    async def delete(self, torrent_id: int) -> bool:
        row = await self.get_by_id(torrent_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
