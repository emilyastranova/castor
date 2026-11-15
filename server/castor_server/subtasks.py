"""Cards with subtasks: an activity card ("Update homelab LXCs") holding cards of its own, so the
board shows the activity once instead of every small task.

A subtask is an ordinary card with `parent_id`: its own column, assignees, due date, comments and
runs. The board and its counts leave subtasks out (like a playbook run's step cards); the
activity shows their progress (`TaskOut.subtasks`) and lists them in its Subtasks tab, ordered by
`position`. One level deep: an activity can't be a subtask, and playbook run and step cards take
no part. Unless the project turns it off (`settings.subtasks_move_card`), the activity follows its
subtasks: into the active column when one starts, and to Review (or Done) when all are finished.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .schemas import SubtaskSummary


class SubtaskError(ValueError):
    """A card that can't be (or have) a subtask here; the message says why."""


async def children(db: AsyncSession, parent_id: uuid.UUID, *, archived: bool = True) -> list[m.Task]:
    query = select(m.Task).where(m.Task.parent_id == parent_id)
    if not archived:
        query = query.where(m.Task.archived_at.is_(None))
    return list((await db.execute(query.order_by(m.Task.position, m.Task.created_at))).scalars())


async def activities_of(db: AsyncSession, tasks: list[m.Task]) -> dict[uuid.UUID, dict[str, str]]:
    """The activity of each subtask among `tasks`: {subtask id: {id, title}} (for search, Home and
    people pages, which show a subtask with the card it's in)."""
    parent_ids = {t.parent_id for t in tasks if t.parent_id is not None}
    if not parent_ids:
        return {}
    titles = dict((await db.execute(select(m.Task.id, m.Task.title).where(m.Task.id.in_(parent_ids)))).all())
    return {t.id: {"id": str(t.parent_id), "title": titles[t.parent_id]} for t in tasks if t.parent_id in titles}


def in_activity(task: m.Task, activities: dict[uuid.UUID, dict[str, str]]) -> str:
    """" in “Activity”" for a subtask, else ""."""
    found = activities.get(task.id)
    return f" in “{found['title']}”" if found else ""


async def _is_run_card(db: AsyncSession, task: m.Task) -> bool:
    return (await db.execute(select(m.PlaybookRun.id).where(m.PlaybookRun.card_id == task.id))).first() is not None


async def check_parent(db: AsyncSession, parent: m.Task) -> None:
    if parent.parent_id is not None:
        raise SubtaskError(f"“{parent.title}” is a subtask itself; subtasks go one level deep")
    if parent.playbook_step_id is not None or parent.playbook_run_id is not None:
        raise SubtaskError("a playbook step's card can't have subtasks")
    if await _is_run_card(db, parent):
        raise SubtaskError("a playbook run's card shows its steps; it can't have subtasks too")
    if parent.archived_at is not None:
        raise SubtaskError(f"“{parent.title}” is archived; restore it first")


async def check_child(db: AsyncSession, parent: m.Task, child: m.Task) -> None:
    if child.id == parent.id:
        raise SubtaskError("a card can't be its own subtask")
    if child.project_id != parent.project_id:
        raise SubtaskError(f"“{child.title}” is in another project")
    if child.parent_id is not None and child.parent_id != parent.id:
        raise SubtaskError(f"“{child.title}” is already a subtask of another card")
    if child.playbook_step_id is not None or child.playbook_run_id is not None or await _is_run_card(db, child):
        raise SubtaskError(f"“{child.title}” belongs to a playbook run")
    if (await db.execute(select(m.Task.id).where(m.Task.parent_id == child.id).limit(1))).first():
        raise SubtaskError(f"“{child.title}” has subtasks of its own; subtasks go one level deep")
    if child.archived_at is not None:
        raise SubtaskError(f"“{child.title}” is archived; restore it first")


async def _finished_roles(db: AsyncSession, column_ids: set[uuid.UUID]) -> dict[uuid.UUID, m.ColumnRole | None]:
    if not column_ids:
        return {}
    rows = await db.execute(select(m.BoardColumn.id, m.BoardColumn.role).where(m.BoardColumn.id.in_(column_ids)))
    return dict(rows.all())  # type: ignore[arg-type]


# Finished, as far as its activity is concerned: a command subtask lands in Review when its run
# succeeds, and waiting there for a look shouldn't hold the whole activity open.
DONE_ROLES = (m.ColumnRole.review, m.ColumnRole.done, m.ColumnRole.reported)


def _finished(task: m.Task, roles: dict[uuid.UUID, Any]) -> bool:
    return task.archived_at is not None or roles.get(task.column_id) in DONE_ROLES


async def summaries(db: AsyncSession, parent_ids: list[uuid.UUID]) -> dict[uuid.UUID, SubtaskSummary]:
    """Each activity's progress, for its card on the board."""
    from .services import latest_jobs_for

    if not parent_ids:
        return {}
    rows = list((await db.execute(select(m.Task).where(m.Task.parent_id.in_(parent_ids)))).scalars())
    if not rows:
        return {}
    runs = dict((await db.execute(select(m.Task.id, m.Task.subtask_run).where(m.Task.id.in_(parent_ids)))).all())
    roles = await _finished_roles(db, {t.column_id for t in rows})
    latest = await latest_jobs_for(db, [t.id for t in rows])
    out: dict[uuid.UUID, SubtaskSummary] = {}
    for t in rows:
        s = out.setdefault(t.parent_id, SubtaskSummary())
        s.total += 1
        job = latest.get(t.id)
        status = job.status if job else None
        if _finished(t, roles):
            s.done += 1
        elif status in (m.JobStatus.failed, m.JobStatus.lost):
            s.failed += 1
        elif status in m.JobStatus.active() or roles.get(t.column_id) == m.ColumnRole.active:
            s.running += 1
        if status == m.JobStatus.approval:
            s.approvals += 1
        if t.due_at and not _finished(t, roles) and (s.next_due is None or t.due_at < s.next_due):
            s.next_due = t.due_at
    for pid, s in out.items():
        if run := runs.get(pid):
            s.queued, s.at_once = len(run.get("queue") or []), run.get("at_once")
    return out


async def _next_position(db: AsyncSession, parent_id: uuid.UUID) -> float:
    top = (await db.execute(select(func.max(m.Task.position)).where(m.Task.parent_id == parent_id))).scalar()
    return (top or 0) + 10.0


async def adopt(db: AsyncSession, parent: m.Task, cards: list[m.Task], user: m.User | None) -> list[m.Task]:
    """Make existing cards subtasks of `parent` (after the ones it has). Checks everything first,
    so it's all or nothing. The caller commits and publishes."""
    from .services import actor_of, record_activity

    await check_parent(db, parent)
    for card in cards:
        await check_child(db, parent, card)
    added = []
    position = await _next_position(db, parent.id)
    for card in cards:
        if card.parent_id == parent.id:
            continue
        card.parent_id = parent.id
        card.position = position
        position += 10.0
        added.append(card)
    if added:
        names = ", ".join(f"“{c.title}”" for c in added[:5]) + (f" and {len(added) - 5} more" if len(added) > 5 else "")
        await record_activity(db, type="task.subtasks_added", message=f"Added {names} to “{parent.title}”",
                              project_id=parent.project_id, task_id=parent.id,
                              payload={"subtasks": [str(c.id) for c in added]}, **actor_of(user))
        await db.flush()
    return added


async def release(db: AsyncSession, card: m.Task, user: m.User | None) -> m.Task | None:
    """Make a subtask its own card on the board again (in its column, at the top). Returns its
    activity. The caller commits and publishes both."""
    from .services import actor_of, record_activity

    if card.parent_id is None:
        raise SubtaskError(f"“{card.title}” isn't a subtask")
    parent = await db.get(m.Task, card.parent_id)
    card.parent_id = None
    top = (await db.execute(select(func.min(m.Task.position)).where(m.Task.column_id == card.column_id,
                                                                    m.Task.parent_id.is_(None)))).scalar()
    card.position = (top if top is not None else 10.0) - 10.0
    await record_activity(db, type="task.subtask_released",
                          message=f"Took “{card.title}” out of “{parent.title if parent else 'its card'}”",
                          project_id=card.project_id, task_id=card.id, payload={"parent": str(parent.id) if parent else None},
                          **actor_of(user))
    await db.flush()
    return parent


async def reorder(db: AsyncSession, parent: m.Task, ids: list[uuid.UUID]) -> None:
    mine = {c.id: c for c in await children(db, parent.id)}
    order = [i for i in ids if i in mine] + [i for i in mine if i not in ids]
    for index, task_id in enumerate(order):
        mine[task_id].position = (index + 1) * 10.0
    await db.flush()


async def consolidate(db: AsyncSession, project: m.Project, title: str, cards: list[m.Task], user: m.User | None,
                      *, description: str = "", column: m.BoardColumn | None = None) -> m.Task:
    """A new activity card holding existing cards (for tidying a board): in `column`, or in the
    column most of them are in. The caller commits and publishes."""
    from .services import actor_of, column_with_role, next_position, record_activity

    if not cards:
        raise SubtaskError("choose the cards to put in it")
    if column is None:
        counts: dict[uuid.UUID, int] = {}
        for c in cards:
            counts[c.column_id] = counts.get(c.column_id, 0) + 1
        column = await db.get(m.BoardColumn, max(counts, key=lambda k: counts[k]))
        roles = await _finished_roles(db, set(counts))
        if not all(_finished(c, roles) for c in cards) and column is not None and column.role in (m.ColumnRole.done, m.ColumnRole.reported):
            column = await column_with_role(db, project.id, m.ColumnRole.active) or column
    parent = m.Task(project_id=project.id, column_id=column.id, position=await next_position(db, column.id),
                    title=title.strip()[:300], description=description, kind=m.TaskKind.manual,
                    created_by=user.id if user else None)
    db.add(parent)
    await db.flush()
    await record_activity(db, type="task.created", message=f"Created “{parent.title}” from {len(cards)} cards",
                          project_id=project.id, task_id=parent.id, **actor_of(user))
    await adopt(db, parent, cards, user)
    return parent


async def follow(db: AsyncSession, parent_id: uuid.UUID | None, user: m.User | None) -> m.Task | None:
    """The activity follows its subtasks (unless its project turned that off): into the active
    column when one has started, and to Review (else Done) when every one is finished. Returns the
    activity when it moved; the caller commits and publishes it."""
    from .services import column_with_role, move_task, next_position

    if parent_id is None:
        return None
    parent = await db.get(m.Task, parent_id)
    if parent is None or parent.archived_at is not None:
        return None
    project = await db.get(m.Project, parent.project_id)
    if project is None or (project.settings or {}).get("subtasks_move_card") is False:
        return None
    kids = await children(db, parent.id)
    if not kids:
        return None
    roles = await _finished_roles(db, {k.column_id for k in kids} | {parent.column_id})
    here = roles.get(parent.column_id)
    target: m.BoardColumn | None = None
    if all(_finished(k, roles) for k in kids):
        if here not in DONE_ROLES:
            target = await column_with_role(db, parent.project_id, m.ColumnRole.review) or \
                await column_with_role(db, parent.project_id, m.ColumnRole.done)
    elif here in (None, m.ColumnRole.backlog, m.ColumnRole.todo):
        started = [k for k in kids if roles.get(k.column_id) == m.ColumnRole.active or _finished(k, roles)]
        if started:
            target = await column_with_role(db, parent.project_id, m.ColumnRole.active)
    if target is None or target.id == parent.column_id:
        return None
    await move_task(db, parent, target, await next_position(db, target.id), user, force=True)
    return parent


# ---------- Run all, a few at a time ----------


async def _runnable(db: AsyncSession, kids: list[m.Task]) -> tuple[list[m.Task], list[dict[str, str]]]:
    """The command subtasks Run all would start now (not finished, not running), and the ones it
    leaves out with why."""
    from .services import active_job_for

    roles = await _finished_roles(db, {k.column_id for k in kids})
    ready, skipped = [], []
    for kid in kids:
        if kid.kind != m.TaskKind.command or not kid.command.strip() or roles.get(kid.column_id) in DONE_ROLES:
            continue
        if await active_job_for(db, kid.id) is not None:
            skipped.append({"id": str(kid.id), "title": kid.title, "reason": "already running"})
            continue
        ready.append(kid)
    return ready, skipped


async def _start(db: AsyncSession, kid: m.Task, user: m.User | None, force: bool) -> str | None:
    """Start one subtask's run; returns why not (it waits on unfinished cards), or None."""
    from .services import start_run, unfinished_blockers

    blockers = (await unfinished_blockers(db, [kid])).get(kid.id)
    if blockers and not force:
        return "waits on " + ", ".join(b["title"] for b in blockers)
    await start_run(db, kid, user)
    return None


async def run_all(db: AsyncSession, parent: m.Task, user: m.User | None, *, force: bool = False,
                  at_once: int | None = None) -> tuple[list[m.Task], list[dict[str, str]], int]:
    """Run every command subtask that isn't finished or running: all now, or `at_once` at a
    time (the rest wait in `parent.subtask_run`, started as others end; 1 is one after another).
    Returns (started, skipped, queued). The caller commits, publishes and wakes the dispatcher."""
    kids = await children(db, parent.id, archived=False)
    ready, skipped = await _runnable(db, kids)
    started: list[m.Task] = []
    if at_once:
        parent.subtask_run = {"queue": [str(k.id) for k in ready], "at_once": at_once, "force": force,
                              "by": str(user.id) if user else None}
        started, more = await _pump(db, parent, user)
        skipped += more
        return started, skipped, len((parent.subtask_run or {}).get("queue") or [])
    for kid in ready:
        why = await _start(db, kid, user, force)
        if why:
            skipped.append({"id": str(kid.id), "title": kid.title, "reason": why})
        else:
            started.append(kid)
    return started, skipped, 0


async def _pump(db: AsyncSession, parent: m.Task, user: m.User | None) -> tuple[list[m.Task], list[dict[str, str]]]:
    from .services import active_job_for

    run = dict(parent.subtask_run or {})
    queue = list(run.get("queue") or [])
    at_once = int(run.get("at_once") or 1)
    kids = {str(k.id): k for k in await children(db, parent.id, archived=False)}
    busy = 0
    for kid in kids.values():
        if await active_job_for(db, kid.id) is not None:
            busy += 1
    started, skipped = [], []
    while queue and busy < at_once:
        kid = kids.get(queue.pop(0))
        if kid is None or kid.parent_id != parent.id:
            continue  # taken out or archived since
        if await active_job_for(db, kid.id) is not None:
            continue
        why = await _start(db, kid, user, bool(run.get("force")))
        if why:
            skipped.append({"id": str(kid.id), "title": kid.title, "reason": why})
            continue
        started.append(kid)
        busy += 1
    parent.subtask_run = {**run, "queue": queue} if queue else None
    return started, skipped


async def pump(db: AsyncSession, parent_id: uuid.UUID | None) -> list[m.Task]:
    """A subtask's run ended: start the next ones waiting in its activity's Run all, up to its
    limit. Returns the subtasks started (the caller commits and publishes them and the activity)."""
    if parent_id is None:
        return []
    parent = await db.get(m.Task, parent_id)
    if parent is None or not parent.subtask_run or parent.archived_at is not None:
        return []
    by = (parent.subtask_run or {}).get("by")
    user = await db.get(m.User, uuid.UUID(by)) if by else None
    started, _ = await _pump(db, parent, user)
    return started


def stop(parent: m.Task) -> int:
    """Stop starting more (what's running carries on); returns how many were waiting."""
    waiting = len((parent.subtask_run or {}).get("queue") or [])
    parent.subtask_run = None
    return waiting


# ---------- one subtask per target ----------


async def targets_of(db: AsyncSession, project_id: uuid.UUID, value: Any) -> list[str]:
    """The targets for "one subtask each": a list, text (commas or lines), a saved list
    ({"list": …}) or the project's assets ({"assets": …})."""
    from .assets import is_asset_ref, ref_values
    from .variables import is_list_ref, list_entries

    if is_asset_ref(value):
        entries = await ref_values(db, project_id, value)
    elif is_list_ref(value):
        _row, entries, problem = await list_entries(db, value)
        if problem:
            raise SubtaskError(problem)
    elif isinstance(value, list):
        entries = [str(v) for v in value]
    elif isinstance(value, str):
        import re

        entries = re.split(r"[,\n]", value)
    else:
        raise SubtaskError("targets: a list, text, a saved list or an asset query")
    out = list(dict.fromkeys(e.strip() for e in entries if str(e).strip()))
    if not out:
        raise SubtaskError("targets: nothing to run on")
    if len(out) > MAX_MANY:
        raise SubtaskError(f"targets: {len(out)} is more than {MAX_MANY} subtasks at once")
    return out


MAX_MANY = 500


# ---------- to and from playbooks ----------


def _slug(text: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "step"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    taken.add(out)
    return out


async def as_playbook(db: AsyncSession, parent: m.Task) -> tuple[str, list[str]]:
    """An activity's subtasks as a playbook draft (YAML), to save and run again next month: each
    library-command subtask a step with its arguments and variable sets, in order, and "waits on"
    between them as depends_on. Returns the YAML and the subtasks left out (manual ones, and
    commands typed by hand, which a playbook step can't be)."""
    from .playbook_yaml import to_yaml

    kids = await children(db, parent.id, archived=False)
    taken: set[str] = set()
    step_of: dict[str, str] = {}
    left_out = []
    for kid in kids:
        if kid.kind == m.TaskKind.command and kid.command_ref:
            step_of[str(kid.id)] = _slug(kid.title, taken)
        else:
            left_out.append(kid.title)
    sets = {}
    wanted = {str(s) for kid in kids for s in (kid.var_bindings or {}).values()}
    if wanted:
        rows = (await db.execute(select(m.VariableSet).where(m.VariableSet.id.in_([uuid.UUID(s) for s in wanted])))).scalars()
        sets = {str(r.id): r.name for r in rows if not r.ephemeral}
    steps = []
    for kid in kids:
        sid = step_of.get(str(kid.id))
        if sid is None:
            continue
        step: dict[str, Any] = {"id": sid, "name": kid.title[:120], "command_ref": kid.command_ref}
        if kid.description.strip():
            step["description"] = kid.description.strip().splitlines()[0][:300]
        args = {k: v for k, v in (kid.command_args or {}).items() if v not in (None, "", [])}
        if args:
            step["args"] = args
        vars_ = {slot: sets[str(sid_)] for slot, sid_ in (kid.var_bindings or {}).items() if str(sid_) in sets}
        if vars_:
            step["vars"] = vars_
        deps = [step_of[w] for w in (kid.waits_on or []) if w in step_of]
        if deps:
            step["depends_on"] = deps
        steps.append(step)
    if not steps:
        raise SubtaskError("none of its subtasks runs a library command, so there's nothing a playbook could repeat")
    spec = {"id": _slug(parent.title, set()), "name": parent.title[:120],
            "description": (parent.description.strip().splitlines() or [""])[0][:500], "steps": steps}
    return to_yaml(spec), left_out


async def from_playbook(db: AsyncSession, spec: Any, project: m.Project, inputs: dict[str, Any],
                        user: m.User | None) -> tuple[m.Task, list[str]]:
    """A new activity whose subtasks are the playbook's command steps (with its inputs filled in),
    "waits on" following depends_on, for working through it by hand and at your own pace. Steps
    that need a run (transforms, for_each, arguments from another step's output) are left out and
    named in the activity's description. Returns the activity and those notes. The caller commits."""
    from .command_library import apply_library, load_spec
    from .command_render import ArgumentError
    from .playbook_spec import PLACEHOLDER_RE, resolve_step_args
    from .services import actor_of, column_with_role, next_position, record_activity

    notes: list[str] = []
    column = await column_with_role(db, project.id, m.ColumnRole.todo)
    if column is None:
        raise SubtaskError("the project has no To Do column")
    parent = m.Task(project_id=project.id, column_id=column.id, position=await next_position(db, column.id),
                    title=spec.name[:300], description=spec.description or "", kind=m.TaskKind.manual,
                    created_by=user.id if user else None)
    db.add(parent)
    await db.flush()
    made: dict[str, m.Task] = {}
    for step in spec.steps:
        if step.kind != "command":
            notes.append(f"{step.name}: a transform runs only in a playbook run")
            continue
        if step.for_each is not None:
            notes.append(f"{step.name}: runs once per item of another step's output (for_each)")
            continue
        if step.upstream_steps():
            notes.append(f"{step.name}: takes {', '.join(step.upstream_steps())}'s output, so it needs a playbook run")
            continue
        cmd = await load_spec(db, step.command_ref)
        if cmd is None:
            notes.append(f"{step.name}: {step.command_ref} isn't in the library")
            continue
        card = m.Task(project_id=project.id, column_id=column.id, position=0, title=step.name[:300],
                      description=step.description or "", kind=m.TaskKind.command, created_by=user.id if user else None)
        try:
            await apply_library(db, card, cmd, resolve_step_args(step.args, inputs, {}))
        except ArgumentError as exc:
            notes.append(f"{step.name}: {exc}")
            continue
        db.add(card)
        await db.flush()
        bindings = {}
        for slot, value in step.vars.items():
            ref = PLACEHOLDER_RE.match(value.strip())
            target = inputs.get(ref.group(1).split(".")[1]) if ref else value
            from .variables import find_set

            row = await find_set(db, target) if target else None
            if row is not None:
                bindings[slot] = str(row.id)
        card.var_bindings = bindings
        made[step.id] = card
    for step in spec.steps:
        card = made.get(step.id)
        if card is not None:
            card.waits_on = [str(made[d].id) for d in step.depends_on if d in made] or None
    if not made:
        raise SubtaskError("none of its steps can be a subtask (" + "; ".join(notes) + ")")
    if notes:
        parent.description = (parent.description + "\n\n" if parent.description else "") + \
            "Left out of this activity (they need a playbook run):\n" + "\n".join(f"- {n}" for n in notes)
    await record_activity(db, type="task.created", message=f"Started “{parent.title}” from the playbook {spec.id}",
                          project_id=project.id, task_id=parent.id, payload={"playbook": spec.id}, **actor_of(user))
    await adopt(db, parent, list(made.values()), user)
    return parent, notes
