"""Emoji reactions on comments and posts, and custom emoji (reactions.py)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import models as m
from .. import reactions as rx
from ..access import check_project
from ..deps import DB, Admin, CurrentUser, parse_uuid
from ..events import bus
from ..services import actor_of, record_activity

router = APIRouter(tags=["reactions"])


class ReactionIn(BaseModel):
    # A Unicode emoji ("👍") or ":name:" for a custom one (GET /api/emojis).
    emoji: str = Field(min_length=1, max_length=80)


class ReactionOut(BaseModel):
    emoji: str
    count: int
    user_ids: list[str]
    names: list[str]


async def _comment(db: DB, user: m.User, comment_id: str) -> tuple[m.Comment, m.Task]:
    comment = await db.get(m.Comment, parse_uuid(comment_id))
    task = await db.get(m.Task, comment.task_id) if comment else None
    if comment is None or task is None:
        raise HTTPException(404, "Comment not found")
    try:
        await check_project(db, user, task.project_id, write=True)
    except HTTPException as err:
        raise HTTPException(404, "Comment not found") if err.status_code == 404 else err from None
    return comment, task


async def _post(db: DB, user: m.User, post_id: str) -> m.ProjectPost:
    post = await db.get(m.ProjectPost, parse_uuid(post_id))
    if post is None:
        raise HTTPException(404, "Post not found")
    try:
        await check_project(db, user, post.project_id, write=True)
    except HTTPException as err:
        raise HTTPException(404, "Post not found") if err.status_code == 404 else err from None
    return post


async def _react(db, user, project_id, emoji, add: bool, **target) -> list[dict[str, Any]]:
    try:
        return await rx.react(db, user, project_id, emoji, add=add, **target)
    except rx.ReactionError as exc:
        raise HTTPException(422, f"emoji: {exc}") from exc


@router.post("/comments/{comment_id}/reactions", response_model=list[ReactionOut])
async def react_to_comment(comment_id: str, body: ReactionIn, db: DB, user: CurrentUser):
    """React to a comment (again with the same emoji: nothing changes). Returns its reactions."""
    comment, task = await _comment(db, user, comment_id)
    return await _react(db, user, task.project_id, body.emoji, True, comment_id=comment.id)


@router.delete("/comments/{comment_id}/reactions", response_model=list[ReactionOut])
async def unreact_comment(comment_id: str, emoji: str, db: DB, user: CurrentUser):
    """Take your reaction back."""
    comment, task = await _comment(db, user, comment_id)
    return await _react(db, user, task.project_id, emoji, False, comment_id=comment.id)


@router.post("/posts/{post_id}/reactions", response_model=list[ReactionOut])
async def react_to_post(post_id: str, body: ReactionIn, db: DB, user: CurrentUser):
    post = await _post(db, user, post_id)
    return await _react(db, user, post.project_id, body.emoji, True, post_id=post.id)


@router.delete("/posts/{post_id}/reactions", response_model=list[ReactionOut])
async def unreact_post(post_id: str, emoji: str, db: DB, user: CurrentUser):
    post = await _post(db, user, post_id)
    return await _react(db, user, post.project_id, emoji, False, post_id=post.id)


# ---------- custom emoji ----------

class EmojiOut(BaseModel):
    name: str
    url: str
    created_at: str | None = None


@router.get("/emojis", response_model=list[EmojiOut])
async def list_emojis(db: DB, _: CurrentUser):
    """The instance's custom emoji: react with one as ":name:"."""
    rows = (await db.execute(select(m.CustomEmoji).order_by(m.CustomEmoji.name))).scalars()
    return [rx.emoji_out(e) for e in rows]


@router.get("/emojis/{name}/image")
async def emoji_image(name: str, db: DB, _: CurrentUser):
    row = await db.get(m.CustomEmoji, name)
    path = rx.directory() / row.file if row else None
    if row is None or path is None or not path.is_file():
        raise HTTPException(404, "No such emoji")
    return Response(path.read_bytes(), media_type=row.content_type,
                    headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.post("/emojis", response_model=EmojiOut, status_code=201)
async def add_emoji(db: DB, user: Admin, name: Annotated[str, Form()], file: Annotated[UploadFile, File()],
                    replace: Annotated[bool, Form()] = False):
    """Add a custom emoji (admins): a name of 2–32 lowercase letters, digits and underscores, and a
    PNG, GIF, WebP or JPEG image up to 256 KB (square works best; it's drawn at text size)."""
    name = name.strip().strip(":").lower()
    if not rx.NAME.match(name):
        raise HTTPException(422, "name: 2–32 lowercase letters, digits and underscores, like party_parrot")
    row = await db.get(m.CustomEmoji, name)
    if row is not None and not replace:
        raise HTTPException(409, f"There's already an emoji :{name}:")
    try:
        file_name, content_type = rx.save(name, await file.read(rx.MAX_BYTES + 1))
    except rx.ReactionError as exc:
        raise HTTPException(422, f"file: {exc}") from exc
    if row is None:
        row = m.CustomEmoji(name=name, file=file_name, content_type=content_type, created_by=user.id)
        db.add(row)
    else:
        rx.remove_file(row.file)
        row.file, row.content_type = file_name, content_type
    await record_activity(db, type="emoji.added", message=f"Added the custom emoji :{name}:", project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("emojis.changed", None, {})
    return rx.emoji_out(row)


@router.delete("/emojis/{name}", status_code=204)
async def delete_emoji(name: str, db: DB, user: Admin):
    """Remove a custom emoji, and every reaction made with it."""
    row = await db.get(m.CustomEmoji, name)
    if row is None:
        raise HTTPException(404, "No such emoji")
    await db.execute(delete(m.Reaction).where(m.Reaction.emoji == f":{name}:"))
    rx.remove_file(row.file)
    await db.delete(row)
    await record_activity(db, type="emoji.removed", message=f"Removed the custom emoji :{name}:", project_id=None, **actor_of(user))
    await db.commit()
    bus.publish("emojis.changed", None, {})
