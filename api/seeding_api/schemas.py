from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TorrentCreate(BaseModel):
    display_name: str = Field(default="", max_length=512)
    save_path: str = Field(default="")
    engine_id: str | None = Field(default=None, max_length=64)
    magnet_uri: str = Field(..., min_length=12)
    label: str = Field(default="", max_length=128)


class TorrentUrlCreate(BaseModel):
    url: str = Field(..., min_length=8)
    save_path: str = Field(default="")
    engine_id: str | None = Field(default=None, max_length=64)
    display_name: str = Field(default="", max_length=512)
    label: str = Field(default="", max_length=128)


class TorrentPatch(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=512)


class EngineOut(BaseModel):
    id: str
    url: str
    storage_prefix: str
    listen_port: int | None = None
    disk_total: int | None = None
    disk_free: int | None = None
    online: bool = True
    download_limit: int | None = None
    upload_limit: int | None = None


class EngineLimitsIn(BaseModel):
    download_limit: int | None = Field(default=None, ge=0)
    upload_limit: int | None = Field(default=None, ge=0)


class EngineRegistryItem(BaseModel):
    id: str
    url: str
    storage_prefix: str
    media_path: str | None = None
    listen_port: int | None = None
    enabled: bool = True
    last_seen: datetime | None = None
    age_seconds: int | None = None
    stale: bool = False
    in_pool: bool = False
    source: str = "dynamic"  # static | dynamic | static+dynamic


class EngineRegisterIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=32)
    url: str = Field(..., min_length=4)
    storage_prefix: str = Field(..., min_length=1)
    media_path: str | None = Field(default=None)
    listen_port: int | None = Field(default=None)


class BulkIdsIn(BaseModel):
    ids: list[int] = Field(..., min_length=1)


class BulkLabelIn(BaseModel):
    ids: list[int] = Field(..., min_length=1)
    label: str = Field(default="", max_length=128)


class TrackerAddIn(BaseModel):
    url: str = Field(..., min_length=8)


class SessionLimitsIn(BaseModel):
    download_limit: int | None = Field(default=None, ge=0)
    upload_limit: int | None = Field(default=None, ge=0)
    engine_id: str | None = None


class TorrentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    info_hash: str | None
    magnet_uri: str | None
    display_name: str
    save_path: str
    engine_id: str
    label: str
    status: str
    created_at: datetime


class CreatorBrowseItem(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float


class CreatorTaskCreate(BaseModel):
    engine_id: str = Field(..., min_length=1, max_length=64)
    source_path: str = Field(..., min_length=1)
    skip_episode_check: bool = False


class CreatorTaskOut(BaseModel):
    engine_id: str
    id: int
    source_path: str
    save_path: str
    status: str
    progress: int
    message: str
    error: str | None = None
    name: str
    file_count: int
    created_at: float
    updated_at: float
    has_torrent: bool


class CreatorSeedIn(BaseModel):
    label: str = Field(default="", max_length=128)
    display_name: str = Field(default="", max_length=512)


class TorrentPeerOut(BaseModel):
    endpoint: str
    client: str | None = None
    progress: float | None = None
    download_rate: int | None = None
    upload_rate: int | None = None
    flags: str | None = None
    source: str | None = None


class TorrentRuntimeOut(BaseModel):
    db_id: int
    magnet_uri: str | None = None
    save_path: str
    runtime_status: str
    info_hash: str | None = None
    progress: float | None = None
    lt_state: str | None = None
    download_rate: int | None = None
    upload_rate: int | None = None
    total_uploaded: int | None = None
    peers: int | None = None
    name: str | None = None
    size: int | None = None
    downloaded: int | None = None
    num_seeds: int | None = None
    ratio: float | None = None
    eta: int | None = None
    added_time: int | None = None
    download_limit: int | None = None
    upload_limit: int | None = None
    private: bool | None = None


class TorrentFileOut(BaseModel):
    index: int
    path: str
    size: int
    downloaded: int
    progress: float
    priority: int


class TorrentTrackerOut(BaseModel):
    url: str
    tier: int = 0
    message: str = ""
    verified: bool = False
    num_peers: int = 0


class FilePrioritiesIn(BaseModel):
    priorities: dict[int, int]


class LimitsIn(BaseModel):
    download_limit: int | None = Field(default=None, ge=0)
    upload_limit: int | None = Field(default=None, ge=0)


class PrivateIn(BaseModel):
    enabled: bool | None = None  # None = автоопределение по флагу/passkey


class NetSettingsIn(BaseModel):
    dht: bool | None = None
    pex: bool | None = None
    lsd: bool | None = None


class NetSettingsOut(BaseModel):
    dht: bool
    pex: bool
    lsd: bool
    applied: int | None = None
    errors: int | None = None


class BatchUploadItem(BaseModel):
    filename: str
    ok: bool
    id: int | None = None
    display_name: str | None = None
    error: str | None = None


class BatchUploadResult(BaseModel):
    total: int
    ok: int
    failed: int
    items: list[BatchUploadItem]


class UpdateMatchRequest(BaseModel):
    """Поиск существующих раздач для замены по именам новых .torrent-файлов."""

    filenames: list[str] = Field(default_factory=list)


class UpdateMatchItem(BaseModel):
    filename: str
    candidates: list[TorrentOut] = Field(default_factory=list)


class UpdateMatchResult(BaseModel):
    items: list[UpdateMatchItem]


class TorrentDetailOut(TorrentOut):
    runtime: TorrentRuntimeOut | None = None
    peer_list: list[TorrentPeerOut] = Field(default_factory=list)


class TorrentPageOut(BaseModel):
    """Страница списка раздач: элементы + общее число подходящих под фильтр."""

    items: list[TorrentDetailOut]
    total: int
    limit: int
    offset: int


class TorrentFacetsOut(BaseModel):
    """Счётчики для подписи количества у каждого варианта фильтра."""

    total: int
    statuses: dict[str, int]
    labels: dict[str, int]
    engines: dict[str, int]
    # Суммарный объём раздач по движку (в байтах) — для подписи рядом со счётчиком.
    engine_sizes: dict[str, int]
    states: dict[str, int]
