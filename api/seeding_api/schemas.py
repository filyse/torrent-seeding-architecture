from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TorrentCreate(BaseModel):
    display_name: str = Field(default="", max_length=512)
    save_path: str = Field(..., min_length=1)
    magnet_uri: str = Field(..., min_length=12)


class EngineOut(BaseModel):
    id: str
    url: str
    storage_prefix: str
    listen_port: int | None = None


class TorrentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    info_hash: str | None
    magnet_uri: str | None
    display_name: str
    save_path: str
    engine_id: str
    status: str
    created_at: datetime


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


class TorrentDetailOut(TorrentOut):
    runtime: TorrentRuntimeOut | None = None
    peer_list: list[TorrentPeerOut] = Field(default_factory=list)
