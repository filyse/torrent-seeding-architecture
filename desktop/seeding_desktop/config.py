import json
import os
from pathlib import Path

DEFAULT_API_BASE = "http://127.0.0.1:8000"


def config_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "TorrentSeeding"
    return Path.home() / ".config" / "TorrentSeeding"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_api_base_url() -> str:
    path = config_path()
    if not path.is_file():
        return DEFAULT_API_BASE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_API_BASE
    url = data.get("api_base_url")
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip("/")
    return DEFAULT_API_BASE
