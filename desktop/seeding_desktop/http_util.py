from __future__ import annotations

import json
import sys
from typing import Any

import httpx


def print_response_error(r: httpx.Response) -> None:
    try:
        body = r.json()
    except json.JSONDecodeError:
        print(f"HTTP {r.status_code}: {r.text or r.reason_phrase}", file=sys.stderr)
        return
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("message", r.reason_phrase)
        print(f"HTTP {r.status_code}: {msg}", file=sys.stderr)
    else:
        print(f"HTTP {r.status_code}: {body}", file=sys.stderr)


def print_json_body(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
) -> int:
    try:
        if method.upper() == "DELETE":
            r = client.request(method, path)
        else:
            r = client.request(method, path, json=json_body)
    except httpx.HTTPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not r.is_success:
        print_response_error(r)
        return 1
    if r.content:
        try:
            print_json_body(r.json())
        except json.JSONDecodeError:
            print(r.text)
    return 0
