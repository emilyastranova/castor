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
