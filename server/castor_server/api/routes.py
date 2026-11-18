"""Network routes: chains of hops a job's traffic goes through (network_routes.py, routing.py)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import network_routes as nr
from .. import routing
from ..access import visible_project_ids
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..events import bus
from ..orchestrator.dispatcher import project_allowed
from ..orchestrator.gateway import gateway
from ..routing import HopIn, RouteError
from ..services import actor_of, record_activity

router = APIRouter(prefix="/routes", tags=["routes"])


class RoutePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None
    project_ids: list[str] | None = None
    # The whole chain, in order. A hop sent with its `id` keeps its stored secret unless a new one
    # is given; one without an id is new.
    hops: list[HopIn] | None = Field(None, min_length=1, max_length=routing.MAX_HOPS)
    dns: str | None = Field(None, pattern="^(remote|local)$")
    strict: bool | None = None
    segment: str | None = Field(None, max_length=80)
    # Only from these agents; null: any agent (sent as null, it goes back to any).
    agents: routing.RouteAgents | None = None


class TestIn(BaseModel):
    # The agent to test from; none: any connected agent that runs one of the route's projects.
    agent_id: uuid.UUID | None = None
    # Also reach this through it (host:port), to see the route gets where it should.
    target: str | None = Field(None, max_length=300)


class FromSshHostIn(BaseModel):
    ssh_host_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    project_ids: list[str] = Field(default_factory=list)


class PlanIn(BaseModel):
    """How a card's runs would go through a route: give the card (task_id), or a library command."""

    route_id: str | None = None  # none: the card's own, or the project's default
    task_id: uuid.UUID | None = None
    command_ref: str | None = None
    agent_id: uuid.UUID | None = None


# ---------- helpers ----------

async def _visible(db, user: m.User) -> set[uuid.UUID] | None:
    return await visible_project_ids(db, user)


async def _route_or_404(db, user: m.User, route_id: str) -> m.Route:
    row = await db.get(m.Route, parse_uuid(route_id))
    if row is None or not nr.can_see(row, await _visible(db, user)):
        raise HTTPException(404, "Route not found")
    return row


async def _projects(db, user: m.User, ids: list[str]) -> list[str]:
    """Project ids the person can see (a route only for projects they're in, or for every one)."""
    visible = await _visible(db, user)
    out = []
    for raw in ids:
        pid = parse_uuid(str(raw))
        if await db.get(m.Project, pid) is None or (visible is not None and pid not in visible):
            raise HTTPException(422, f"project_ids: no project {raw}")
        out.append(str(pid))
    if not out and visible is not None and not user.is_admin:
        raise HTTPException(422, "project_ids: name the projects it's for (only admins make routes for every project)")
    return sorted(set(out))


async def _name_free(db, name: str, exclude: uuid.UUID | None = None) -> str:
    name = name.strip()
    found = (await db.execute(select(m.Route).where(m.Route.name == name))).scalar_one_or_none()
    if found is not None and found.id != exclude:
        raise HTTPException(409, f"There's already a route called “{name}”")
    return name


def _tags(tags: list[str]) -> list[str]:
    return sorted({t.strip() for t in tags if t and t.strip()})[:20]


def _changes(before: m.Route | dict[str, Any], after: m.Route) -> list[str]:
    get = (lambda k: before.get(k)) if isinstance(before, dict) else (lambda k: getattr(before, k))
    words = {"name": "name", "description": "description", "tags": "tags", "project_ids": "projects", "hops": "hops",
             "dns": "where names are resolved", "strict": "“only through the route”", "segment": "segment",
             "agents": "agents"}
    return [w for k, w in words.items() if get(k) != getattr(after, k)]


# ---------- routes ----------

@router.get("")
async def list_routes(db: DB, user: CurrentUser, project_id: uuid.UUID | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    """Routes you can use: for one of your projects, or for every project. `project_id` narrows it
    to the ones that project can use."""
    visible = await _visible(db, user)
    rows = (await db.execute(select(m.Route).order_by(m.Route.name))).scalars().all()
    out = []
    for row in rows:
        if not nr.can_see(row, visible):
            continue
        if project_id is not None and not nr.for_project(row, project_id):
            continue
        if tag and tag not in (row.tags or []):
            continue
        out.append(nr.route_out(row))
    return out


@router.post("", status_code=201)
async def create_route(body: routing.RouteIn, db: DB, user: Operator) -> dict[str, Any]:
    name = await _name_free(db, body.name)
    try:
        hops = nr.store_hops(body.hops, [], f"castor route {name}")
        agents = await nr.check_agents(db, body.agents)
    except RouteError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = m.Route(name=name, description=body.description, tags=_tags(body.tags),
                  project_ids=await _projects(db, user, body.project_ids), hops=hops, dns=body.dns, strict=body.strict,
                  segment=body.segment.strip(), agents=agents, created_by=user.id)
    db.add(row)
    await db.flush()
    await record_activity(db, type="route.created", message=f"Made the route “{name}” ({routing.chain_label(hops)})",
                          project_id=None, payload={"route_id": str(row.id)}, **actor_of(user))
    await db.commit()
    await db.refresh(row)
    nr.publish(row)
    return nr.route_out(row)


@router.get("/{route_id}")
async def get_route(route_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    return nr.route_out(await _route_or_404(db, user, route_id))


@router.patch("/{route_id}")
async def update_route(route_id: str, body: RoutePatch, db: DB, user: Operator) -> dict[str, Any]:
    row = await _route_or_404(db, user, route_id)
    before = {k: getattr(row, k) for k in ("name", "description", "tags", "project_ids", "hops", "dns", "strict", "segment", "agents")}
    if body.name is not None:
        row.name = await _name_free(db, body.name, row.id)
    if body.description is not None:
        row.description = body.description
    if body.tags is not None:
        row.tags = _tags(body.tags)
    if body.project_ids is not None:
        row.project_ids = await _projects(db, user, body.project_ids)
    if body.hops is not None:
        try:
            row.hops = nr.store_hops(body.hops, row.hops or [], f"castor route {row.name}")
        except RouteError as exc:
            raise HTTPException(422, str(exc)) from exc
    if body.dns is not None:
        row.dns = body.dns
    if body.strict is not None:
        row.strict = body.strict
    if body.segment is not None:
        row.segment = body.segment.strip()
    if "agents" in body.model_fields_set:
        try:
            row.agents = await nr.check_agents(db, body.agents, row.agents)
        except RouteError as exc:
            raise HTTPException(422, str(exc)) from exc
    changed = _changes(before, row)
    if changed:
        if "hops" in changed:
            row.last_test = None  # a different chain: the old test says nothing about it
        await record_activity(db, type="route.updated", message=f"Changed the {', '.join(changed)} of the route “{row.name}”",
                              project_id=None, payload={"route_id": str(row.id)}, **actor_of(user))
    await db.commit()
    await db.refresh(row)
    nr.publish(row)
    return nr.route_out(row)


@router.delete("/{route_id}", status_code=204)
async def delete_route(route_id: str, db: DB, user: Operator, force: bool = False) -> None:
    """Delete a route. Cards, runs, projects and agents that use it don't go direct afterwards: their
    jobs refuse to run until they're given another (or none). In use: 409 unless `force`."""
    row = await _route_or_404(db, user, route_id)
    usage = await _usage(db, user, row)
    if not force and (usage["cards"] or usage["projects"] or usage["agents"]):
        raise HTTPException(409, f"“{row.name}” is in use ({usage['summary']}); delete it anyway with force")
    await record_activity(db, type="route.deleted", message=f"Deleted the route “{row.name}”" + (f" ({usage['summary']})" if usage["summary"] else ""),
                          project_id=None, payload={"route_id": str(row.id)}, **actor_of(user))
    data = {"id": str(row.id)}
    pids = list(row.project_ids or [])
    await db.delete(row)
    await db.commit()
    for (rid, agent) in list(nr.STATES):
        if rid == str(row.id):
            nr.STATES.pop((rid, agent), None)
    for pid in pids or [None]:
        bus.publish("route.deleted", pid, data)


async def _usage(db, user: m.User, row: m.Route) -> dict[str, Any]:
    rid = str(row.id)
    visible = await _visible(db, user)
    cards = (await db.execute(select(m.Task).where(m.Task.route == rid, m.Task.archived_at.is_(None)))).scalars().all()
    shown = [{"id": str(t.id), "title": t.title, "project_id": str(t.project_id)} for t in cards
             if visible is None or t.project_id in visible]
    projects = [p for p in (await db.execute(select(m.Project))).scalars() if (p.settings or {}).get("route") == rid]
    agents = (await db.execute(select(m.Agent).where(m.Agent.route == rid))).scalars().all()
    runs = (await db.execute(select(m.PlaybookRun).where(m.PlaybookRun.route == rid,
                                                         m.PlaybookRun.status == m.PlaybookRunStatus.running))).scalars().all()
    parts = []
    if cards:
        parts.append(f"{len(cards)} card{'s' if len(cards) != 1 else ''}")
    if projects:
        parts.append(f"the default of {', '.join(p.codename for p in projects)}")
    if agents:
        parts.append(f"agent{'s' if len(agents) != 1 else ''} {', '.join(a.name for a in agents)}")
    if runs:
        parts.append(f"{len(runs)} running playbook run{'s' if len(runs) != 1 else ''}")
    return {"cards": shown[:50], "card_count": len(cards), "projects": [{"id": str(p.id), "codename": p.codename} for p in projects
                                                                       if visible is None or p.id in visible],
            "agents": [{"id": str(a.id), "name": a.name} for a in agents], "runs": len(runs), "summary": ", ".join(parts)}


@router.get("/{route_id}/usage")
async def route_usage(route_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    """The cards, projects (their default), agents and running playbook runs that use it."""
    return await _usage(db, user, await _route_or_404(db, user, route_id))


@router.post("/{route_id}/test")
async def test_route(route_id: str, body: TestIn, db: DB, user: Operator) -> dict[str, Any]:
    """Bring the route up on an agent and try it hop by hop: each hop's latency, or where it breaks
    and why; then the target, if given. Host keys seen for the first time are trusted; a changed one
    is held for someone to accept."""
    row = await _route_or_404(db, user, route_id)
    conn = None
    if body.agent_id:
        conn = gateway.connections.get(body.agent_id)
        if conn is None:
            raise HTTPException(409, "That agent isn't connected")
        if not nr.allows(row, conn.agent_id, conn.tags):
            raise HTTPException(409, f"“{row.name}” only works from {nr.agents_words(row)}, and {conn.name} isn't one; "
                                     "add it to the route's agents first")
    else:
        able = [c for c in gateway.connections.values() if c.capabilities.get("routes") and nr.allows(row, c.agent_id, c.tags)
                and (not row.project_ids or any(project_allowed(c, p) for p in row.project_ids))]
        if not able:
            if row.agents is not None:
                raise HTTPException(409, f"None of the agents “{row.name}” works from ({nr.agents_words(row)}) is connected "
                                         "and can bring routes up")
            raise HTTPException(409, "No connected agent can bring routes up (agents from before Castor 1.13 can't)")
        conn = min(able, key=lambda c: len(c.running))
    try:
        return await nr.test(db, row, conn, body.target, user)
    except RouteError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{route_id}/hops/{hop_id}/accept-host-key")
async def accept_host_key(route_id: str, hop_id: str, db: DB, user: Operator) -> dict[str, Any]:
    """Trust the key an SSH hop shows now, after checking it's the right machine."""
    row = await _route_or_404(db, user, route_id)
    hops = [dict(h) for h in row.hops or []]
    for i, h in enumerate(hops):
        if h.get("id") == hop_id:
            if not h.get("host_key_pending"):
                raise HTTPException(409, "That hop has no new host key waiting")
            h["host_key"], h["host_key_pending"] = h["host_key_pending"], None
            row.hops = hops
            await record_activity(db, type="route.host_key_accepted",
                                  message=f"Accepted hop {i + 1}'s new host key on route “{row.name}” ({routing.fingerprint(h['host_key'])})",
                                  project_id=None, payload={"route_id": str(row.id), "hop_id": hop_id}, **actor_of(user))
            await db.commit()
            await db.refresh(row)
            nr.publish(row)
            return nr.route_out(row)
    raise HTTPException(404, "No such hop")


@router.post("/from-ssh-host", status_code=201)
async def route_from_ssh_host(body: FromSshHostIn, db: DB, user: Admin) -> dict[str, Any]:
    """A route that reaches what an SSH host can: its jump hosts (from the shared SSH config) and the
    host itself as SSH hops, signing in with the host's key, with the host keys Castor trusts."""
    from .. import ssh_config

    host = await db.get(m.SshHost, body.ssh_host_id)
    if host is None:
        raise HTTPException(404, "SSH host not found")
    name = await _name_free(db, body.name)
    try:
        hops = await nr.hops_from_ssh_host(db, host)
    except ssh_config.ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = m.Route(name=name, description=f"From the SSH host {host.host}", tags=[], hops=hops, dns="remote", strict=True,
                  project_ids=await _projects(db, user, body.project_ids), created_by=user.id)
    db.add(row)
    await db.flush()
    await record_activity(db, type="route.created", message=f"Made the route “{name}” from the SSH host {host.host} ({routing.chain_label(hops)})",
                          project_id=None, payload={"route_id": str(row.id)}, **actor_of(user))
    await db.commit()
    await db.refresh(row)
    nr.publish(row)
    return nr.route_out(row)


@router.post("/plan")
async def plan_route(body: PlanIn, db: DB, user: CurrentUser) -> dict[str, Any]:
    """How a card's runs would go through a route, before running it: for a run in a container and
    for one on an agent's host, the ways Castor would try (best first) or why it can't."""
    from ..access import task_or_404
    from ..command_library import load_spec
    from ..tool_library import tools_by_id

    task = await task_or_404(db, user, body.task_id) if body.task_id else None
    command_ref = body.command_ref or (task.command_ref if task else None)
    spec = await load_spec(db, command_ref) if command_ref else None
    tool_id = (task.tool if task else None) or (spec.tool if spec else None)
    tool = (await tools_by_id(db)).get(tool_id) if tool_id else None
    route_id = body.route_id or (await nr.chosen_for(db, task) if task else None)
    if route_id == routing.NONE or not route_id:
        return {"route": None, "container": None, "host": None}
    row = await _route_or_404(db, user, route_id)
    command = task.command if task else ""
    if spec is not None and not command:
        from ..command_render import render_command

        try:
            command = render_command(spec, {a.name: a.example if a.example is not None else a.default for a in spec.args})
        except ValueError:
            command = spec.command
    caps = routing.merge_caps(tool.route if tool else None, spec.route if spec else None, shell=tool is None and spec is None)
    agent_caps: dict[str, Any] = {}
    if body.agent_id and (conn := gateway.connections.get(body.agent_id)):
        agent_caps = conn.capabilities
    out: dict[str, Any] = {"route": {"id": str(row.id), "name": row.name, "label": routing.chain_label(row.hops or []),
                                     "strict": row.strict, "dns": row.dns}}
    for where, in_container in (("container", True), ("host", False)):
        try:
            out[where] = {"ways": [w["method"] for w in routing.plan(caps, command, in_container=in_container,
                                                                     strict=row.strict, agent=agent_caps)]}
        except RouteError as exc:
            out[where] = {"error": str(exc)}
    out["runs_in"] = "container" if (task and task.image) or (tool and (tool.image or tool.build)) or (spec and spec.image) else "either"
    return out
