import json
import os
from pathlib import Path
from typing import Any

DEFAULT_API_BASE = "http://127.0.0.1:8000"


def config_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "TorrentSeeding"
    return Path.home() / ".config" / "TorrentSeeding"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def update_config(**values: Any) -> dict[str, Any]:
    """Слить переданные ключи в конфиг и сохранить. Возвращает актуальный конфиг."""
    data = load_config()
    data.update({k: v for k, v in values.items() if v is not None})
    save_config(data)
    return data


def load_api_base_url() -> str:
    url = load_config().get("api_base_url")
    if isinstance(url, str) and url.strip():
        return url.strip().rstrip("/")
    return DEFAULT_API_BASE


def load_api_key() -> str:
    key = load_config().get("api_key")
    return key.strip() if isinstance(key, str) else ""
