"""In-process event bus. Domain code publishes; the UI WebSocket fans out."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (uuid.UUID,)):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Cannot serialise {type(obj)!r}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


@dataclass(frozen=True)
class Event:
    """A published event: the routing fields the UI socket filters on, and the message."""

    name: str
    project_id: str | None  # only for those who can see this project (None: everyone)
    user_id: str | None  # only for this user (None: everyone)
    ref: str | None  # a small routing detail, e.g. the user a user.changed is about
    message: str


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(
        self,
        name: str,
        project_id: uuid.UUID | str | None,
        data: Any,
        *,
        user_id: uuid.UUID | str | None = None,
        ref: uuid.UUID | str | None = None,
    ) -> None:
        message = dumps(
            {"type": "event", "name": name, "project_id": project_id, "data": data}
        )
        event = Event(
            name,
            str(project_id) if project_id else None,
            str(user_id) if user_id else None,
            str(ref) if ref else None,
            message,
        )
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("UI subscriber queue full; dropping subscriber")
                self._subscribers.discard(queue)


bus = EventBus()
