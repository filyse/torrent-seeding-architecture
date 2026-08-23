"""Сообщить оркестратору, что задача создания исчезла по TTL.

Ручной DELETE идёт через API — там Kafka публикуется сразу. TTL живёт на движке,
поэтому reaper шлёт пачку сюда (тот же X-Register-Key, что и саморегистрация).
Вызывать из фонового потока: оркестратор в этот момент может ждать ответ движка.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


def notify_orchestrator_deleted(reason: str, tasks: list[dict]) -> None:
    orch = os.getenv("SEEDING_ORCHESTRATOR_URL", "").strip().rstrip("/")
    key = os.getenv("SEEDING_ENGINE_REGISTER_KEY", "").strip()
    engine_id = (
        os.getenv("SEEDING_ENGINE_ID") or os.getenv("ENGINE_STORAGE_SUBDIR") or ""
    ).strip()
    if not (orch and key and engine_id and tasks):
        return
    body = json.dumps(
        {
            "engine_id": engine_id,
            "tasks": [
                {
                    "id": int(item["id"]),
                    "name": str(item.get("name") or ""),
                    "source_path": str(item.get("source_path") or ""),
                    "reason": reason,
                }
                for item in tasks
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{orch}/api/v1/creator/events/deleted",
        data=body,
        headers={"Content-Type": "application/json", "X-Register-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not (200 <= resp.status < 300):
                log.warning("creator deleted notify: HTTP %s", resp.status)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        log.warning("creator deleted notify failed: %s", exc)
