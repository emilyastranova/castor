"""Assets (Phase 3f): a project's inventory of what it found, its scope, and triggers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import assets as A
from .. import models as m
from .. import notifications, vault
from ..access import check_project, project_or_404
from ..deps import DB, CurrentUser, parse_uuid
from ..events import bus
from ..services import actor_of, record_activity

router = APIRouter(tags=["assets"])


class AssetBrief(BaseModel):
    id: uuid.UUID
    type: str
    key: str
    label: str
    fields: dict[str, Any]
    has_secret: bool
    in_scope: bool
    first_seen: datetime
    last_seen: datetime
    links: int = 0
    status: str = "confirmed"  # confirmed | suggested | dismissed
    confidence: int = 80
    # Suggested ones: where they were mentioned, newest first (kind, excerpt, link, at).
    mentions: list[dict[str, Any]] = Field(default_factory=list)


class AssetList(BaseModel):
    items: list[AssetBrief]
    total: int
    counts: dict[str, int]
    # Assets only mentioned (comments, output, pictures…), waiting to be confirmed or dismissed.
    suggested: int = 0


def _brief(a: m.Asset, project: m.Project, links: int = 0, mentions: list[dict[str, Any]] | None = None) -> AssetBrief:
    return AssetBrief(id=a.id, type=a.type, key=a.key, label=a.label, fields=a.fields or {}, has_secret=bool(a.secret),
                      in_scope=A.in_scope(project, a), first_seen=a.first_seen, last_seen=a.last_seen, links=links,
                      status=a.status or "confirmed", confidence=a.confidence or 0, mentions=mentions or [])


SOURCE_WORDS = {"comment": "a comment", "post": "a timeline post", "card": "a card's description", "note": "a pinned note",
                "output": "a run's output", "ai": "a picture (read by the AI)", "parser": "a tool's results", "manual": "added by hand",
                "import": "an import"}


def ref_link(project_id: uuid.UUID, ref: dict[str, Any] | None, task_id: uuid.UUID | None, job_id: uuid.UUID | None) -> str | None:
    """Where a mention is: the comment, post, card, note, picture or run."""
    ref = ref or {}
    kind, rid = ref.get("kind"), ref.get("id")
    card = f"/projects/{project_id}?task={task_id}" if task_id else None
    if kind == "comment" and card:
        return f"{card}&comment={rid}"
    if kind == "post":
        return f"/projects/{project_id}/dashboard?post={rid}"
    if kind == "note":
        return f"/projects/{project_id}/dashboard"
    if kind == "job" and card:
        return f"{card}&tab=output&job={job_id or rid}"
    if kind == "attachment" and card:
        return f"{card}&tab=attachments"
    if kind == "attachment" and rid:
        return f"/api/attachments/{rid}"
    return card


async def _mentions(db: DB, assets: list[m.Asset]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """For suggested assets: the places they were mentioned (up to three each), newest first."""
    if not assets:
        return {}
    rows = (await db.execute(select(m.AssetObservation).where(m.AssetObservation.asset_id.in_([a.id for a in assets]))
                             .order_by(m.AssetObservation.observed_at.desc()))).scalars().all()
    project_of = {a.id: a.project_id for a in assets}
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for o in rows:
        seen = out.setdefault(o.asset_id, [])
        if len(seen) < 3:
            seen.append({"source": o.source, "where": SOURCE_WORDS.get(o.source, o.source), "excerpt": (o.ref or {}).get("excerpt", ""),
                         "name": (o.ref or {}).get("name"), "at": o.observed_at,
                         "link": ref_link(project_of[o.asset_id], o.ref, o.task_id, o.job_id)})
    return out


async def _asset_or_404(db: DB, user: m.User, asset_id: str, *, write: bool = False) -> m.Asset:
    a = await db.get(m.Asset, parse_uuid(asset_id))
    if a is None:
        raise HTTPException(404, "Asset not found")
    try:
        await check_project(db, user, a.project_id, write=write)
    except HTTPException as err:
        if err.status_code == 404:
            raise HTTPException(404, "Asset not found") from None
        raise
    return a


@router.get("/assets/types")
async def asset_types(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "label": t.label, "plural": t.plural, "icon": t.icon, "identity": list(t.identity),
         "default_field": A.default_field(t.name),
         "fields": [{"name": f.name, "label": f.label, "type": f.type} for f in t.fields]}
        for t in A.TYPES.values()
    ]


@router.get("/projects/{project_id}/assets", response_model=AssetList)
async def list_assets(project_id: str, db: DB, user: CurrentUser, type: str | None = None, where: str = "",
                      limit: int = 200, offset: int = 0, status: Literal["confirmed", "suggested", "dismissed"] = "confirmed"):
    """`where` takes `field:value`, `field!=value`, `port>1000` and free words. `status`: the
    confirmed assets (the default), the suggested ones (only mentioned) or the dismissed ones."""
    project = await project_or_404(db, user, parse_uuid(project_id))
    try:
        rows = await A.query(db, project.id, type, where, status=status)
    except A.AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    counts_rows = await db.execute(select(m.Asset.type, func.count()).where(m.Asset.project_id == project.id, m.Asset.status == status)
                                   .group_by(m.Asset.type))
    suggested = (await db.execute(select(func.count()).select_from(m.Asset).where(m.Asset.project_id == project.id,
                                                                                  m.Asset.status == "suggested"))).scalar_one()
    page = rows[offset: offset + max(0, min(limit, 1000))]
    link_counts: dict[uuid.UUID, int] = {}
    if page:
        ids = [a.id for a in page]
        for fid, tid in (await db.execute(select(m.AssetLink.from_id, m.AssetLink.to_id).where(or_(m.AssetLink.from_id.in_(ids), m.AssetLink.to_id.in_(ids))))).all():
            for x in (fid, tid):
                if x in ids:
                    link_counts[x] = link_counts.get(x, 0) + 1
    mentions = await _mentions(db, page) if status != "confirmed" else {}
    return AssetList(items=[_brief(a, project, link_counts.get(a.id, 0), mentions.get(a.id)) for a in page], total=len(rows),
                     counts={t: n for t, n in counts_rows.all()}, suggested=suggested)


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    """The asset with where each value came from (and conflicting values), its links and history."""
    a = await _asset_or_404(db, user, asset_id)
    project = await db.get(m.Project, a.project_id)
    obs = (await db.execute(select(m.AssetObservation).where(m.AssetObservation.asset_id == a.id).order_by(m.AssetObservation.observed_at.desc()).limit(200))).scalars().all()
    tasks = {t.id: t for t in (await db.execute(select(m.Task).where(m.Task.id.in_({o.task_id for o in obs if o.task_id})))).scalars()}
    users = {u.id: u for u in (await db.execute(select(m.User).where(m.User.id.in_({o.actor_id for o in obs if o.actor_id})))).scalars()}

    def source(o: m.AssetObservation) -> dict[str, Any]:
        t = tasks.get(o.task_id) if o.task_id else None
        u = users.get(o.actor_id) if o.actor_id else None
        return {"kind": o.source, "parser": o.parser, "at": o.observed_at, "job_id": str(o.job_id) if o.job_id else None,
                "task": {"id": str(t.id), "title": t.title, "link": f"/projects/{t.project_id}?task={t.id}"} if t else None,
                "by": u.display_name if u else None, "confidence": o.confidence, "where": SOURCE_WORDS.get(o.source, o.source),
                "excerpt": (o.ref or {}).get("excerpt"), "link": ref_link(a.project_id, o.ref, o.task_id, o.job_id)}

    provenance: dict[str, list[dict[str, Any]]] = {}
    for o in obs:  # newest first: the first value seen per field is the current one
        for k, v in (o.fields or {}).items():
            seen = provenance.setdefault(k, [])
            if not any(x["value"] == v for x in seen):
                seen.append({"value": v, "source": source(o)})
    rows = (await db.execute(select(m.AssetLink).where(or_(m.AssetLink.from_id == a.id, m.AssetLink.to_id == a.id)))).scalars().all()
    others = {x.id: x for x in (await db.execute(select(m.Asset).where(m.Asset.id.in_({r.to_id if r.from_id == a.id else r.from_id for r in rows})))).scalars()}
    links = []
    for r in rows:
        outgoing = r.from_id == a.id
        other = others.get(r.to_id if outgoing else r.from_id)
        if other is None:
            continue
        words = A.LINKS.get(r.kind, (r.kind, r.kind))
        links.append({"kind": r.kind, "label": words[0] if outgoing else words[1],
                      "asset": {"id": str(other.id), "type": other.type, "label": other.label}})
    fires = (await db.execute(select(m.AssetTriggerFire, m.AssetTrigger.name).join(m.AssetTrigger, m.AssetTrigger.id == m.AssetTriggerFire.trigger_id)
                              .where(m.AssetTriggerFire.asset_id == a.id).order_by(m.AssetTriggerFire.created_at.desc()).limit(20))).all()
    return {
        **_brief(a, project).model_dump(mode="json"),
        "provenance": provenance,
        "links": sorted(links, key=lambda x: (x["label"], x["asset"]["label"])),
        "history": [{"fields": o.fields, "source": source(o)} for o in obs[:50]],
        "triggers": [{"id": str(f.id), "trigger": name, "status": f.status, "reason": f.reason, "at": f.created_at,
                      "task_id": str(f.task_id) if f.task_id else None, "run_id": str(f.run_id) if f.run_id else None} for f, name in fires],
    }


class AssetIn(BaseModel):
    type: str
    fields: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(None, max_length=10_000)


@router.post("/projects/{project_id}/assets", response_model=AssetBrief, status_code=201)
async def add_asset(project_id: str, body: AssetIn, db: DB, user: CurrentUser):
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    batch = A.Batch(project.id)
    try:
        if body.secret and A.type_of(body.type).name != "credential":
            raise A.AssetError("only credentials have a secret")
        asset = await A.observe(db, project.id, body.type, body.fields, batch=batch, source="manual", actor_id=user.id, secret=body.secret)
    except A.AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    await A.settle(db, batch, actor=user)
    return _brief(asset, project)


class ImportIn(BaseModel):
    type: Literal["host", "ip", "domain", "url", "user"]
    text: str = Field(max_length=500_000)


@router.post("/projects/{project_id}/assets/import")
async def import_assets(project_id: str, body: ImportIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    """One asset per line (or comma/space separated): addresses or hostnames, domains, URLs, users."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    field = {"host": None, "ip": "ip", "domain": "name", "url": "url", "user": "username"}[body.type]
    batch, skipped = A.Batch(project.id), []
    values = [v for v in body.text.replace(",", "\n").split() if v.strip()][:5000]
    for v in values:
        fields = {field: v} if field else ({"ip": v} if A._ip(v) else {"hostname": v})
        try:
            await A.observe(db, project.id, body.type, fields, batch=batch, source="import", actor_id=user.id)
        except A.AssetError:
            skipped.append(v)
    await A.settle(db, batch, actor=user)
    return {"added": len(batch.created()), "updated": sum(1 for c in batch.changes.values() if c.event == "updated"), "skipped": skipped[:50]}


@router.post("/projects/{project_id}/assets/collect")
async def collect_assets(project_id: str, db: DB, user: CurrentUser) -> dict[str, int]:
    """Record assets from the results the project's jobs already produced, and suggest the ones
    its comments, posts, cards and notes mention (once is enough; new ones are recorded as they
    come). Triggers don't fire for these."""
    from ..assets_text import mentions_on

    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    batch = await A.collect_past(db, project.id, user, mentions=mentions_on(project))
    return {"added": len(batch.created()), "updated": sum(1 for c in batch.changes.values() if c.event == "updated"),
            "suggested": len(batch.suggested)}


class AssetPatch(BaseModel):
    fields: dict[str, Any]


@router.patch("/assets/{asset_id}", response_model=AssetBrief)
async def edit_asset(asset_id: str, body: AssetPatch, db: DB, user: CurrentUser):
    """Set fields by hand (recorded as a manual observation; identity fields can't change)."""
    a = await _asset_or_404(db, user, asset_id, write=True)
    project = await db.get(m.Project, a.project_id)
    t = A.type_of(a.type)
    fields = {k: v for k, v in body.fields.items() if k not in t.identity}
    batch = A.Batch(a.project_id)
    a = await A.observe(db, a.project_id, a.type, {**a.fields, **fields}, batch=batch, source="manual", actor_id=user.id)
    await A.settle(db, batch, actor=user)
    return _brief(a, project)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, db: DB, user: CurrentUser):
    a = await _asset_or_404(db, user, asset_id, write=True)
    pid, label = a.project_id, a.label
    await db.delete(a)
    await record_activity(db, type="assets.deleted", message=f"Removed the {a.type} {label}", project_id=pid, **actor_of(user))
    await db.commit()
    bus.publish("assets.changed", pid, {"project_id": str(pid)})


async def _confirm(db: DB, asset: m.Asset, user: m.User) -> None:
    """A person says a suggested (or dismissed) asset is real: it joins the inventory, and to
    triggers it's new."""
    asset.status = "confirmed"
    asset.confidence = max(asset.confidence or 0, A.CONFIDENCE["manual"])
    db.add(m.AssetObservation(asset_id=asset.id, source="manual", actor_id=user.id, fields={}, confidence=A.CONFIDENCE["manual"]))
    await db.flush()


@router.post("/assets/{asset_id}/confirm", response_model=AssetBrief)
async def confirm_asset(asset_id: str, db: DB, user: CurrentUser):
    """A suggested asset is real: it joins the project's assets (and its triggers see it as new)."""
    a = await _asset_or_404(db, user, asset_id, write=True)
    project = await db.get(m.Project, a.project_id)
    if a.status != "confirmed":
        await _confirm(db, a, user)
        batch = A.Batch(a.project_id)
        batch.note(a, True, [])
        await record_activity(db, type="assets.confirmed", message=f"Confirmed the {a.type} {a.label}", project_id=a.project_id, **actor_of(user))
        await A.settle(db, batch, actor=None)
    return _brief(a, project)


@router.post("/assets/{asset_id}/dismiss", response_model=AssetBrief)
async def dismiss_asset(asset_id: str, db: DB, user: CurrentUser):
    """A suggestion isn't an asset (a version number, a typo…): hide it; mentions don't bring it
    back, but a tool or a person reporting it does."""
    a = await _asset_or_404(db, user, asset_id, write=True)
    project = await db.get(m.Project, a.project_id)
    if a.status != "dismissed":
        a.status = "dismissed"
        await db.commit()
        bus.publish("assets.changed", a.project_id, {"project_id": str(a.project_id)})
    return _brief(a, project)


class SuggestionsIn(BaseModel):
    action: Literal["confirm", "dismiss"]
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


@router.post("/projects/{project_id}/assets/suggested")
async def decide_suggestions(project_id: str, body: SuggestionsIn, db: DB, user: CurrentUser) -> dict[str, int]:
    """Confirm or dismiss several suggested assets at once."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    rows = (await db.execute(select(m.Asset).where(m.Asset.project_id == project.id, m.Asset.id.in_(body.ids),
                                                   m.Asset.status != "confirmed"))).scalars().all()
    batch = A.Batch(project.id)
    for a in rows:
        if body.action == "confirm":
            await _confirm(db, a, user)
            batch.note(a, True, [])
        else:
            a.status = "dismissed"
    if body.action == "confirm" and rows:
        await record_activity(db, type="assets.confirmed", message=f"Confirmed {A.describe(A._counts(rows))}", project_id=project.id,
                              **actor_of(user))
        await A.settle(db, batch, actor=None)
    else:
        await db.commit()
        bus.publish("assets.changed", project.id, {"project_id": str(project.id)})
    return {"done": len(rows)}


class SortIn(BaseModel):
    guidance: str = Field("", max_length=4000)
    # Which suggestions to sort (the ones shown); all waiting ones when empty.
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)


@router.post("/projects/{project_id}/assets/suggested/sort")
async def sort_suggestions(project_id: str, body: SortIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    """Ask the AI which suggested assets look real and which look like noise, steered by the
    person's guidance and the project's scope. It only advises: nothing changes until someone
    confirms or dismisses. Up to 200 at a time."""
    from .. import llm
    from ..assets_text import SORT_LIMIT
    from ..assets_text import sort_suggestions as sort

    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    stmt = select(m.Asset).where(m.Asset.project_id == project.id, m.Asset.status == "suggested")
    if body.ids:
        stmt = stmt.where(m.Asset.id.in_(body.ids))
    rows = sorted((await db.execute(stmt)).scalars().all(), key=A.sort_key)
    if not rows:
        return {"verdicts": [], "model": "", "judged": 0, "left": 0}
    try:
        out = await sort(db, project, rows, body.guidance, user)
    except llm.NotConfigured as exc:
        raise HTTPException(409, str(exc)) from exc
    except llm.AiError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**out, "left": max(0, len(rows) - SORT_LIMIT)}


class MergeIn(BaseModel):
    into: uuid.UUID


@router.post("/assets/{asset_id}/merge", response_model=AssetBrief)
async def merge_asset(asset_id: str, body: MergeIn, db: DB, user: CurrentUser):
    """This asset is the same thing as another (a host by name and by address, say): fold it
    into that one, with its history, links and values."""
    a = await _asset_or_404(db, user, asset_id, write=True)
    into = await _asset_or_404(db, user, str(body.into), write=True)
    if into.project_id != a.project_id:
        raise HTTPException(422, "into: an asset in the same project")
    label = a.label
    try:
        merged = await A.merge(db, a, into=into)
    except A.AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    await record_activity(db, type="assets.merged", message=f"Merged the {merged.type} {label} into {merged.label}",
                          project_id=merged.project_id, **actor_of(user))
    await db.commit()
    bus.publish("assets.changed", merged.project_id, {"project_id": str(merged.project_id)})
    return _brief(merged, await db.get(m.Project, merged.project_id))


@router.post("/assets/{asset_id}/reveal")
async def reveal_secret(asset_id: str, db: DB, user: CurrentUser) -> dict[str, str]:
    """A credential's secret (operators; recorded in the activity feed)."""
    a = await _asset_or_404(db, user, asset_id, write=True)
    if not a.secret:
        raise HTTPException(404, "No secret stored")
    await record_activity(db, type="assets.revealed", message=f"Revealed the secret of {a.label}", project_id=a.project_id, **actor_of(user))
    await db.commit()
    return {"secret": vault.decrypt(a.secret).decode()}


# ---------- scope ----------


class ScopeIn(BaseModel):
    include: list[str] = Field(default_factory=list, max_length=500)
    exclude: list[str] = Field(default_factory=list, max_length=500)


@router.get("/projects/{project_id}/scope")
async def get_scope(project_id: str, db: DB, user: CurrentUser) -> dict[str, list[str]]:
    return A.scope_of(await project_or_404(db, user, parse_uuid(project_id)))


@router.put("/projects/{project_id}/scope")
async def set_scope(project_id: str, body: ScopeIn, db: DB, user: CurrentUser) -> dict[str, list[str]]:
    """CIDRs, addresses and domains (a domain covers its subdomains); exclusions win."""
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    clean = {k: [x.strip() for x in getattr(body, k) if x.strip()] for k in ("include", "exclude")}
    project.settings = {**(project.settings or {}), "scope": clean}
    await record_activity(db, type="project.scope", message=f"Set the scope: {', '.join(clean['include'][:5]) or 'nothing'}"
                          + (" …" if len(clean["include"]) > 5 else ""), project_id=project.id, **actor_of(user))
    await db.commit()
    bus.publish("assets.changed", project.id, {"project_id": str(project.id)})
    return clean


# ---------- triggers ----------


class Condition(BaseModel):
    field: str
    op: Literal["equals", "not_equals", "contains", "exists", "missing", "gt", "lt", "gte", "lte"] = "equals"
    value: Any = None


class TriggerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    asset_type: str
    on_event: Literal["created", "updated", "any"] = "created"
    conditions: list[Condition] = Field(default_factory=list)
    action: Literal["command", "playbook"]
    target: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    once_per_asset: bool = True
    max_per_hour: int = Field(20, ge=1, le=1000)


def _trigger_out(t: m.AssetTrigger, counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {"id": str(t.id), "project_id": str(t.project_id), "name": t.name, "enabled": t.enabled, "asset_type": t.asset_type,
            "on_event": t.on_event, "conditions": t.conditions, "action": t.action, "target": t.target, "args": t.args,
            "once_per_asset": t.once_per_asset, "max_per_hour": t.max_per_hour, "fires": counts or {},
            "created_at": t.created_at.isoformat()}


async def _check_target(db: DB, body: TriggerIn) -> None:
    from ..command_library import load_spec
    from ..playbook_library import load_playbook_spec

    try:
        A.type_of(body.asset_type)
    except A.AssetError as exc:
        raise HTTPException(422, str(exc)) from exc
    found = await (load_playbook_spec(db, body.target) if body.action == "playbook" else load_spec(db, body.target))
    if found is None:
        raise HTTPException(422, f"No {body.action} {body.target}")


@router.get("/projects/{project_id}/asset-triggers")
async def list_triggers(project_id: str, db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    project = await project_or_404(db, user, parse_uuid(project_id))
    rows = (await db.execute(select(m.AssetTrigger).where(m.AssetTrigger.project_id == project.id).order_by(m.AssetTrigger.created_at))).scalars().all()
    counts: dict[uuid.UUID, dict[str, int]] = {}
    if rows:
        for tid, status, n in (await db.execute(select(m.AssetTriggerFire.trigger_id, m.AssetTriggerFire.status, func.count())
                                                .where(m.AssetTriggerFire.trigger_id.in_([t.id for t in rows]))
                                                .group_by(m.AssetTriggerFire.trigger_id, m.AssetTriggerFire.status))).all():
            counts.setdefault(tid, {})[status] = n
    return [_trigger_out(t, counts.get(t.id)) for t in rows]


@router.post("/projects/{project_id}/asset-triggers", status_code=201)
async def create_trigger(project_id: str, body: TriggerIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    project = await project_or_404(db, user, parse_uuid(project_id), write=True)
    await _check_target(db, body)
    t = m.AssetTrigger(project_id=project.id, created_by=user.id, **{**body.model_dump(), "asset_type": A.type_of(body.asset_type).name,
                                                                    "conditions": [c.model_dump() for c in body.conditions]})
    db.add(t)
    await db.flush()
    await record_activity(db, type="trigger.created", message=f"Added the trigger “{t.name}”", project_id=project.id, **actor_of(user))
    await db.commit()
    bus.publish("asset_triggers.changed", project.id, {"project_id": str(project.id)})
    return _trigger_out(t)


async def _trigger_or_404(db: DB, user: m.User, trigger_id: str, write: bool = False) -> m.AssetTrigger:
    t = await db.get(m.AssetTrigger, parse_uuid(trigger_id))
    if t is None:
        raise HTTPException(404, "Trigger not found")
    await check_project(db, user, t.project_id, write=write)
    return t


@router.put("/asset-triggers/{trigger_id}")
async def update_trigger(trigger_id: str, body: TriggerIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    t = await _trigger_or_404(db, user, trigger_id, write=True)
    await _check_target(db, body)
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    t.asset_type = A.type_of(body.asset_type).name
    t.conditions = [c.model_dump() for c in body.conditions]
    await record_activity(db, type="trigger.updated", message=f"Changed the trigger “{t.name}”", project_id=t.project_id, **actor_of(user))
    await db.commit()
    bus.publish("asset_triggers.changed", t.project_id, {"project_id": str(t.project_id)})
    return _trigger_out(t)


@router.delete("/asset-triggers/{trigger_id}", status_code=204)
async def delete_trigger(trigger_id: str, db: DB, user: CurrentUser):
    t = await _trigger_or_404(db, user, trigger_id, write=True)
    pid = t.project_id
    await db.delete(t)
    await record_activity(db, type="trigger.deleted", message=f"Removed the trigger “{t.name}”", project_id=pid, **actor_of(user))
    await db.commit()
    bus.publish("asset_triggers.changed", pid, {"project_id": str(pid)})


@router.get("/projects/{project_id}/asset-trigger-fires")
async def list_fires(project_id: str, db: DB, user: CurrentUser, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    project = await project_or_404(db, user, parse_uuid(project_id))
    q = (select(m.AssetTriggerFire, m.AssetTrigger, m.Asset).join(m.AssetTrigger, m.AssetTrigger.id == m.AssetTriggerFire.trigger_id)
         .join(m.Asset, m.Asset.id == m.AssetTriggerFire.asset_id).where(m.AssetTrigger.project_id == project.id))
    if status:
        q = q.where(m.AssetTriggerFire.status == status)
    rows = (await db.execute(q.order_by(m.AssetTriggerFire.created_at.desc()).limit(min(limit, 500)))).all()
    return [{"id": str(f.id), "status": f.status, "reason": f.reason, "at": f.created_at.isoformat(),
             "trigger": {"id": str(t.id), "name": t.name, "action": t.action, "target": t.target},
             "asset": {"id": str(a.id), "type": a.type, "label": a.label},
             "task_id": str(f.task_id) if f.task_id else None, "run_id": str(f.run_id) if f.run_id else None} for f, t, a in rows]


async def _fire_or_404(db: DB, user: m.User, fire_id: str) -> tuple[m.AssetTriggerFire, m.AssetTrigger, m.Asset]:
    f = await db.get(m.AssetTriggerFire, parse_uuid(fire_id))
    if f is None:
        raise HTTPException(404, "Not found")
    t = await db.get(m.AssetTrigger, f.trigger_id)
    a = await db.get(m.Asset, f.asset_id)
    await check_project(db, user, t.project_id, write=True)
    if f.status != "waiting":
        raise HTTPException(409, f"This one is already {f.status}")
    return f, t, a


@router.post("/asset-trigger-fires/{fire_id}/approve")
async def approve_fire(fire_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    """Run a trigger that waited (its asset was outside the scope)."""
    f, t, a = await _fire_or_404(db, user, fire_id)
    ok = await A.run_fire(db, f, t, a, user)
    await record_activity(db, type="trigger.approved", message=f"Approved “{t.name}” for {a.label}", project_id=t.project_id, **actor_of(user))
    await notifications.clear(db, f"trigger-fire:{f.id}")
    await db.commit()
    bus.publish("asset_triggers.changed", t.project_id, {"project_id": str(t.project_id)})
    if ok:
        from ..orchestrator.dispatcher import dispatcher

        dispatcher.wake()
    return {"status": f.status, "reason": f.reason, "task_id": str(f.task_id) if f.task_id else None, "run_id": str(f.run_id) if f.run_id else None}


@router.post("/asset-trigger-fires/{fire_id}/skip")
async def skip_fire(fire_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    f, t, _ = await _fire_or_404(db, user, fire_id)
    f.status, f.decided_by = "skipped", user.id
    await notifications.clear(db, f"trigger-fire:{f.id}")
    await db.commit()
    bus.publish("asset_triggers.changed", t.project_id, {"project_id": str(t.project_id)})
    return {"status": f.status}
