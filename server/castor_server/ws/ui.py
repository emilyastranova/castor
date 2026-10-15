"""UI WebSocket: pushes domain events to authenticated browsers."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import SessionLocal
from ..deps import websocket_user
from ..events import bus

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/ui")
async def ui_socket(ws: WebSocket) -> None:
    async with SessionLocal() as db:
        user = await websocket_user(ws, db)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = bus.subscribe()

    async def pump() -> None:
        while True:
            message = await queue.get()
            await ws.send_text(message)

    sender = asyncio.create_task(pump())
    try:
        while True:
            # The client may send pings / subscriptions; we only need to keep the socket open.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("ui socket closed", exc_info=True)
    finally:
        sender.cancel()
        bus.unsubscribe(queue)
