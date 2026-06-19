import base64
import contextlib
import os
from collections.abc import AsyncIterator

import httpx


def _retries() -> int:
    try:
        return max(0, int(os.getenv("SEEDING_ENGINE_HTTP_RETRIES", "2")))
    except ValueError:
        return 2


def _api_token() -> str:
    return os.getenv("SEEDING_ENGINE_API_TOKEN", "").strip()


def _verify_for(base_url: str):
    """Как проверять TLS-сертификат движка.

    Для http — неважно. Для https: если задан SEEDING_ENGINE_TLS_CA (наш приватный CA) —
    проверяем по нему; иначе проверяем по системным корневым (verify=True)."""
    if not base_url.startswith("https://"):
        return True
    ca = os.getenv("SEEDING_ENGINE_TLS_CA", "").strip()
    return ca if ca else True


def _client_cert():
    """Опциональный клиентский сертификат для mTLS (если оркестратор должен предъявлять cert)."""
    crt = os.getenv("SEEDING_ENGINE_TLS_CLIENT_CERT", "").strip()
    key = os.getenv("SEEDING_ENGINE_TLS_CLIENT_KEY", "").strip()
    if crt and key:
        return (crt, key)
    if crt:
        return crt
    return None


class EngineClient:
    """HTTP-клиент к внутреннему API движка.

    Транспорт переподключается при сетевых сбоях (движок перезапускается/недоступен):
    httpx.AsyncHTTPTransport(retries=N) повторяет установку соединения с экспоненциальным
    backoff. Кол-во ретраев — `SEEDING_ENGINE_HTTP_RETRIES` (по умолчанию 2)."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        transport = httpx.AsyncHTTPTransport(
            retries=_retries(),
            verify=_verify_for(self._base),
            cert=_client_cert(),
        )
        token = _api_token()
        headers = {"X-Engine-Token": token} if token else None
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(30.0),
            transport=transport,
            headers=headers,
        )

    @property
    def base_url(self) -> str:
        return self._base

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

    async def register_torrent_file(
        self, db_id: int, torrent_bytes: bytes, save_path: str, seed_mode: bool = False
    ) -> dict:
        r = await self._client.post(
            "/internal/v1/torrents",
            json={
                "db_id": db_id,
                "magnet_uri": None,
                "torrent_b64": base64.b64encode(torrent_bytes).decode("ascii"),
                "save_path": save_path,
                "seed_mode": seed_mode,
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

    async def get_torrent_file(self, db_id: int) -> bytes | None:
        """Скачать сохранённый .torrent с движка (для переноса на другой движок)."""
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/torrent-file")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        b64 = data.get("torrent_b64") if isinstance(data, dict) else None
        return base64.b64decode(b64) if b64 else None

    async def import_local(
        self,
        db_id: int,
        torrent_bytes: bytes,
        save_path: str,
        src_content_path: str,
    ) -> dict:
        """Импорт раздачи на этот движок копированием контента из /media (перенос b→b).

        Копирование контента может занять минуты — отдельный длинный таймаут вместо
        дефолтных 30s, чтобы не оборвать перенос крупной раздачи."""
        r = await self._client.post(
            "/internal/v1/torrents/import-local",
            json={
                "db_id": db_id,
                "torrent_b64": base64.b64encode(torrent_bytes).decode("ascii"),
                "save_path": save_path,
                "src_content_path": src_content_path,
            },
            timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
        )
        r.raise_for_status()
        return r.json()

    async def path_exists(self, path: str) -> bool:
        """Видит ли движок путь (общий /media) — факт-проверка для авто-выбора транспорта."""
        r = await self._client.get("/internal/v1/fs/exists", params={"path": path})
        r.raise_for_status()
        data = r.json()
        return bool(data.get("exists")) if isinstance(data, dict) else False

    @contextlib.asynccontextmanager
    async def stream_content(self, db_id: int):
        """Открыть потоковую выгрузку контента (tar) с движка-источника.

        Возвращает (response, content_total). Контекст держит соединение открытым,
        пока оркестратор перекачивает байты в движок-приёмник."""
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        async with self._client.stream(
            "GET", f"/internal/v1/torrents/{db_id}/content", timeout=timeout
        ) as resp:
            resp.raise_for_status()
            try:
                total = int(resp.headers.get("X-Content-Total", "0"))
            except ValueError:
                total = 0
            yield resp, total

    async def stage_remote(
        self, db_id: int, torrent_bytes: bytes, save_path: str, content_total: int
    ) -> dict:
        r = await self._client.post(
            "/internal/v1/torrents/stage-remote",
            json={
                "db_id": db_id,
                "save_path": save_path,
                "torrent_b64": base64.b64encode(torrent_bytes).decode("ascii"),
                "content_total": int(content_total),
            },
        )
        r.raise_for_status()
        return r.json()

    async def import_remote(self, db_id: int, content_iter: AsyncIterator[bytes]) -> dict:
        """Отправить tar-поток контента движку-приёмнику телом запроса (после stage-remote)."""
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/import-remote",
            content=content_iter,
            headers={"Content-Type": "application/x-tar"},
            timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
        )
        r.raise_for_status()
        return r.json()

    async def peer_check(self, source_url: str) -> bool:
        """Спросить движок, видит ли он другой движок напрямую (для авто-выбора direct)."""
        r = await self._client.get("/internal/v1/peer-check", params={"url": source_url})
        if r.status_code >= 400:
            return False
        data = r.json()
        return bool(data.get("reachable")) if isinstance(data, dict) else False

    async def import_direct(
        self, db_id: int, torrent_bytes: bytes, save_path: str, source_url: str
    ) -> dict:
        """Импорт раздачи прямым pull у движка-источника (минуя оркестратор по данным)."""
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/import-direct",
            json={
                "source_url": source_url,
                "save_path": save_path,
                "torrent_b64": base64.b64encode(torrent_bytes).decode("ascii"),
            },
            timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
        )
        r.raise_for_status()
        return r.json()

    async def content_manifest(self, db_id: int) -> dict | None:
        """Манифест контента источника (root + файлы) для возобновляемого переноса."""
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/content-manifest")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    @contextlib.asynccontextmanager
    async def stream_content_file(self, db_id: int, path: str, offset: int = 0):
        """Открыть потоковую выгрузку одного файла контента с движка-источника (с Range)."""
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        headers = {"Range": f"bytes={int(offset)}-"} if offset > 0 else {}
        async with self._client.stream(
            "GET",
            f"/internal/v1/torrents/{db_id}/content-file",
            params={"path": path},
            headers=headers,
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            yield resp

    async def import_file_size(self, db_id: int, save_path: str, root: str, path: str) -> int:
        r = await self._client.get(
            f"/internal/v1/torrents/{db_id}/import-file-size",
            params={"save_path": save_path, "root": root, "path": path},
        )
        r.raise_for_status()
        data = r.json()
        return int(data.get("size", 0)) if isinstance(data, dict) else 0

    async def import_file_append(
        self,
        db_id: int,
        save_path: str,
        root: str,
        path: str,
        offset: int,
        content_iter: AsyncIterator[bytes],
    ) -> int:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/import-file",
            params={"save_path": save_path, "root": root, "path": path, "offset": int(offset)},
            content=content_iter,
            headers={"Content-Type": "application/octet-stream"},
            timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
        )
        r.raise_for_status()
        data = r.json()
        return int(data.get("written", 0)) if isinstance(data, dict) else 0

    async def import_finalize(self, db_id: int) -> dict:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/import-finalize",
            timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
        )
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

    async def list_runtime(self) -> dict[int, dict]:
        """Все рантайм-снапшоты движка одним запросом: {db_id: handle}.

        Нужно для списка раздач, чтобы не дёргать движок по одному торренту (N+1)."""
        r = await self._client.get("/internal/v1/torrents")
        r.raise_for_status()
        data = r.json()
        out: dict[int, dict] = {}
        if isinstance(data, list):
            for h in data:
                did = h.get("db_id") if isinstance(h, dict) else None
                if did is not None:
                    out[int(did)] = h
        return out

    async def get_migrate_progress(self, db_id: int) -> dict | None:
        r = await self._client.get(f"/internal/v1/torrents/{db_id}/migrate-progress")
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

    async def get_net_settings(self) -> dict | None:
        r = await self._client.get("/internal/v1/session/net-settings")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def set_net_settings(
        self, dht: bool | None, pex: bool | None, lsd: bool | None
    ) -> dict | None:
        r = await self._client.post(
            "/internal/v1/session/net-settings",
            json={"dht": dht, "pex": pex, "lsd": lsd},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def set_private(self, db_id: int, enabled: bool | None) -> dict | None:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/private",
            json={"enabled": enabled},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def add_tracker(self, db_id: int, url: str) -> list[dict]:
        r = await self._client.post(
            f"/internal/v1/torrents/{db_id}/trackers",
            json={"url": url},
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def remove_tracker(self, db_id: int, url: str) -> list[dict]:
        r = await self._client.delete(
            f"/internal/v1/torrents/{db_id}/trackers",
            params={"url": url},
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def session_stats(self) -> dict:
        r = await self._client.get("/internal/v1/session/stats")
        r.raise_for_status()
        return r.json()

    async def net_status(self) -> dict:
        r = await self._client.get("/internal/v1/net/status")
        r.raise_for_status()
        return r.json()

    async def sysinfo(self) -> dict:
        r = await self._client.get("/internal/v1/sysinfo", timeout=httpx.Timeout(12.0))
        r.raise_for_status()
        return r.json()

    async def set_session_limits(
        self, download_limit: int | None, upload_limit: int | None
    ) -> dict:
        r = await self._client.post(
            "/internal/v1/session/limits",
            json={"download_limit": download_limit, "upload_limit": upload_limit},
        )
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
