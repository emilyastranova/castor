"""In-process event bus. Domain code publishes; the UI WebSocket fans out."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
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


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def publish(self, name: str, project_id: uuid.UUID | str | None, data: Any) -> None:
        message = dumps(
            {"type": "event", "name": name, "project_id": project_id, "data": data}
        )
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("UI subscriber queue full; dropping subscriber")
                self._subscribers.discard(queue)


bus = EventBus()
