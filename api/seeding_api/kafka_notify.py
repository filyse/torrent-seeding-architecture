"""Исходящие события creator → Kafka (MPW слушает и снимает строки).

Без ``SEEDING_KAFKA_BOOTSTRAP`` или без ``kafka-python`` — тихий no-op:
удаление задачи не зависит от брокера.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

CREATOR_DELETED_EVENT = "creator.task.deleted"
_DEFAULT_TOPIC = "creator.task.deleted"

_producer = None
_producer_lock = threading.Lock()
_import_warned = False


def kafka_bootstrap() -> str:
    return os.getenv("SEEDING_KAFKA_BOOTSTRAP", "").strip()


def creator_deleted_topic() -> str:
    return (
        os.getenv("SEEDING_KAFKA_TOPIC_CREATOR_DELETED", "").strip() or _DEFAULT_TOPIC
    )


def creator_deleted_payload(
    engine_id: str,
    task_id: int,
    *,
    reason: str = "deleted",
    name: str = "",
    source_path: str = "",
) -> dict:
    eid = str(engine_id).strip().lower()
    tid = int(task_id)
    return {
        "event": CREATOR_DELETED_EVENT,
        "engine_id": eid,
        "task_id": tid,
        "task_key": f"{eid}:{tid}",
        "reason": reason,
        "name": name or "",
        "source_path": source_path or "",
    }


def _get_producer():
    global _producer, _import_warned
    servers = [part.strip() for part in kafka_bootstrap().split(",") if part.strip()]
    if not servers:
        return None
    with _producer_lock:
        if _producer is not None:
            return _producer
        try:
            from kafka import KafkaProducer
        except ImportError:
            if not _import_warned:
                log.warning("kafka-python не установлен — событие creator.task.deleted не уйдёт")
                _import_warned = True
            return None
        _producer = KafkaProducer(
            bootstrap_servers=servers,
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode(
                "utf-8"
            ),
            acks="all",
            retries=3,
            request_timeout_ms=10000,
        )
        return _producer


def reset_producer() -> None:
    """Для тестов: сбросить кэш продюсера."""
    global _producer
    with _producer_lock:
        producer = _producer
        _producer = None
    if producer is not None:
        try:
            producer.close(timeout=2)
        except Exception:  # noqa: BLE001
            pass


def publish_creator_deleted(
    engine_id: str,
    task_id: int,
    *,
    reason: str = "deleted",
    name: str = "",
    source_path: str = "",
) -> bool:
    if not kafka_bootstrap():
        return False
    producer = _get_producer()
    if producer is None:
        return False
    payload = creator_deleted_payload(
        engine_id, task_id, reason=reason, name=name, source_path=source_path
    )
    try:
        producer.send(creator_deleted_topic(), value=payload)
        producer.flush(timeout=5)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось опубликовать %s: %s", CREATOR_DELETED_EVENT, exc)
        return False


async def publish_creator_deleted_async(
    engine_id: str,
    task_id: int,
    *,
    reason: str = "deleted",
    name: str = "",
    source_path: str = "",
) -> bool:
    return await asyncio.to_thread(
        publish_creator_deleted,
        engine_id,
        task_id,
        reason=reason,
        name=name,
        source_path=source_path,
    )
