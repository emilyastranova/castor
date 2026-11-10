"""Posts: comments on nothing, written straight into a project's timeline (its Home). Notes,
screenshots, findings that don't belong to a card. Anyone who sees the project reads them;
operators post; the author (or an admin) edits and deletes. Pasted images are project
attachments without a card (`POST /api/projects/{id}/attachments`)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import api_tokens, notifications, people
from .. import models as m
from ..access import check_project
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..models import utcnow
from ..services import actor_of, record_activity

router = APIRouter(tags=["posts"])

# A #hashtag: starts with a letter, after a space or punctuation (not in a URL or a word, not ##).
# web/src/lib/hashtags.ts uses the same rule to make them links.
HASHTAG = re.compile(r"(?<![\w#/&])#([^\W\d_][\w-]{0,39})")
CODE = re.compile(r"```.*?(?:```|$)|`[^`\n]*`|\]\([^)]*\)", re.DOTALL)  # code, and link targets


def hashtags(text: str) -> list[str]:
    """The #tags in a post's markdown (not in code or links), first spelling of each, in order."""
    seen: dict[str, str] = {}
    for match in HASHTAG.finditer(CODE.sub(" ", text)):
        tag = match[1].rstrip("-_")
        seen.setdefault(tag.lower(), tag)
    return list(seen.values())


class PostIn(BaseModel):
    body: str = Field(min_length=1, max_length=50_000)


class PostOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    author_id: uuid.UUID | None
    author_name: str
    author_avatar: str | None = None
    body: str
    mentions: list[str]
    tags: list[str] = []
    created_at: datetime
    edited_at: datetime | None = None
    via: str | None = None  # "mcp": through an AI assistant


def post_out(post: m.ProjectPost) -> PostOut:
    a = post.author
    return PostOut(
        id=post.id, project_id=post.project_id, author_id=post.author_id, author_name=a.display_name if a else "unknown",
        author_avatar=a.avatar_url if a else None, body=post.body, mentions=list(post.mentions or []), tags=list(post.tags or []),
        created_at=post.created_at, edited_at=post.edited_at, via=post.via,
    )


async def _post_or_404(db: DB, user: m.User, post_id: str, *, write: bool = False) -> m.ProjectPost:
    post = await db.get(m.ProjectPost, parse_uuid(post_id), options=[selectinload(m.ProjectPost.author)])
    if post is None:
        raise HTTPException(404, "Post not found")
    try:
        await check_project(db, user, post.project_id, write=write)
    except HTTPException as err:
        if err.status_code == 404:
            raise HTTPException(404, "Post not found") from None
        raise
    if write and post.author_id != user.id and not user.is_admin:
        raise HTTPException(403, "You can only change your own posts")
    return post


async def _mention(db: DB, post: m.ProjectPost, author: m.User, already: list[str]) -> None:
    """Store who the post mentions and notify the newly mentioned (not the author)."""
    users = await people.mentioned(db, post.project_id, post.body)
    post.mentions = [str(u.id) for u in users]
    new = [u.id for u in users if str(u.id) not in already and u.id != author.id]
    if new:
        project = await db.get(m.Project, post.project_id)
        excerpt = " ".join(post.body.split())
        await notifications.notify(
            db, kind="comment.mention", title=f"{author.display_name} mentioned you in {project.codename if project else 'a project'}",
            body=excerpt[:200] + ("…" if len(excerpt) > 200 else ""),
            link=f"/projects/{post.project_id}/dashboard?post={post.id}", project_id=post.project_id,
            subject=f"post:{post.id}", user_ids=new, webhooks=False,
        )


def tagged(tag: str):
    """Posts with this hashtag (any case)."""
    from ..search import _like, _text

    return _text(m.ProjectPost.tags).ilike(_like(f'"{tag.lstrip("#")}"'), escape="\\")


async def post_tags(db: DB, project_id: uuid.UUID) -> list[dict]:
    """A project's hashtags in use, most used first: [{tag, count}]."""
    counts: dict[str, list] = {}
    for (tags,) in (await db.execute(select(m.ProjectPost.tags).where(m.ProjectPost.project_id == project_id))).all():
        for t in tags or []:
            counts.setdefault(t.lower(), [t, 0])[1] += 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.values(), key=lambda x: (-x[1], x[0].lower()))]


def _publish(name: str, post: m.ProjectPost, out: PostOut | None = None) -> None:
    bus.publish(name, post.project_id, (out or post_out(post)).model_dump(mode="json") if name != "post.deleted"
                else {"id": str(post.id), "project_id": str(post.project_id)})


@router.get("/projects/{project_id}/posts", response_model=list[PostOut])
async def list_posts(project_id: str, db: DB, user: CurrentUser, before: datetime | None = None, limit: int = 50,
                     tag: str | None = None):
    """A project's posts, newest first (the timeline shows them too); `tag` (without #) for one hashtag."""
    pid = parse_uuid(project_id)
    await check_project(db, user, pid)
    query = select(m.ProjectPost).options(selectinload(m.ProjectPost.author)).where(m.ProjectPost.project_id == pid)
    if tag:
        query = query.where(tagged(tag))
    if before:
        query = query.where(m.ProjectPost.created_at < before)
    rows = (await db.execute(query.order_by(m.ProjectPost.created_at.desc()).limit(max(1, min(limit, 200))))).scalars()
    return [post_out(p) for p in rows]


@router.get("/projects/{project_id}/post-tags")
async def list_post_tags(project_id: str, db: DB, user: CurrentUser) -> list[dict]:
    """The hashtags a project's posts use, most used first."""
    pid = parse_uuid(project_id)
    await check_project(db, user, pid)
    return await post_tags(db, pid)


@router.post("/projects/{project_id}/posts", response_model=PostOut, status_code=201)
async def create_post(project_id: str, body: PostIn, db: DB, user: CurrentUser):
    """Post to a project's timeline (markdown; @mentions notify people)."""
    pid = parse_uuid(project_id)
    await check_project(db, user, pid, write=True)
    if not body.body.strip():
        raise HTTPException(400, "The post is empty")
    post = m.ProjectPost(project_id=pid, author_id=user.id, body=body.body, tags=hashtags(body.body), via=api_tokens.via.get())
    post.author = user
    db.add(post)
    await db.flush()
    await _mention(db, post, user, [])
    await record_activity(db, type="post.created", message="Posted to the timeline", project_id=pid,
                          payload={"post_id": str(post.id)}, **actor_of(user))
    await db.commit()
    out = post_out(post)
    _publish("post.created", post, out)
    return out


@router.patch("/posts/{post_id}", response_model=PostOut)
async def update_post(post_id: str, body: PostIn, db: DB, user: CurrentUser):
    post = await _post_or_404(db, user, post_id, write=True)
    if not body.body.strip():
        raise HTTPException(400, "The post is empty")
    if body.body != post.body:
        post.body = body.body
        post.tags = hashtags(body.body)
        post.edited_at = utcnow()
        if api_tokens.via.get():
            post.via = api_tokens.via.get()
        await _mention(db, post, post.author or user, list(post.mentions or []))
        await record_activity(db, type="post.edited", message="Edited a post", project_id=post.project_id,
                              payload={"post_id": str(post.id)}, **actor_of(user))
        await db.commit()
    out = post_out(post)
    _publish("post.updated", post, out)
    return out


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: str, db: DB, user: CurrentUser):
    post = await _post_or_404(db, user, post_id, write=True)
    await db.delete(post)
    await record_activity(db, type="post.deleted", message="Deleted a post", project_id=post.project_id,
                          payload={"post_id": str(post.id)}, **actor_of(user))
    await db.commit()
    _publish("post.deleted", post)
