"""Emoji reactions on comments and timeline posts, and the instance's custom emoji.

A reaction's `emoji` is a Unicode emoji ("👍", "🎉", flags and skin tones included) or ":name:" for a
custom emoji an admin uploaded. Each person reacts with a given emoji once per comment; reacting again
takes it back (the API has add and remove; the UI toggles). Reactions aren't activity: they'd drown
the feed, so they're published (`reactions.changed`) for open pages and kept with the thing.

Custom emoji images live in `<data_dir>/emojis`: PNG, GIF (animated is fine), WebP or JPEG, up to
256 KB; no SVG, so an emoji can never carry script."""

from __future__ import annotations

import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .avatars import image_type
from .config import settings
from .events import bus

NAME = re.compile(r"^[a-z0-9_]{2,32}$")
CUSTOM = re.compile(r"^:([a-z0-9_]{2,32}):$")
MAX_BYTES = 256 * 1024
MAX_KINDS = 20  # different emoji on one comment


class ReactionError(ValueError):
    pass


def is_unicode_emoji(text: str) -> bool:
    """One emoji (with its modifiers and joiners), not letters or punctuation dressed up as one."""
    if not text or len(text) > 16 or any(c.isspace() for c in text):
        return False
    if any(c.isascii() and c not in "#*0123456789" for c in text):
        return False  # keycaps (#️⃣, 1️⃣) are the only emoji with ASCII in them
    joiners = {"‍", "️", "︎", "⃣"}
    for c in text:
        if c in joiners or 0x1F3FB <= ord(c) <= 0x1F3FF or 0xE0020 <= ord(c) <= 0xE007F:
            continue  # zero-width joiner, variation selectors, keycap, skin tones, tag characters
        cat = unicodedata.category(c)
        if cat not in ("So", "Sk", "Nd") and not 0x1F1E6 <= ord(c) <= 0x1F1FF:  # symbols, digits, regional flags
            return False
    return any(unicodedata.category(c) == "So" or 0x1F1E6 <= ord(c) <= 0x1F1FF or c == "⃣" for c in text)


async def check_emoji(db: AsyncSession, emoji: str) -> str:
    emoji = emoji.strip()
    custom = CUSTOM.match(emoji)
    if custom:
        if await db.get(m.CustomEmoji, custom[1]) is None:
            raise ReactionError(f"there's no custom emoji :{custom[1]}:")
        return emoji
    if not is_unicode_emoji(emoji):
        raise ReactionError("an emoji, like 👍, or :name: for a custom one")
    return emoji


# ---------- summaries ----------

async def summaries(db: AsyncSession, *, comment_ids: list[uuid.UUID] | None = None,
                    post_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """{comment or post id: [{emoji, count, user_ids, names}]}, emoji in the order first used."""
    q = select(m.Reaction, m.User.display_name).join(m.User, m.User.id == m.Reaction.user_id)
    if comment_ids is not None:
        if not comment_ids:
            return {}
        q = q.where(m.Reaction.comment_id.in_(comment_ids))
    else:
        if not post_ids:
            return {}
        q = q.where(m.Reaction.post_id.in_(post_ids))
    out: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
    for r, name in (await db.execute(q.order_by(m.Reaction.created_at))).all():
        key = r.comment_id or r.post_id
        entry = out.setdefault(key, {}).setdefault(r.emoji, {"emoji": r.emoji, "count": 0, "user_ids": [], "names": []})
        entry["count"] += 1
        entry["user_ids"].append(str(r.user_id))
        entry["names"].append(name)
    return {k: list(v.values()) for k, v in out.items()}


async def of_one(db: AsyncSession, *, comment_id: uuid.UUID | None = None, post_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    got = await (summaries(db, comment_ids=[comment_id]) if comment_id else summaries(db, post_ids=[post_id]))
    return got.get(comment_id or post_id, [])  # type: ignore[arg-type]


# ---------- reacting ----------

async def react(db: AsyncSession, user: m.User, project_id: uuid.UUID, emoji: str, *, add: bool,
                comment_id: uuid.UUID | None = None, post_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Add (or take back) the person's reaction; returns the thing's reactions and tells open pages."""
    emoji = await check_emoji(db, emoji) if add else emoji.strip()
    target = m.Reaction.comment_id == comment_id if comment_id else m.Reaction.post_id == post_id
    mine = (await db.execute(select(m.Reaction).where(target, m.Reaction.user_id == user.id, m.Reaction.emoji == emoji))).scalar_one_or_none()
    added = False
    if add and mine is None:
        kinds = set((await db.execute(select(m.Reaction.emoji).where(target))).scalars())
        if emoji not in kinds and len(kinds) >= MAX_KINDS:
            raise ReactionError(f"it already has {MAX_KINDS} different reactions: add to one of them")
        db.add(m.Reaction(project_id=project_id, comment_id=comment_id, post_id=post_id, user_id=user.id, emoji=emoji))
        added = True
    elif not add and mine is not None:
        await db.delete(mine)
    await db.flush()
    reactions = await of_one(db, comment_id=comment_id, post_id=post_id)
    if added:
        await _tell_author(db, user, project_id, reactions, comment_id=comment_id, post_id=post_id)
    await db.commit()
    bus.publish("reactions.changed", project_id, {
        "comment_id": str(comment_id) if comment_id else None, "post_id": str(post_id) if post_id else None,
        "reactions": reactions})
    return reactions


async def _tell_author(db: AsyncSession, user: m.User, project_id: uuid.UUID, reactions: list[dict[str, Any]], *,
                       comment_id: uuid.UUID | None, post_id: uuid.UUID | None) -> None:
    """The author hears about reactions to what they wrote, in one notification per comment or post:
    a new one takes the place of the unread one before it ("Sam and 2 others reacted…")."""
    from .notifications import clear, notify

    if comment_id:
        thing = await db.get(m.Comment, comment_id)
        task = await db.get(m.Task, thing.task_id) if thing else None
        where = f"your comment on “{task.title}”" if task else "your comment"
        link = f"/projects/{project_id}?task={thing.task_id}&comment={comment_id}" if thing else ""
    else:
        thing = await db.get(m.ProjectPost, post_id)
        where, link = "your post", f"/projects/{project_id}/dashboard?post={post_id}"
    if thing is None or thing.author_id is None or thing.author_id == user.id:
        return
    author = str(thing.author_id)
    people: list[str] = []
    for r in reactions:
        for uid, name in zip(r["user_ids"], r["names"], strict=False):
            if uid != author and name not in people:
                people.append(name)
    if not people:
        return
    who = people[0] if len(people) == 1 else f"{people[0]} and {people[1]}" if len(people) == 2 else \
        f"{people[0]} and {len(people) - 1} others"
    said = " · ".join(f"{r['emoji']} {r['count']}" for r in reactions)
    excerpt = " ".join(thing.body.split())
    subject = f"reaction:{comment_id or post_id}"
    await clear(db, subject)
    await notify(db, kind="reaction", title=f"{who} reacted to {where}", body=f"{said}\n{excerpt[:160]}{'…' if len(excerpt) > 160 else ''}",
                 link=link, project_id=project_id, subject=subject, user_ids=[thing.author_id], webhooks=False,
                 payload={"comment_id": str(comment_id) if comment_id else None, "post_id": str(post_id) if post_id else None})


# ---------- custom emoji ----------

def directory() -> Path:
    path = settings.data_dir / "emojis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def emoji_out(e: m.CustomEmoji) -> dict[str, Any]:
    return {"name": e.name, "url": f"/api/emojis/{e.name}/image?v={e.file.rsplit('-', 1)[-1].split('.')[0]}",
            "created_at": e.created_at.isoformat() if e.created_at else None}


def save(name: str, data: bytes) -> tuple[str, str]:
    """Store an upload; returns (file name, content type)."""
    if len(data) > MAX_BYTES:
        raise ReactionError(f"the image is larger than {MAX_BYTES // 1024} KB")
    kind = image_type(data)
    if kind is None:
        raise ReactionError("upload a PNG, GIF, WebP or JPEG image")
    file = f"{name}-{uuid.uuid4().hex[:8]}.{kind[1]}"
    (directory() / file).write_bytes(data)
    return file, kind[0]


def remove_file(file: str | None) -> None:
    if file and "/" not in file and "\\" not in file:
        (directory() / file).unlink(missing_ok=True)
