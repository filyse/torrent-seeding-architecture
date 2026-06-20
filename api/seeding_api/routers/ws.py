"""WebSocket-эндпоинт (Фаза 7, WS-1): единое соединение с подписками на каналы.

Авторизация в handshake, без ключа в URL по возможности:
1. сабпротокол ``bearer.<key>`` (клиент шлёт ``["seeding.v1", "bearer.<key>"]``);
2. query ``?api_key=`` (fallback, как у SSE);
3. первое сообщение ``{"type":"auth","key":"…"}`` (fallback) в течение таймаута.

Протокол сообщений:
- клиент → сервер: ``{type:"subscribe"|"unsubscribe", channel}``, ``{type:"ping"}``,
  ``{type:"auth", key}``;
- сервер → клиент: ``{type:"snapshot"|"event", channel, data, v}``, ``{type:"pong"}``,
  ``{type:"error", error}``.

На ``subscribe`` сразу шлём текущий снапшот (если умеем его собрать), далее — дельты от
publisher'ов (WS-2). Эндпоинт за фича-флагом ``SEEDING_WS_ENABLED``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from seeding_api.auth import resolve_principal
from seeding_api.ws_hub import WsClient, ws_enabled
from seeding_api.ws_sources import initial_snapshot

log = logging.getLogger(__name__)

router = APIRouter()

_MARKER_SUBPROTOCOL = "seeding.v1"
_BEARER_PREFIX = "bearer."
_AUTH_TIMEOUT = 10.0


def _parse_subprotocols(ws: WebSocket) -> list[str]:
    raw = ws.headers.get("sec-websocket-protocol", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _valid_channel(channel: str) -> bool:
    if channel in ("stats", "engines"):
        return True
    if channel.startswith("torrent:") or channel.startswith("migrate:"):
        return channel.split(":", 1)[1].isdigit()
    if channel.startswith("job:"):
        token = channel.split(":", 1)[1]
        return bool(token) and all(c.isalnum() or c in "-_" for c in token)
    return False


async def _authenticate(ws: WebSocket):
    """Вернуть (principal|None, echo_subprotocol|None, need_message_auth)."""
    protos = _parse_subprotocols(ws)
    echo = _MARKER_SUBPROTOCOL if _MARKER_SUBPROTOCOL in protos else None
    for p in protos:
        if p.startswith(_BEARER_PREFIX):
            key = p[len(_BEARER_PREFIX):]
            if key:
                return await resolve_principal(ws, key), echo, False
    qk = ws.query_params.get("api_key")
    if qk:
        return await resolve_principal(ws, qk.strip()), echo, False
    # Ни сабпротокол, ни query — попробуем первое сообщение после accept.
    return None, echo, True


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not ws_enabled():
        await ws.close(code=1013)  # try again later (фича выключена)
        return

    principal, echo, need_msg_auth = await _authenticate(ws)

    if principal is None and not need_msg_auth:
        await ws.close(code=4401)  # auth failed (явный ключ невалиден)
        return

    await ws.accept(subprotocol=echo)

    if principal is None:
        # Авторизация первым сообщением.
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=_AUTH_TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") == "auth" and msg.get("key"):
                principal = await resolve_principal(ws, str(msg["key"]).strip())
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError):
            principal = None
        if principal is None:
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps({"type": "error", "error": "auth required"}))
                await ws.close(code=4401)
            return

    client = WsClient(ws, principal)
    hub = ws.app.state.ws_hub
    await hub.register(client)
    writer = asyncio.create_task(client.run_writer())
    client.send_raw("__ready__", {"type": "ready", "role": principal.role})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mtype = msg.get("type")
            if mtype == "subscribe":
                channel = str(msg.get("channel") or "")
                if not _valid_channel(channel):
                    client.send_raw("__err__", {"type": "error", "error": f"bad channel: {channel}"})
                    continue
                await hub.subscribe(client, channel)
                try:
                    snap = await initial_snapshot(ws.app, channel)
                except Exception as exc:  # noqa: BLE001
                    log.debug("ws initial snapshot %s failed: %s", channel, exc)
                    snap = None
                if snap is not None:
                    client.send_snapshot(channel, snap)
            elif mtype == "unsubscribe":
                await hub.unsubscribe(client, str(msg.get("channel") or ""))
            elif mtype == "ping":
                client.send_raw("__pong__", {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws loop error: %s", exc)
    finally:
        client.closed = True
        client.wake()
        writer.cancel()
        with contextlib.suppress(Exception):
            await writer
        await hub.unregister(client)
