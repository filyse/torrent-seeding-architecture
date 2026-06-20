"""WebSocket-хаб (Фаза 7, WS-1): подписки на каналы и пуш по факту изменения.

Канал — это строка-тема, на которую подписывается клиент:
- ``stats``           — агрегаты сессии (верхняя панель);
- ``torrent:{id}``    — живые поля одной раздачи;
- ``engines``         — реестр/здоровье движков;
- ``migrate:{id}``    — прогресс переноса раздачи;
- ``job:{id}``        — результат фоновой задачи (arq).

Бэкпрешер: на каждого клиента держим «последнее сообщение по каналу» (коалесинг), а не
очередь — медленный клиент получит свежий снапшот, а не лавину устаревших. Один воркер на
клиента читает накопленное и пишет в сокет.

WS-1 — это in-process хаб (один процесс API). Многоворкерный fan-out через Redis — WS-3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1


def ws_enabled() -> bool:
    """Фича-флаг: WebSocket выключен по умолчанию (Фаза 7 за флагом)."""
    return os.getenv("SEEDING_WS_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class WsClient:
    """Одно WebSocket-соединение с коалесингом исходящих сообщений по каналам."""

    def __init__(self, ws: Any, principal: Any) -> None:
        self.ws = ws
        self.principal = principal
        self.channels: set[str] = set()
        self.closed = False
        self._pending: dict[str, str] = {}
        self._wake = asyncio.Event()

    def offer(self, channel: str, payload: str) -> None:
        """Положить сериализованное сообщение на отправку (последнее по каналу побеждает)."""
        if self.closed:
            return
        self._pending[channel] = payload
        self._wake.set()

    def send_event(self, channel: str, data: Any) -> None:
        self.offer(channel, _dumps({"type": "event", "channel": channel, "data": data, "v": PROTOCOL_VERSION}))

    def send_snapshot(self, channel: str, data: Any) -> None:
        self.offer(channel, _dumps({"type": "snapshot", "channel": channel, "data": data, "v": PROTOCOL_VERSION}))

    def send_raw(self, key: str, obj: Any) -> None:
        self.offer(key, _dumps(obj))

    async def run_writer(self) -> None:
        """Цикл отправки: ждём пробуждения, выгружаем накопленное по каналам в сокет."""
        try:
            while not self.closed:
                await self._wake.wait()
                self._wake.clear()
                pending, self._pending = self._pending, {}
                for payload in pending.values():
                    await self.ws.send_text(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — сокет закрылся/ошибка записи
            log.debug("ws writer stopped: %s", exc)
            self.closed = True

    def wake(self) -> None:
        self._wake.set()


class WsHub:
    """Реестр клиентов и подписок; публикация события всем подписчикам канала."""

    def __init__(self) -> None:
        self.clients: set[WsClient] = set()
        self._subs: dict[str, set[WsClient]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, client: WsClient) -> None:
        async with self._lock:
            self.clients.add(client)

    async def unregister(self, client: WsClient) -> None:
        async with self._lock:
            self.clients.discard(client)
            for ch in list(client.channels):
                subs = self._subs.get(ch)
                if subs is not None:
                    subs.discard(client)
                    if not subs:
                        self._subs.pop(ch, None)
            client.channels.clear()

    async def subscribe(self, client: WsClient, channel: str) -> None:
        async with self._lock:
            client.channels.add(channel)
            self._subs[channel].add(client)

    async def unsubscribe(self, client: WsClient, channel: str) -> None:
        async with self._lock:
            client.channels.discard(channel)
            subs = self._subs.get(channel)
            if subs is not None:
                subs.discard(client)
                if not subs:
                    self._subs.pop(channel, None)

    async def publish(self, channel: str, data: Any) -> int:
        """Разослать событие всем подписчикам канала. Возвращает число адресатов."""
        payload = _dumps({"type": "event", "channel": channel, "data": data, "v": PROTOCOL_VERSION})
        async with self._lock:
            targets = list(self._subs.get(channel, ()))
        for client in targets:
            client.offer(channel, payload)
        return len(targets)

    def publish_sync(self, channel: str, data: Any) -> int:
        """Синхронная публикация (для sync-кода вроде set_progress).

        Без захвата asyncio-лока: вызывается из того же event-loop, что и
        subscribe/unsubscribe, поэтому без await параллельной мутации множества не будет.
        ``offer`` неблокирующий — только кладёт сообщение клиенту и будит его writer.
        """
        subs = self._subs.get(channel)
        if not subs:
            return 0
        payload = _dumps({"type": "event", "channel": channel, "data": data, "v": PROTOCOL_VERSION})
        for client in list(subs):
            client.offer(channel, payload)
        return len(subs)

    def has_subscribers(self, channel: str) -> bool:
        subs = self._subs.get(channel)
        return bool(subs)

    def any_subscribers(self, prefix: str) -> bool:
        """Есть ли подписчики на канал с данным префиксом (напр. ``torrent:``)."""
        for ch, subs in self._subs.items():
            if subs and ch.startswith(prefix):
                return True
        return False

    def channels_with_prefix(self, prefix: str) -> list[str]:
        """Список каналов с данным префиксом, у которых есть подписчики (для адресных пуллеров)."""
        return [ch for ch, subs in self._subs.items() if subs and ch.startswith(prefix)]

    @property
    def metrics(self) -> dict[str, int]:
        return {"clients": len(self.clients), "channels": len(self._subs)}
