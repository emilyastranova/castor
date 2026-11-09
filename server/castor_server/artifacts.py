"""Per-job artifact upload tokens and which files an agent should collect."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .command_spec import CommandSpec

# Agents always collect anything a job writes under /work/artifacts/, in addition to the
# files a command definition declares as outputs.
DEFAULT_COLLECT = ["/work/artifacts/**"]


def new_token() -> tuple[str, str]:
    """Return (token for the agent, hash to store on the job)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str | None, stored_hash: str | None) -> bool:
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def collect_patterns(spec: CommandSpec | None) -> list[str]:
    patterns = [o.source for o in spec.outputs if o.source] if spec else []
    return [*dict.fromkeys(patterns + DEFAULT_COLLECT)]


class ArtifactRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def store(db: Any, job: Any, name: str, content_type: str | None, chunks: AsyncIterator[bytes]) -> Any:
    """Keep one file a job wrote as an artifact attachment on its card (the agent's upload,
    or a file an SSH host's job left). Raises ArtifactRefused past the window or limits."""
    import aiofiles
    from sqlalchemy import func, select

    from . import models as m
    from .api.attachments import _safe_name
    from .config import settings
    from .events import bus
    from .schemas import AttachmentOut
    from .services import publish_task, utcnow

    window_closed = (
        job.status.is_terminal
        and job.finished_at is not None
        and (utcnow() - job.finished_at).total_seconds() > settings.artifact_upload_window_s
    )
    if window_closed:
        raise ArtifactRefused(410, "The upload window for this job has closed")
    count = (await db.execute(select(func.count()).where(m.Attachment.job_id == job.id))).scalar_one()
    if count >= settings.artifact_max_files:
        raise ArtifactRefused(413, f"A job can keep at most {settings.artifact_max_files} files")
    task = await db.get(m.Task, job.task_id)
    if task is None:
        raise ArtifactRefused(410, "The card for this job no longer exists")

    display_name = (name or "artifact").lstrip("/")[:255]
    attachment_id = uuid.uuid4()
    directory = settings.uploads_dir / str(task.project_id) / "artifacts" / str(job.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{attachment_id}-{_safe_name(display_name)}"
    size = 0
    try:
        async with aiofiles.open(target, "wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > settings.artifact_max_bytes:
                    raise ArtifactRefused(413, "File is larger than the artifact size limit")
                await out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    attachment = m.Attachment(
        id=attachment_id, project_id=task.project_id, task_id=task.id, job_id=job.id, kind="artifact",
        filename=display_name, content_type=content_type or "application/octet-stream", size=size,
        storage_path=str(target.relative_to(settings.data_dir)),
    )
    db.add(attachment)
    await db.commit()
    out = AttachmentOut.model_validate(attachment)
    bus.publish("attachment.created", task.project_id, out)
    await publish_task(db, task)
    return out
