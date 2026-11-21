"""Manual runs: output someone brings in to be parsed (pasted, from a comment or a timeline post, or
a run's output, such as what they did in a shell card), as a run on a card. It's a job like any
other once it's made: `runner = "manual"`, finished, its text as stdout and one output, "results",
read by the parser they chose. So everything that hangs off a run works on it: its results table,
the assets and relationships its results become (assets.ingest_outputs), comparing runs, an AI
summary. With no parser, the output is marked unread and the AI drafts one for it (a
ParserProposal, as for any output its parser couldn't read); using the draft reads it again.

A manual run never says what it didn't see (asset_history.record_absent): it's a part of
something, not a whole scan. `rank` tries every parser on a text, for choosing one."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import parsers
from .models import utcnow

MAX_TEXT = 5 * 1024 * 1024
OUTPUT = "results"
# Parsers that read nearly any text, or need options to read anything: offered after the ones that
# recognise the format.
GENERIC = {"lines", "text", "regex", "json-value"}
UNREAD = "No parser has read this yet: choose one, or have the AI draft one for it"
WHERE = {"paste": "Pasted output", "comment": "Output from a comment", "post": "Output from a timeline post",
         "job": "Output of a run"}

_FENCE = re.compile(r"^(```|~~~)[^\n]*\n(.*?)^\1[ \t]*$", re.DOTALL | re.MULTILINE)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class ManualRunError(ValueError):
    pass


def text_of_writing(body: str) -> str:
    """What to parse in a comment or post: its code blocks when it has any (the output people paste
    goes there), else the whole text without pictures."""
    blocks = [b.group(2) for b in _FENCE.finditer(body or "")]
    if blocks:
        return "\n".join(b.rstrip("\n") for b in blocks) + "\n"
    return _IMAGE.sub("", body or "").strip() + "\n"


async def text_of_job(db: AsyncSession, job: m.Job, lines: tuple[int, int] | None = None) -> str:
    """A run's output as text (a terminal's escape sequences and overwrites resolved), or the lines
    [from, to) of it."""
    from .interactive import plain

    rows = await db.execute(select(m.JobLogChunk.data).where(m.JobLogChunk.job_id == job.id, m.JobLogChunk.stream != "system")
                            .order_by(m.JobLogChunk.seq))
    text = plain("".join(d for (d,) in rows))
    if lines is not None:
        start, end = lines
        text = "\n".join(text.split("\n")[max(start, 0):max(end, 0)])
    return text


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if value is None or value == "" or value == {}:
        return 0
    return 1


def _try(parser: parsers.Parser, data: bytes) -> dict[str, Any] | None:
    try:
        options = parser.configure({})
        value = parser.run(data, options)
    except parsers.OptionsError:
        return None  # it needs options to read anything (a pattern, a path): chosen by hand
    except Exception:  # noqa: BLE001 - not its format (or a parser bug): it simply didn't read it
        return None
    count = _count(value)
    if not count:
        return None
    return {"count": count, "preview": value[:5] if isinstance(value, list) else value}


async def rank(text: str) -> list[dict[str, Any]]:
    """Every parser that reads something from the text, the ones that recognise its format first (by
    how much they read), then the general ones."""
    data = text.encode()[:MAX_TEXT]
    found: list[dict[str, Any]] = []
    gate = asyncio.Semaphore(4)

    async def one(parser: parsers.Parser) -> None:
        async with gate:
            got = await asyncio.to_thread(_try, parser, data)
        if got is not None:
            found.append({"parser": parser.name, "type": parser.type, "description": parser.description,
                          "generic": parser.name in GENERIC, **got})

    await asyncio.gather(*(one(p) for p in parsers.all_parsers()))
    return sorted(found, key=lambda f: (f["generic"], -f["count"], f["parser"]))


async def create(db: AsyncSession, task: m.Task, user: m.User, text: str, *, parser: str | None,
                 options: dict[str, Any] | None = None, origin: dict[str, Any] | None = None, ai: bool = False) -> m.Job:
    """Make the manual run, read it with the parser (or mark it unread and, with `ai`, have a parser
    drafted), and record what it found. Commits."""
    from .outputs import parse_job
    from .services import actor_of, publish_job, publish_task, record_activity

    if not text.strip():
        raise ManualRunError("there's no text to parse")
    if len(text.encode()) > MAX_TEXT:
        raise ManualRunError(f"the text is larger than {MAX_TEXT // (1024 * 1024)} MB")
    chosen = parsers.get(parser) if parser else None
    if parser and chosen is None:
        raise ManualRunError(f"there's no parser {parser}")
    if chosen is not None:
        try:
            chosen.configure(options or {})
        except parsers.OptionsError as exc:
            raise ManualRunError("options: " + "; ".join(f"{k}: {v}" for k, v in exc.errors.items())) from exc
    origin = {"from": "paste", **(origin or {})}
    now = utcnow()
    job = m.Job(
        kind="command", runner="manual", task_id=task.id, command=WHERE.get(origin["from"], "Pasted output"),
        image=None, tool=None, container_flags=[], env={}, var_bindings={}, status=m.JobStatus.succeeded, exit_code=0,
        queued_at=now, dispatched_at=now, started_at=now, finished_at=now, created_by=user.id, run_as=user.id,
        origin=origin, stdout_bytes=len(text.encode()),
        output_specs=[{"name": OUTPUT, "type": chosen.type if chosen else None, "parser": chosen.name if chosen else None,
                       "source": None, "options": options or {}}],
    )
    db.add(job)
    await db.flush()
    db.add(m.JobLogChunk(job_id=job.id, seq=0, stream="stdout", data=text))
    read = f", read with {chosen.name}" if chosen else (", for the AI to draft a parser" if ai else "")
    await record_activity(db, type="job.manual", message=f"Brought {WHERE.get(origin['from'], 'output').lower()} into “{task.title}”{read}",
                          project_id=task.project_id, task_id=task.id, payload={"job_id": str(job.id), "origin": origin},
                          **actor_of(user))
    await db.commit()
    publish_job(job, task.project_id)
    await publish_task(db, task)
    await parse_job(db, job)
    if chosen is None and ai:
        await draft(db, job, task, user)
    return job


async def draft(db: AsyncSession, job: m.Job, task: m.Task, user: m.User) -> m.ParserProposal:
    """Have the AI draft a parser for the run's unread output (in the background). Commits."""
    from . import ai as ai_module
    from . import llm
    from .services import publish_parser_proposal

    if not await llm.enabled(db):
        raise ManualRunError("no AI model is set up (Settings → AI)")
    proposal = m.ParserProposal(job_id=job.id, project_id=task.project_id, output_name=OUTPUT, failed_parser="",
                                failure=UNREAD, status="drafting", requested_by=user.id)
    db.add(proposal)
    await db.commit()
    await publish_parser_proposal(db, proposal)
    ai_module.schedule_parser(proposal.id)
    return proposal


def origin_of(kind: str, ident: uuid.UUID | str | None = None, lines: tuple[int, int] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"from": kind}
    if ident is not None:
        out["id"] = str(ident)
    if lines is not None:
        out["lines"] = list(lines)
    return out
