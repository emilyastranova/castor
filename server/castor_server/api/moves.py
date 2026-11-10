"""Moving what people wrote: a comment to another card, a comment out to the project's
timeline (it becomes a post), or a timeline post onto a card (it becomes a comment).

It stays the same writing: the author, text, mentions, when it was written, whether it was
edited and whether it came through MCP all come along. A comment moved between cards keeps
its id; a conversion makes the other kind (with a "created" activity dated when it was
written, so it sits in the right place on the timeline) and records the move itself. Only the
author or an admin moves it, between cards of the same project (mentions and images belong to
the project). Images keep working where they're stored."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import selectinload

from .. import models as m
from .. import people
from ..access import check_project, task_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..schemas import CommentOut
from ..services import actor_of, publish_task, record_activity
from .posts import PostOut, _post_or_404, hashtags, post_out
from .tasks import _comment_out, _task_or_404

router = APIRouter(tags=["comments"])


class MoveIn(BaseModel):
    """Where to: a card's id, or null for the project's timeline."""

    to_task_id: uuid.UUID | None = None


class MoveOut(BaseModel):
    kind: Literal["comment", "post"]
    comment: CommentOut | None = None
    post: PostOut | None = None


async def _target(db: DB, user: m.User, project_id: uuid.UUID, to_task_id: uuid.UUID | None) -> m.Task | None:
    if to_task_id is None:
        return None
    task = await task_or_404(db, user, to_task_id, write=True)
    if task.project_id != project_id:
        raise HTTPException(422, "Comments move between cards of the same project")
    if task.playbook_step_id:
        raise HTTPException(422, "Move it to the playbook run's card, not one of its steps")
    return task


async def _backdated(db: DB, event: m.ActivityEvent, when) -> None:
    event.created_at = when
    await db.flush()


@router.post("/tasks/{task_id}/comments/{comment_id}/move", response_model=MoveOut)
async def move_comment(task_id: str, comment_id: str, body: MoveIn, db: DB, user: CurrentUser):
    """Move a comment to another card of the project, or out to its timeline (`to_task_id`
    null), where it becomes a post."""
    source = await _task_or_404(db, user, task_id, write=True)
    comment = await db.get(m.Comment, parse_uuid(comment_id), options=[selectinload(m.Comment.author)])
    if comment is None or comment.task_id != source.id:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != user.id and not user.is_admin:
        raise HTTPException(403, "You can only move your own comments")
    target = await _target(db, user, source.project_id, body.to_task_id)
    if target is not None and target.id == source.id:
        raise HTTPException(422, "It's already on that card")

    if target is not None:
        comment.task_id = target.id
        await record_activity(
            db, type="comment.moved", message=f"Moved a comment from “{source.title}” to “{target.title}”",
            project_id=source.project_id, task_id=target.id,
            payload={"moved_comment_id": str(comment.id), "from_task_id": str(source.id)}, **actor_of(user),
        )
        await db.commit()
        out = _comment_out(comment)
        bus.publish("comment.deleted", source.project_id, {"id": comment.id, "task_id": source.id})
        bus.publish("comment.created", target.project_id, out)
        await publish_task(db, source)
        await publish_task(db, target)
        return MoveOut(kind="comment", comment=out)

    # Out to the timeline: the same writing as a post.
    post = m.ProjectPost(
        project_id=source.project_id, author_id=comment.author_id, body=comment.body, tags=hashtags(comment.body),
        mentions=[str(u.id) for u in await people.mentioned(db, source.project_id, comment.body)],
        edited_at=comment.edited_at, via=comment.via, created_at=comment.created_at, updated_at=comment.updated_at,
    )
    post.author = comment.author
    db.add(post)
    old_id = comment.id
    await db.delete(comment)
    await db.flush()
    created = await record_activity(db, type="post.created", message="Posted to the timeline", project_id=source.project_id,
                                    payload={"post_id": str(post.id)}, **actor_of(comment.author))
    await _backdated(db, created, post.created_at)
    await record_activity(
        db, type="comment.moved", message=f"Moved a comment from “{source.title}” to the timeline",
        project_id=source.project_id, payload={"moved_post_id": str(post.id), "from_task_id": str(source.id)}, **actor_of(user),
    )
    await db.commit()
    out = post_out(post)
    bus.publish("comment.deleted", source.project_id, {"id": old_id, "task_id": source.id})
    bus.publish("post.created", source.project_id, out.model_dump(mode="json"))
    await publish_task(db, source)
    return MoveOut(kind="post", post=out)


@router.post("/posts/{post_id}/move", response_model=MoveOut)
async def move_post(post_id: str, body: MoveIn, db: DB, user: CurrentUser):
    """Move a timeline post onto a card of the project, where it becomes a comment."""
    post = await _post_or_404(db, user, post_id, write=True)
    if body.to_task_id is None:
        raise HTTPException(422, "It's already on the timeline; give to_task_id, a card of the project")
    await check_project(db, user, post.project_id, write=True)
    target = await _target(db, user, post.project_id, body.to_task_id)
    assert target is not None

    comment = m.Comment(
        task_id=target.id, author_id=post.author_id, body=post.body,
        mentions=[str(u.id) for u in await people.mentioned(db, post.project_id, post.body)],
        edited_at=post.edited_at, via=post.via, created_at=post.created_at, updated_at=post.updated_at,
    )
    comment.author = post.author
    db.add(comment)
    old_id, project_id = post.id, post.project_id
    await db.delete(post)
    await db.flush()
    created = await record_activity(db, type="comment.created", message=f"Commented on “{target.title}”", project_id=project_id,
                                    task_id=target.id, payload={"comment_id": str(comment.id)}, **actor_of(comment.author))
    await _backdated(db, created, comment.created_at)
    await record_activity(
        db, type="comment.moved", message=f"Moved a post from the timeline to “{target.title}”", project_id=project_id,
        task_id=target.id, payload={"moved_comment_id": str(comment.id), "from": "timeline"}, **actor_of(user),
    )
    await db.commit()
    out = _comment_out(comment)
    bus.publish("post.deleted", project_id, {"id": str(old_id), "project_id": str(project_id)})
    bus.publish("comment.created", project_id, out)
    await publish_task(db, target)
    return MoveOut(kind="comment", comment=out)
