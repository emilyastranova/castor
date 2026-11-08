"""UI WebSocket: pushes domain events to authenticated browsers, each only the events
about projects its user can see (and notifications meant for them)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import api_tokens
from .. import models as m
from ..access import visible_project_ids
from ..db import SessionLocal
from ..deps import websocket_user
from ..events import Event, bus

log = logging.getLogger(__name__)
router = APIRouter()


class Audience:
    """What one socket's user may see; reloaded when membership or the user changes."""

    def __init__(self, user_id: uuid.UUID, token_id: uuid.UUID | None = None) -> None:
        self.user_id = user_id
        self.token_id = token_id  # an API token's project limit applies too
        self.str_id = str(user_id)
        self.visible: set[str] | None = set()
        self.active = True

    async def reload(self) -> None:
        async with SessionLocal() as db:
            user = await db.get(m.User, self.user_id)
            if user is None or user.disabled_at is not None:
                self.active = False
                return
            if self.token_id is not None:
                user.api_token = await db.get(m.ApiToken, self.token_id)
                if user.api_token is None or api_tokens.usable(user.api_token):
                    self.active = False
                    return
            ids = await visible_project_ids(db, user)
        self.visible = None if ids is None else {str(i) for i in ids}

    def allows(self, event: Event) -> bool:
        if event.user_id is not None and event.user_id != self.str_id:
            return False
        return event.project_id is None or self.visible is None or event.project_id in self.visible


@router.websocket("/ws/ui")
async def ui_socket(ws: WebSocket) -> None:
    async with SessionLocal() as db:
        user = await websocket_user(ws, db)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = bus.subscribe()
    audience = Audience(user.id, user.api_token.id if user.api_token else None)
    await audience.reload()

    async def pump() -> None:
        while True:
            event = await queue.get()
            if event.name == "project.members" or (event.name == "user.changed" and event.ref == audience.str_id) \
                    or (event.name == "api_token.revoked" and audience.token_id is not None and event.ref == str(audience.token_id)):
                await audience.reload()
                if not audience.active:
                    await ws.close(code=4401)
                    return
            if audience.allows(event):
                await ws.send_text(event.message)

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
