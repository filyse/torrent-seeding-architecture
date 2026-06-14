from __future__ import annotations

import argparse
from pathlib import Path

import httpx

from seeding_desktop.config import load_api_base_url
from seeding_desktop.http_util import print_json_body, print_response_error, request_json


def cmd_list(client: httpx.Client) -> int:
    return request_json(client, "GET", "/api/v1/torrents")


def cmd_add(client: httpx.Client, magnet: str, save_path: str, display_name: str) -> int:
    body = {"magnet_uri": magnet.strip(), "save_path": save_path.strip(), "display_name": display_name.strip()}
    return request_json(client, "POST", "/api/v1/torrents", json_body=body)


def cmd_add_file(client: httpx.Client, torrent_file: str, save_path: str, display_name: str) -> int:
    p = Path(torrent_file).expanduser()
    if not p.is_file():
        print(f"error: file not found: {p}")
        return 1
    try:
        payload = p.read_bytes()
    except OSError as exc:
        print(f"error: cannot read file: {exc}")
        return 1
    data = {"save_path": save_path.strip(), "display_name": display_name.strip()}
    files = {"torrent_file": (p.name, payload, "application/x-bittorrent")}
    try:
        r = client.post("/api/v1/torrents/upload", data=data, files=files)
    except httpx.HTTPError as exc:
        print(f"error: {exc}")
        return 1
    if not r.is_success:
        print_response_error(r)
        return 1
    if r.content:
        try:
            print_json_body(r.json())
        except ValueError:
            print(r.text)
    return 0


def cmd_get(client: httpx.Client, torrent_id: int) -> int:
    return request_json(client, "GET", f"/api/v1/torrents/{torrent_id}")


def cmd_pause(client: httpx.Client, torrent_id: int) -> int:
    return request_json(client, "POST", f"/api/v1/torrents/{torrent_id}/pause")


def cmd_resume(client: httpx.Client, torrent_id: int) -> int:
    return request_json(client, "POST", f"/api/v1/torrents/{torrent_id}/resume")


def cmd_remove(client: httpx.Client, torrent_id: int) -> int:
    return request_json(client, "DELETE", f"/api/v1/torrents/{torrent_id}")


def main() -> None:
    p = argparse.ArgumentParser(prog="seeding-desktop", description="Torrent seeding — CLI к публичному API")
    p.add_argument(
        "--api-base",
        default=None,
        help="переопределить base URL API (иначе из config или 127.0.0.1:8000)",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="заголовок X-API-Key (если на сервере задано SEEDING_API_KEYS)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="GET /api/v1/torrents")

    p_add = sub.add_parser("add", help="POST /api/v1/torrents (magnet + save_path)")
    p_add.add_argument("--magnet", required=True, help="magnet:?xt=urn:btih:…")
    p_add.add_argument("--save-path", required=True, help="каталог на стороне движка, напр. /data")
    p_add.add_argument("--display-name", default="", help="необязательно")
    p_add_file = sub.add_parser("add-file", help="POST /api/v1/torrents/upload (.torrent + save_path)")
    p_add_file.add_argument("--torrent-file", required=True, help="путь до .torrent на локальном ПК")
    p_add_file.add_argument("--save-path", required=True, help="каталог на стороне движка, напр. /data")
    p_add_file.add_argument("--display-name", default="", help="необязательно")

    p_get = sub.add_parser("get", help="GET /api/v1/torrents/{id} (+ runtime)")
    p_get.add_argument("id", type=int, help="id торрента в БД")

    p_pause = sub.add_parser("pause", help="POST …/pause")
    p_pause.add_argument("id", type=int)

    p_resume = sub.add_parser("resume", help="POST …/resume")
    p_resume.add_argument("id", type=int)

    p_remove = sub.add_parser("remove", help="DELETE /api/v1/torrents/{id}")
    p_remove.add_argument("id", type=int)

    args = p.parse_args()
    base = (args.api_base or load_api_base_url()).rstrip("/")
    headers = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    code = 2
    with httpx.Client(base_url=base, timeout=60.0, headers=headers) as client:
        if args.cmd == "list":
            code = cmd_list(client)
        elif args.cmd == "add":
            code = cmd_add(client, args.magnet, args.save_path, args.display_name)
        elif args.cmd == "add-file":
            code = cmd_add_file(client, args.torrent_file, args.save_path, args.display_name)
        elif args.cmd == "get":
            code = cmd_get(client, args.id)
        elif args.cmd == "pause":
            code = cmd_pause(client, args.id)
        elif args.cmd == "resume":
            code = cmd_resume(client, args.id)
        elif args.cmd == "remove":
            code = cmd_remove(client, args.id)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
