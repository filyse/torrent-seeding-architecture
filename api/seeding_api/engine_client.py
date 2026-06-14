import httpx
import base64


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

    async def register_torrent_file(self, db_id: int, torrent_bytes: bytes, save_path: str) -> dict:
        r = await self._client.post(
            "/internal/v1/torrents",
            json={
                "db_id": db_id,
                "magnet_uri": None,
                "torrent_b64": base64.b64encode(torrent_bytes).decode("ascii"),
                "save_path": save_path,
            },
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

    async def restore_from_disk(self, db_id: int, save_path: str) -> dict | None:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/restore-from-disk",
            params={"save_path": save_path},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def runtime_snapshot(self, db_id: int) -> dict | None:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def list_peers(self, db_id: int) -> list[dict]:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/peers")
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def list_files(self, db_id: int) -> list[dict]:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/files")
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def set_file_priorities(self, db_id: int, priorities: dict[int, int]) -> bool:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/files/priorities",
            json={"priorities": {str(k): v for k, v in priorities.items()}},
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    async def list_trackers(self, db_id: int) -> list[dict]:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/trackers")
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def recheck(self, db_id: int) -> bool:
        r = await self._client.post(f"/internal/v1/torrents/{db_id}/recheck")
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    async def reannounce(self, db_id: int) -> bool:
        r = await self._client.post(f"/internal/v1/torrents/{db_id}/reannounce")
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    async def set_limits(
        self, db_id: int, download_limit: int | None, upload_limit: int | None
    ) -> dict | None:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/limits",
            json={"download_limit": download_limit, "upload_limit": upload_limit},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def remove_from_runtime(
        self,
        db_id: int,
        *,
        delete_files: bool = False,
        save_path: str | None = None,
        display_name: str | None = None,
    ) -> bool:
        params: dict[str, str | bool] = {}
        if delete_files:
            params["delete_files"] = True
        if save_path:
            params["save_path"] = save_path
        if display_name:
            params["display_name"] = display_name
        r = await self._client.delete(
            f"/internal/v1/torrents/{db_id}",
            params=params or None,
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True
