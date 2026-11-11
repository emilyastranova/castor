"""Global search (Phase 3g): everything a user can see, from one box.

Searches the live tables (no separate index to keep in sync): every word of the query must
appear in at least one of a thing's fields, case-insensitively. Works the same on SQLite and
Postgres. Prefix filters narrow it:

    in:NWL26-001   one project (by codename)
    type:comment   one kind of thing (see TYPES), or several: type:card,comment
    by:sam         made by someone (cards, comments, posts, runs, notes)
    tag:smb        with a tag (cards, posts' #hashtags, commands, playbooks, agents)
    is:mine        yours (assigned to or opened by you; comments that mention you)
    status:failed  a run's status, or a card's latest run

Ranking: every hit is scored (`score`) by how closely its name matches the query (the whole
query being a thing's exact name or key, an asset's address or domain, a project's codename, a
username… beats a name that starts with it, which beats a name that contains it, which beats
text that only mentions it) plus a little for the kind of thing (things before mentions of them).
Groups are ordered by their best hit, hits within a group by score, and the closest hits are also
lifted into a "Best match" group at the top. Each query also fetches exact names first
(`_closest_first`), so an exact match can't fall outside a group's limit.

Never searched: secret values (variable-set secrets live in their own encrypted column),
passwords, tokens.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, and_, case, cast, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import parsers
from .access import visible_project_ids
from .api_tokens import project_limit

# (type, heading) in the order groups are shown.
TYPES: list[tuple[str, str]] = [
    ("card", "Cards"),
    ("comment", "Comments"),
    ("post", "Timeline posts"),
    ("output", "Results"),
    ("log", "Console output"),
    ("file", "Files and screenshots"),
    ("run", "Playbook runs"),
    ("note", "Pinned notes"),
    ("asset", "Assets"),
    ("project", "Projects"),
    ("playbook", "Playbooks"),
    ("command", "Commands"),
    ("tool", "Tools"),
    ("parser", "Parsers"),
    ("variable", "Variables"),
    ("person", "People"),
    ("agent", "Agents"),
]
TYPE_NAMES = {t for t, _ in TYPES}
ALIASES = {"cards": "card", "task": "card", "tasks": "card", "comments": "comment", "results": "output",
           "outputs": "output", "logs": "log", "console": "log", "files": "file", "screenshot": "file",
           "screenshots": "file", "attachment": "file", "runs": "run", "notes": "note", "posts": "post", "projects": "project",
           "playbooks": "playbook", "commands": "command", "tools": "tool", "parsers": "parser",
           "variables": "variable", "vars": "variable", "people": "person", "user": "person", "assets": "asset",
           "host": "asset", "hosts": "asset", "service": "asset", "services": "asset", "domain": "asset", "domains": "asset",
           "users": "person", "agents": "agent"}
BY_TYPES = {"card", "comment", "post", "run", "note"}
TAG_TYPES = {"card", "post", "command", "playbook", "agent"}
STATUS_TYPES = {"card", "run"}
MINE_TYPES = {"card", "comment", "post"}
FILTER = re.compile(r"(?:^|\s)(in|type|by|tag|is|status):(\"[^\"]*\"|\S+)", re.IGNORECASE)


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)
    project: str | None = None
    types: set[str] | None = None
    by: str | None = None
    tags: list[str] = field(default_factory=list)
    mine: bool = False
    status: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.terms or self.project or self.by or self.tags or self.mine or self.status)


def parse(text: str) -> Query:
    q = Query()
    for key, raw in FILTER.findall(text):
        value = raw.strip('"')
        key = key.lower()
        if key == "in":
            q.project = value
        elif key == "type":
            names = {ALIASES.get(v.lower(), v.lower()) for v in value.split(",")}
            q.types = (q.types or set()) | (names & TYPE_NAMES)
        elif key == "by":
            q.by = value.lstrip("@").lower()
        elif key == "tag":
            q.tags.append(value.lower())
        elif key == "is" and value.lower() in ("mine", "me"):
            q.mine = True
        elif key == "status":
            q.status = value.lower()
    rest = FILTER.sub(" ", text)
    q.terms = [t for t in re.findall(r'"[^"]+"|\S+', rest.lower()) if t.strip('"')]
    q.terms = [t.strip('"') for t in q.terms][:8]
    return q


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _match(columns: list, terms: list[str]):
    """Every term in at least one of the columns."""
    if not terms:
        return true()
    return and_(*[or_(*[c.ilike(_like(t), escape="\\") for c in columns]) for t in terms])


def _text(column):
    return cast(column, String)


def _closest_first(columns: list, terms: list[str]):
    """An ORDER BY that puts rows whose name is exactly the query first, then those starting
    with it (before the table's own order)."""
    if not terms:
        return case((true(), 2), else_=2)
    phrase = " ".join(terms)
    exact = or_(*[func.lower(c) == phrase for c in columns])
    starts = or_(*[c.ilike(_like(phrase)[1:], escape="\\") for c in columns])
    return case((exact, 0), (starts, 1), else_=2)


# How much each kind counts beyond how closely it matches: things before mentions of them.
KIND_WEIGHT = {"asset": 8, "project": 8, "card": 6, "person": 6, "agent": 5, "command": 5, "playbook": 5, "tool": 5,
               "parser": 4, "note": 4, "run": 4, "variable": 3, "post": 3, "comment": 2, "output": 2, "file": 2, "log": 0}
BEST = 80  # a hit this close is a best match


def score(kind: str, hit: dict[str, Any], terms: list[str]) -> int:
    """How well a hit answers the query (0–108): its name or key exactly the query (100), starting
    with it (80), or a word of it (70); its name holding every term (60), some (45); only its text (25)."""
    if not terms:
        return KIND_WEIGHT.get(kind, 0)
    phrase = " ".join(terms)
    names = [str(x).lower() for x in [hit.get("title"), *hit.get("keys", [])] if x]
    if any(n == phrase for n in names):
        base = 100
    elif any(n.startswith(phrase) for n in names):
        base = 80
    elif any(phrase in re.split(r"[\s·,/@()\[\]]+", n) for n in names):
        base = 70
    elif any(all(t in n for t in terms) for n in names):
        base = 60
    elif any(any(t in n for t in terms) for n in names):
        base = 45
    else:
        base = 25
    return base + KIND_WEIGHT.get(kind, 0)


def snippet(text: str | None, terms: list[str], width: int = 80) -> str:
    """The part of a text around the first matching term, on one line."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    low = flat.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    at = min(hits) if hits else 0
    start = max(0, at - width // 2)
    end = min(len(flat), start + width * 2)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _log_line(data: str, terms: list[str]) -> str:
    for line in data.splitlines():
        low = line.lower()
        if all(t in low for t in terms) or any(t in low for t in terms):
            line = line.strip()
            return line if len(line) <= 200 else snippet(line, terms, 90)
    return snippet(data, terms)


class Searcher:
    def __init__(self, db: AsyncSession, user: m.User, q: Query, limit: int):
        self.db, self.user, self.q, self.limit = db, user, q, limit
        self.projects: dict[uuid.UUID, m.Project] = {}
        self.scope: set[uuid.UUID] | None = None

    async def prepare(self) -> bool:
        visible = await visible_project_ids(self.db, self.user)
        rows = (await self.db.execute(select(m.Project))).scalars().all()
        self.projects = {p.id: p for p in rows if visible is None or p.id in visible}
        self.scope = set(self.projects)
        if self.q.project:
            wanted = {pid for pid, p in self.projects.items() if p.codename.lower() == self.q.project.lower()}
            if not wanted:
                return False
            self.scope = wanted
        return True

    def in_scope(self, column):
        return column.in_(self.scope) if self.scope else false()

    def project_ref(self, pid) -> dict[str, str] | None:
        p = self.projects.get(pid)
        return {"id": str(p.id), "codename": p.codename, "name": p.name} if p else None

    def wants(self, kind: str) -> bool:
        q = self.q
        if q.types is not None and kind not in q.types:
            return False
        if q.by and kind not in BY_TYPES:
            return False
        if q.tags and kind not in TAG_TYPES:
            return False
        if q.mine and kind not in MINE_TYPES:
            return False
        if q.status and kind not in STATUS_TYPES:
            return False
        # Things outside projects don't belong to a project filter.
        return not (q.project and kind in {"playbook", "command", "tool", "parser", "variable", "person", "agent"})

    async def by_user_id(self) -> uuid.UUID | None:
        if not self.q.by:
            return None
        row = await self.db.execute(select(m.User.id).where(m.User.username.ilike(self.q.by)))
        return row.scalar_one_or_none() or uuid.UUID(int=0)

    async def rows(self, query) -> list:
        return list((await self.db.execute(query.limit(self.limit + 1))).all())

    # ---- each kind ----

    async def card(self) -> list[dict[str, Any]]:
        q, t = self.q, m.Task
        cols = [t.title, t.description, t.command, _text(t.tags), _text(t.custom_fields)]
        cond = [self.in_scope(t.project_id), _match(cols, q.terms)]
        cond += [_text(t.tags).ilike(_like(f'"{tag}"'), escape="\\") for tag in q.tags]
        if (by := await self.by_user_id()) is not None:
            cond.append(t.created_by == by)
        if q.mine:
            from .people import my_group_ids

            by_group = [_text(t.group_assignees).ilike(_like(g), escape="\\") for g in await my_group_ids(self.db, self.user.id)]
            cond.append(or_(_text(t.assignees).ilike(_like(str(self.user.id)), escape="\\"), t.created_by == self.user.id, *by_group))
        if q.status:
            latest = select(m.Job.status).where(m.Job.task_id == t.id).order_by(m.Job.queued_at.desc()).limit(1).scalar_subquery()
            cond.append(cast(latest, String).ilike(q.status))
        rows = await self.rows(select(t).where(*cond).order_by(_closest_first([t.title], q.terms), t.updated_at.desc()))
        out = []
        for (task,) in rows:
            text = next((v for v in (task.title, task.description, task.command, " ".join(task.tags)) if any(x in (v or "").lower() for x in q.terms)), task.description)
            out.append({
                "id": str(task.id), "title": task.title, "snippet": snippet(text, q.terms) if text != task.title else "",
                "project": self.project_ref(task.project_id), "at": task.updated_at,
                "icon": "terminal" if task.kind == m.TaskKind.command else "person",
                "link": f"/projects/{task.project_id}?task={task.id}",
            })
        return out

    async def comment(self) -> list[dict[str, Any]]:
        q, c, t = self.q, m.Comment, m.Task
        cond = [self.in_scope(t.project_id), _match([c.body], q.terms)]
        if (by := await self.by_user_id()) is not None:
            cond.append(c.author_id == by)
        if q.mine:
            cond.append(_text(c.mentions).ilike(_like(str(self.user.id)), escape="\\"))
        rows = await self.rows(
            select(c, t, m.User.display_name).join(t, t.id == c.task_id).outerjoin(m.User, m.User.id == c.author_id)
            .where(*cond).order_by(c.created_at.desc())
        )
        return [{
            "id": str(cm.id), "title": f"{author or 'Someone'} on “{task.title}”", "snippet": snippet(cm.body, q.terms),
            "project": self.project_ref(task.project_id), "at": cm.created_at, "icon": "chat_bubble",
            "link": f"/projects/{task.project_id}?task={task.id}&comment={cm.id}",
        } for cm, task, author in rows]

    async def post(self) -> list[dict[str, Any]]:
        q, p = self.q, m.ProjectPost
        cond = [self.in_scope(p.project_id), _match([p.body], q.terms)]
        if (by := await self.by_user_id()) is not None:
            cond.append(p.author_id == by)
        cond += [_text(p.tags).ilike(_like(f'"{tag}"'), escape="\\") for tag in q.tags]
        if q.mine:
            cond.append(_text(p.mentions).ilike(_like(str(self.user.id)), escape="\\"))
        rows = await self.rows(
            select(p, m.User.display_name).outerjoin(m.User, m.User.id == p.author_id).where(*cond).order_by(p.created_at.desc())
        )
        return [{
            "id": str(post.id), "title": f"{author or 'Someone'} on the timeline", "snippet": snippet(post.body, q.terms),
            "project": self.project_ref(post.project_id), "at": post.created_at, "icon": "edit_note",
            "link": f"/projects/{post.project_id}/dashboard?post={post.id}",
        } for post, author in rows]

    async def output(self) -> list[dict[str, Any]]:
        q, o, j, t = self.q, m.JobOutput, m.Job, m.Task
        if not q.terms:
            return []
        rows = await self.rows(
            select(o, t).join(j, j.id == o.job_id).join(t, t.id == j.task_id)
            .where(self.in_scope(t.project_id), _match([o.name, _text(o.value)], q.terms)).order_by(o.created_at.desc())
        )
        return [{
            "id": str(out.id), "title": f"{out.name} · {task.title}", "snippet": snippet(json.dumps(out.value, default=str), q.terms),
            "project": self.project_ref(task.project_id), "at": out.created_at, "icon": "table_view",
            "link": f"/projects/{task.project_id}?task={task.id}&tab=output&job={out.job_id}&view=results",
        } for out, task in rows]

    async def log(self) -> list[dict[str, Any]]:
        q, ch, j, t = self.q, m.JobLogChunk, m.Job, m.Task
        if not q.terms:
            return []
        rows = (await self.db.execute(
            select(ch, t).join(j, j.id == ch.job_id).join(t, t.id == j.task_id)
            .where(self.in_scope(t.project_id), _match([ch.data], q.terms)).order_by(ch.ts.desc()).limit(self.limit * 6)
        )).all()
        seen: set[uuid.UUID] = set()
        out = []
        for chunk, task in rows:
            if chunk.job_id in seen:
                continue  # one hit per run
            seen.add(chunk.job_id)
            out.append({
                "id": f"{chunk.job_id}:{chunk.seq}", "title": task.title, "snippet": _log_line(chunk.data, q.terms),
                "project": self.project_ref(task.project_id), "at": chunk.ts, "icon": "terminal", "mono": True,
                "link": f"/projects/{task.project_id}?task={task.id}&tab=output&job={chunk.job_id}",
            })
        return out[: self.limit + 1]

    async def file(self) -> list[dict[str, Any]]:
        q, a = self.q, m.Attachment
        if not q.terms:
            return []
        rows = await self.rows(select(a, m.Task.title).outerjoin(m.Task, m.Task.id == a.task_id)
                               .where(self.in_scope(a.project_id), _match([a.filename], q.terms)).order_by(a.created_at.desc()))
        return [{
            "id": str(att.id), "title": att.filename, "snippet": f"on “{title}”" if title else "in a pinned note",
            "project": self.project_ref(att.project_id), "at": att.created_at,
            "icon": "image" if att.content_type.startswith("image/") else "attach_file",
            "link": f"/projects/{att.project_id}?task={att.task_id}&tab=attachments" if att.task_id else f"/projects/{att.project_id}/dashboard",
        } for att, title in rows]

    async def run(self) -> list[dict[str, Any]]:
        q, r = self.q, m.PlaybookRun
        cond = [self.in_scope(r.project_id), _match([r.playbook_name, r.playbook_id, _text(r.inputs)], q.terms)]
        if (by := await self.by_user_id()) is not None:
            cond.append(r.created_by == by)
        if q.status:
            cond.append(cast(r.status, String).ilike(q.status))
        rows = await self.rows(select(r).where(*cond).order_by(r.created_at.desc()))
        return [{
            "id": str(run.id), "title": run.playbook_name, "snippet": f"{run.status.value} · " + ", ".join(f"{k}={v}" for k, v in (run.inputs or {}).items()),
            "project": self.project_ref(run.project_id), "at": run.created_at, "icon": "account_tree",
            "link": f"/playbooks?run={run.id}",
        } for (run,) in rows]

    async def note(self) -> list[dict[str, Any]]:
        q, n = self.q, m.ProjectNote
        cond = [self.in_scope(n.project_id), _match([n.title, n.body], q.terms)]
        if (by := await self.by_user_id()) is not None:
            cond.append(n.updated_by == by)
        rows = await self.rows(select(n).where(*cond).order_by(n.updated_at.desc()))
        return [{
            "id": str(note.id), "title": note.title, "snippet": snippet(note.body, q.terms),
            "project": self.project_ref(note.project_id), "at": note.updated_at, "icon": "push_pin",
            "link": f"/projects/{note.project_id}/dashboard",
        } for (note,) in rows]

    async def asset(self) -> list[dict[str, Any]]:
        from . import assets as A

        q, a = self.q, m.Asset
        if not q.terms:
            return []
        rows = await self.rows(select(a).where(self.in_scope(a.project_id), a.status == "confirmed",
                                               _match([a.label, a.key, _text(a.fields), a.description], q.terms))
                               .order_by(_closest_first([a.key, a.label], q.terms), a.last_seen.desc()))
        out = []
        for (asset,) in rows:
            t = A.TYPES.get(asset.type)
            extra = ", ".join(f"{k}={v}" for k, v in (asset.fields or {}).items() if any(x in f"{k} {v}".lower() for x in q.terms))
            f = asset.fields or {}
            keys = [asset.key, *(str(f[k]) for k in ("ip", "hostname", "name", "url", "username", "id") if f.get(k))]
            out.append({"id": str(asset.id), "title": asset.label, "keys": keys,
                        "snippet": f"{t.label if t else asset.type}" + (f" · {extra}" if extra else ""),
                        "project": self.project_ref(asset.project_id), "at": asset.last_seen, "icon": t.icon if t else "radar",
                        "link": f"/projects/{asset.project_id}/assets?asset={asset.id}"})
        return out

    async def project(self) -> list[dict[str, Any]]:
        q, p = self.q, m.Project
        if not q.terms:
            return []
        rows = await self.rows(select(p, m.Client.name).outerjoin(m.Client, m.Client.id == p.client_id)
                               .where(self.in_scope(p.id), _match([p.codename, p.name, p.description, m.Client.name], q.terms))
                               .order_by(_closest_first([p.codename, p.name], q.terms), p.updated_at.desc()))
        return [{
            "id": str(pr.id), "title": f"{pr.codename} · {pr.name}", "keys": [pr.codename, pr.name],
            "snippet": client or snippet(pr.description, q.terms),
            "project": None, "at": pr.updated_at, "icon": "view_kanban", "link": f"/projects/{pr.id}",
        } for pr, client in rows]

    async def playbook(self) -> list[dict[str, Any]]:
        q, p = self.q, m.PlaybookDefinition
        cond = [_match([p.id, p.name, p.description, _text(p.tags)], q.terms)]
        cond += [_text(p.tags).ilike(_like(f'"{tag}"'), escape="\\") for tag in q.tags]
        rows = await self.rows(select(p).where(*cond).order_by(_closest_first([p.id, p.name], q.terms), p.name))
        return [{"id": pb.id, "title": pb.name, "keys": [pb.id], "snippet": pb.description, "project": None, "at": None,
                 "icon": "account_tree", "link": f"/playbooks/edit/{pb.id}"} for (pb,) in rows]

    async def command(self) -> list[dict[str, Any]]:
        q, c = self.q, m.CommandDefinition
        cond = [_match([c.id, c.name, c.description, _text(c.tags)], q.terms)]
        cond += [_text(c.tags).ilike(_like(f'"{tag}"'), escape="\\") for tag in q.tags]
        rows = await self.rows(select(c).where(*cond).order_by(_closest_first([c.id, c.name], q.terms), c.name))
        return [{"id": cd.id, "title": cd.name, "keys": [cd.id], "snippet": cd.description or cd.id, "project": None, "at": None,
                 "icon": "menu_book", "link": f"/library?c={cd.id}"} for (cd,) in rows]

    async def tool(self) -> list[dict[str, Any]]:
        q, t = self.q, m.ToolDefinition
        if not q.terms:
            return []
        rows = await self.rows(select(t).where(_match([t.id, t.name, t.description], q.terms))
                               .order_by(_closest_first([t.id, t.name], q.terms), t.name))
        return [{"id": td.id, "title": td.name, "keys": [td.id], "snippet": td.description, "project": None, "at": None,
                 "icon": "handyman", "link": f"/library?t={td.id}"} for (td,) in rows]

    async def parser(self) -> list[dict[str, Any]]:
        if not self.q.terms:
            return []
        out = []
        for p in parsers.all_parsers():
            hay = f"{p.name} {p.type} {p.description}".lower()
            if all(t in hay for t in self.q.terms):
                out.append({"id": p.name, "title": p.name, "snippet": p.description, "project": None, "at": None,
                            "icon": "data_object", "link": f"/parsers?parser={p.name}"})
        return out[: self.limit + 1]

    async def variable(self) -> list[dict[str, Any]]:
        from .api.variables import _can_see_set

        q, s, k = self.q, m.VariableSet, m.VariableKind
        if not q.terms:
            return []
        visible = None if self.user.is_admin and project_limit(self.user) is None else self.scope
        # Non-secret values are searchable (target lists, domains); secrets are a separate column.
        sets = (await self.db.execute(
            select(s, k.name).outerjoin(k, k.id == s.kind_id)
            .where(s.ephemeral.is_(False), _match([s.name, s.description, _text(s.values), k.name], q.terms))
            .order_by(s.name).limit(self.limit * 3)
        )).all()
        out = [{"id": str(vs.id), "title": vs.name, "snippet": kind or "", "project": None, "at": None,
                "icon": "key", "link": "/variables"} for vs, kind in sets if _can_see_set(vs, visible)]
        kinds = (await self.db.execute(select(k).where(_match([k.id, k.name, k.description], q.terms)).order_by(k.name).limit(self.limit))).all()
        out += [{"id": f"kind:{kd.id}", "title": f"{kd.name} (kind)", "snippet": kd.description, "project": None, "at": None,
                 "icon": "category", "link": "/variables"} for (kd,) in kinds]
        return out[: self.limit + 1]

    async def person(self) -> list[dict[str, Any]]:
        q, u = self.q, m.User
        if not q.terms:
            return []
        rows = await self.rows(select(u).where(u.disabled_at.is_(None), _match([u.username, u.display_name], q.terms))
                               .order_by(_closest_first([u.username, u.display_name], q.terms), u.display_name))
        return [{"id": str(p.id), "title": p.display_name, "keys": [p.username, f"@{p.username}"], "snippet": f"@{p.username} · {p.role.value}", "project": None,
                 "at": None, "icon": "person", "avatar": p.avatar_url, "link": f"/people/{p.username}"} for (p,) in rows]

    async def agent(self) -> list[dict[str, Any]]:
        q, a = self.q, m.Agent
        cond = [_match([a.name, a.hostname, a.os, _text(a.tags)], q.terms)]
        cond += [_text(a.tags).ilike(_like(f'"{tag}"'), escape="\\") for tag in q.tags]
        rows = await self.rows(select(a).where(*cond).order_by(_closest_first([a.name, a.hostname], q.terms), a.name))
        return [{"id": str(ag.id), "title": ag.name, "keys": [ag.hostname], "snippet": " · ".join(x for x in (ag.hostname, ag.status.value, ", ".join(ag.tags or [])) if x),
                 "project": None, "at": ag.last_seen_at, "icon": "dns", "link": "/agents"} for (ag,) in rows]


async def search(db: AsyncSession, user: m.User, text: str, limit: int = 6) -> dict[str, Any]:
    q = parse(text)
    if q.empty:
        return {"query": text, "groups": []}
    single = q.types is not None and len(q.types) == 1
    searcher = Searcher(db, user, q, limit=25 if single else limit)
    if not await searcher.prepare():
        return {"query": text, "groups": [], "note": f"No project {q.project} that you can see"}
    scored: list[tuple[int, int, str, str, list[tuple[int, dict[str, Any]]], bool]] = []
    for order, (kind, heading) in enumerate(TYPES):
        if not searcher.wants(kind):
            continue
        hits = await getattr(searcher, kind)()
        if not hits:
            continue
        more = len(hits) > searcher.limit
        # Score what was fetched (a group fetches one past its limit), best first, recency kept on ties.
        ranked = sorted(((score(kind, h, q.terms), h) for h in hits), key=lambda x: -x[0])
        scored.append((ranked[0][0], order, kind, heading, ranked, more))
    # The closest hits of any kind lead, in a group of their own (and leave their own group).
    best: list[tuple[int, int, dict[str, Any]]] = []
    if q.terms and not single:
        for _, order, kind, _, ranked, _ in scored:
            best += [(s_, order, {**h, "type": kind}) for s_, h in ranked if s_ >= BEST]
        best = sorted(best, key=lambda x: (-x[0], x[1]))[:3]
    lifted = {(b[2]["type"], b[2]["id"]) for b in best}
    groups = []
    if best:
        groups.append({"type": "best", "label": "Best match", "hits": [_public(h) for _, _, h in best], "more": False})
    for top, order, kind, heading, ranked, more in sorted(scored, key=lambda x: (-x[0], x[1])):
        hits = [_public(h) for _, h in ranked if (kind, h["id"]) not in lifted][: searcher.limit]
        if hits:
            groups.append({"type": kind, "label": heading, "hits": hits, "more": more})
    return {"query": text, "groups": groups}


def _public(hit: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in hit.items() if k != "keys"}
