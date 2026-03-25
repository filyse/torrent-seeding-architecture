from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TorrentCreate(BaseModel):
    display_name: str = Field(default="", max_length=512)
    save_path: str = Field(..., min_length=1)
    magnet_uri: str = Field(..., min_length=12)


class TorrentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    info_hash: str | None
    magnet_uri: str | None
    display_name: str
    save_path: str
    status: str
    created_at: datetime


class TorrentDetailOut(TorrentOut):
    runtime: dict[str, Any] | None = None
