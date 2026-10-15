"""Parse a finished job's artifacts (or stdout) into typed JobOutput rows (Phase 2b).

Runs after `job.finished` (the agent uploads artifacts before it sends that), in a
background task so a large file never blocks the agent connection, and on demand from the
API. Parse failures are stored on the output and never change the job's status.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import parsers
from .command_library import load_spec
from .command_spec import OutputSpec
from .config import settings
from .db import SessionLocal
from .events import bus
from .schemas import JobOutputOut

log = logging.getLogger(__name__)

# Parsers read whole files into memory; bigger artifacts are kept but not parsed.
MAX_PARSE_BYTES = 64 * 1024 * 1024

_background: set[asyncio.Task[Any]] = set()


def output_specs_for(spec_outputs: list[OutputSpec]) -> list[dict[str, Any]]:
    return [o.model_dump(mode="json") for o in spec_outputs if o.parser]


def _matches(source: str, filename: str) -> bool:
    """Artifact filenames are paths relative to /work; sources are /work paths or globs."""
    pattern = source.removeprefix("/work").lstrip("/")
    if pattern.endswith("/**"):
        return filename.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(filename, pattern)


async def _specs(db: AsyncSession, job: m.Job) -> list[OutputSpec]:
    raw = job.output_specs
    if raw is None:
        # Jobs dispatched before 2b: fall back to the card's current definition.
        task = await db.get(m.Task, job.task_id)
        spec = await load_spec(db, task.command_ref) if task and task.command_ref else None
        return [o for o in spec.outputs if o.parser] if spec else []
    specs = []
    for item in raw:
        try:
            specs.append(OutputSpec.model_validate(item))
        except ValidationError as exc:  # e.g. a parser that has since been removed
            log.warning("job %s: skipping output spec %s: %s", job.id, item.get("name"), exc)
    return specs


async def _stdout(db: AsyncSession, job_id: uuid.UUID) -> bytes:
    rows = await db.execute(
        select(m.JobLogChunk.data)
        .where(m.JobLogChunk.job_id == job_id, m.JobLogChunk.stream == "stdout")
        .order_by(m.JobLogChunk.seq)
    )
    return "".join(d for (d,) in rows).encode()


def _read_all(paths: list[Path]) -> list[bytes]:
    return [p.read_bytes() for p in paths]


def run_parser(parser: parsers.Parser, options: parsers.Options, blobs: list[bytes]) -> Any:
    """Parse one or more inputs; list types are concatenated, scalars use the first."""
    if not parser.is_list:
        return parser.run(blobs[0], options)
    merged: list[Any] = []
    for blob in blobs:
        merged.extend(parser.run(blob, options))
    return merged


async def parse_job(db: AsyncSession, job: m.Job) -> list[m.JobOutput]:
    """(Re)compute every output of a job, replacing any stored ones. Commits."""
    specs = await _specs(db, job)
    artifacts = (
        (await db.execute(select(m.Attachment).where(m.Attachment.job_id == job.id)))
        .scalars()
        .all()
    )
    results: list[m.JobOutput] = []
    for spec in specs:
        parser = parsers.get(spec.parser or "")
        if parser is None:
            continue
        row = m.JobOutput(job_id=job.id, name=spec.name, type=parser.type, parser=parser.name)
        try:
            if spec.source:
                files = sorted(
                    (a for a in artifacts if _matches(spec.source, a.filename)),
                    key=lambda a: a.filename,
                )
                if not files:
                    raise parsers.ParseError(
                        f"no file matched {spec.source} (the tool may have stopped before writing it)"
                    )
                too_big = [a.filename for a in files if a.size > MAX_PARSE_BYTES]
                if too_big:
                    raise parsers.ParseError(f"{too_big[0]} is too large to parse")
                row.sources = [a.filename for a in files]
                paths = [settings.data_dir / a.storage_path for a in files]
                blobs = await asyncio.to_thread(_read_all, paths)
            else:
                row.sources = ["stdout"]
                blobs = [await _stdout(db, job.id)]
            options = parser.configure(spec.options)
            row.value = await asyncio.to_thread(run_parser, parser, options, blobs)
        except (parsers.ParseError, parsers.OptionsError) as exc:
            row.error = str(exc)
        except OSError as exc:
            row.error = f"could not read the artifact: {exc.strerror or exc}"
        except Exception as exc:  # a parser bug must not lose the other outputs
            log.exception("parser %s failed on job %s", parser.name, job.id)
            row.error = f"parser error: {type(exc).__name__}: {exc}"
        results.append(row)

    await db.execute(delete(m.JobOutput).where(m.JobOutput.job_id == job.id))
    db.add_all(results)
    await db.commit()
    task = await db.get(m.Task, job.task_id)
    if task is not None:
        bus.publish(
            "job.outputs",
            task.project_id,
            {
                "job_id": job.id,
                "task_id": task.id,
                "outputs": [JobOutputOut.model_validate(r) for r in results],
            },
        )
    return results


async def _parse_in_background(job_id: uuid.UUID) -> None:
    try:
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            if job is not None:
                await parse_job(db, job)
    except Exception:  # background task: log, never crash the gateway
        log.exception("parsing outputs of job %s failed", job_id)


def schedule_parse(job_id: uuid.UUID) -> None:
    task = asyncio.create_task(_parse_in_background(job_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
