import httpx


class EngineClient:
    """HTTP-клиент к внутреннему API движка."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base, timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        r = await self._client.get("/health")
        r.raise_for_status()
        return r.json()

    async def register_torrent(self, db_id: int, magnet_uri: str | None, save_path: str) -> dict:
        r = await self._client.post(
            "/internal/v1/torrents",
            json={"db_id": db_id, "magnet_uri": magnet_uri, "save_path": save_path},
        )
        r.raise_for_status()
        return r.json()

    async def pause(self, db_id: int) -> dict:
        r = await self._client.post(f"/internal/v1/torrents/{db_id}/pause")
        r.raise_for_status()
        return r.json()

    async def resume(self, db_id: int) -> dict:
        r = await self._client.post(f"/internal/v1/torrents/{db_id}/resume")
        r.raise_for_status()
        return r.json()

    async def runtime_snapshot(self, db_id: int) -> dict | None:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def remove_from_runtime(self, db_id: int) -> bool:
        r = await self._client.delete(f"/internal/v1/torrents/{db_id}")
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True
