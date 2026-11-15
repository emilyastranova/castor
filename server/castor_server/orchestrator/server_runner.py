"""Runs the jobs no agent runs: HTTP actions (docs/ACTIONS.md).

The runner speaks to the gateway's job handlers in-process (`job.started`, `job.log`,
`job.finished`) as an agent would, so a server-run job gets the same logs, masking, activity,
alerts, parsing, playbook steps and summaries as any other job.

A request waits for its rate limits, follows the fixed retry rules (Retry-After on 429/503,
backoff on 5xx, timeouts and dropped connections; nothing for other 4xx) and pages through
results. Progress is saved after every page and batch (`Job.progress`, with the records so far
in a file), so a retry or a resume carries on from there. A wait longer than `PARK_AFTER_S`
parks the job (status `waiting`, `resume_at`): it gives its slot back and the dispatcher
starts it again when the time comes."""

from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import func, select

from .. import models as m
from .. import outbound, variables
from ..command_spec import CommandSpec
from ..config import settings
from ..db import SessionLocal
from ..http_render import Rendered, RenderError, render_request
from ..http_spec import Expect, Paginate, Retry
from ..parser_spec import _dig
from ..rate_limits import Scope, limiter, scopes_for
from ..services import publish_job, publish_task, utcnow

log = logging.getLogger(__name__)

NAME = "Castor server"
PARK_AFTER_S = 30.0  # waits up to this long happen in place; longer ones park the job
MAX_WAIT_S = 24 * 3600  # without a job timeout, the longest a job waits in all
LOG_CHUNK = 32 * 1024
TEXTUAL = ("text/", "json", "xml", "javascript", "x-www-form-urlencoded", "yaml", "csv")
HIDDEN_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}


@dataclass
class ServerConnection:
    """What the gateway's handlers see as the 'agent' of a server-run job."""

    agent_id: None = None
    name: str = NAME
    running: set[uuid.UUID] = field(default_factory=set)


class Park(Exception):
    def __init__(self, seconds: float, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds
        self.reason = reason


class Failed(Exception):
    """The job fails with this message (exit 1)."""


@dataclass
class _Run:
    job_id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID
    spec: CommandSpec
    args: dict[str, Any]
    resolved: variables.Resolved
    scopes: list[Scope]
    rules: outbound.Policy
    deadline: float | None
    progress: dict[str, Any]
    seq: int = 0
    records_path: Path | None = None
    texts: list[str] = field(default_factory=list)


def records_file(job_id: uuid.UUID) -> Path:
    return settings.data_dir / "actions" / f"{job_id}.jsonl"


class ServerRunner:
    def __init__(self) -> None:
        self.conn = ServerConnection()
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stopping = False

    @property
    def free_slots(self) -> int:
        return max(settings.server_runner_slots - len(self._tasks), 0)

    def start(self, job_id: uuid.UUID, resolved: variables.Resolved) -> None:
        self.conn.running.add(job_id)
        self._tasks[job_id] = asyncio.create_task(self._guarded(job_id, resolved), name=f"http-job-{job_id}")

    def cancel(self, job_id: uuid.UUID) -> bool:
        task = self._tasks.get(job_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def stop(self) -> None:
        """Shutting down: running jobs are left to carry on at the next start (recover)."""
        self._stopping = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def recover(self) -> int:
        """At startup: server-run jobs the last process was running carry on from their
        saved progress (they're parked to resume now)."""
        async with SessionLocal() as db:
            jobs = (await db.execute(select(m.Job).where(
                m.Job.runner == "server", m.Job.status.in_([m.JobStatus.dispatched, m.JobStatus.running])))).scalars().all()
            for job in jobs:
                job.status = m.JobStatus.waiting
                job.resume_at = utcnow()
            await db.commit()
        return len(jobs)

    async def _guarded(self, job_id: uuid.UUID, resolved: variables.Resolved) -> None:
        try:
            await self._run(job_id, resolved)
        except asyncio.CancelledError:
            if not self._stopping:
                await asyncio.shield(self._finish(job_id, 1, "cancelled"))
        except Exception as exc:
            log.exception("http job %s failed", job_id)
            await asyncio.shield(self._finish(job_id, 1, f"Castor couldn't run it: {exc}"))
        finally:
            self._tasks.pop(job_id, None)
            self.conn.running.discard(job_id)
            if not self._stopping:
                from .dispatcher import dispatcher

                dispatcher.wake()

    # ----- talking to the gateway -----

    async def _log(self, run: _Run, data: str, stream: str = "system") -> None:
        from .gateway import gateway

        for start in range(0, len(data), LOG_CHUNK) or [0]:
            run.seq += 1
            await gateway._on_job_log(self.conn, {"job_id": str(run.job_id), "stream": stream,
                                                  "data": data[start:start + LOG_CHUNK], "seq": run.seq})

    async def _finish(self, job_id: uuid.UUID, exit_code: int, error: str | None) -> None:
        from .gateway import gateway

        records_file(job_id).unlink(missing_ok=True)
        await gateway._on_job_finished(self.conn, {"job_id": str(job_id), "exit_code": exit_code, "error": error})

    # ----- one job -----

    async def _run(self, job_id: uuid.UUID, resolved: variables.Resolved) -> None:
        async with SessionLocal() as db:
            job = await db.get(m.Job, job_id)
            task = await db.get(m.Task, job.task_id) if job else None
            if job is None or task is None:
                return
            resumed = job.started_at is not None
            job.status = m.JobStatus.running
            job.resume_at = None
            if not resumed:
                job.started_at = utcnow()
            spec = CommandSpec.model_validate({"id": "job", "name": task.title, "kind": "http",
                                               **(job.request or {}).get("spec", {})})
            seq = (await db.execute(select(func.max(m.JobLogChunk.seq)).where(m.JobLogChunk.job_id == job.id))).scalar() or 0
            started = job.started_at
            run = _Run(
                job_id=job.id, task_id=task.id, project_id=task.project_id, spec=spec,
                args=(job.request or {}).get("args", {}), resolved=resolved,
                scopes=await scopes_for(db, task, job.var_bindings or {}, spec.rate_limit),
                rules=await outbound.policy(db),
                deadline=(started.timestamp() + job.timeout_s) if job.timeout_s else started.timestamp() + MAX_WAIT_S,
                progress=dict(job.progress or {}), seq=seq,
            )
            await db.commit()
            publish_job(job, task.project_id)
            await publish_task(db, task)
        if resumed:
            await self._log(run, f"Carrying on{self._where(run)}\n")
        try:
            code, error = await self._execute(run)
        except Park as park:
            await self._park(run, park)
            return
        except Failed as exc:
            code, error = 1, str(exc)
        await self._finish(job_id, code, error)

    @staticmethod
    def _where(run: _Run) -> str:
        p = run.progress
        parts = []
        if p.get("batch"):
            parts.append(f"batch {p['batch'] + 1}")
        if p.get("pages"):
            parts.append(f"page {p['pages'] + 1}")
        return f" from {', '.join(parts)}" if parts else ""

    async def _park(self, run: _Run, park: Park) -> None:
        resume = utcnow() + timedelta(seconds=park.seconds)
        when = resume.strftime("%H:%M:%S UTC" if resume.date() == utcnow().date() else "%Y-%m-%d %H:%M UTC")
        await self._log(run, f"Waiting {_duration(park.seconds)} ({park.reason}); this run will carry on at {when}\n")
        async with SessionLocal() as db:
            job = await db.get(m.Job, run.job_id)
            if job is None or job.status != m.JobStatus.running:
                return
            job.status = m.JobStatus.waiting
            job.resume_at = resume
            job.progress = run.progress
            task = await db.get(m.Task, job.task_id)
            await db.commit()
            publish_job(job, run.project_id)
            if task is not None:
                await publish_task(db, task)

    async def _save_progress(self, run: _Run) -> None:
        async with SessionLocal() as db:
            job = await db.get(m.Job, run.job_id)
            if job is not None:
                job.progress = dict(run.progress)
                await db.commit()

    async def _wait(self, run: _Run, seconds: float, reason: str) -> None:
        """Wait here, or park the job if it's long; fail if it would run past the deadline."""
        if run.deadline is not None and time.time() + seconds > run.deadline:
            raise Failed(f"it would have to wait {_duration(seconds)} ({reason}), past its time limit")
        if seconds > PARK_AFTER_S:
            raise Park(seconds, reason)
        if seconds >= 0.5:
            await self._log(run, f"Waiting {_duration(max(seconds, 1))} ({reason})\n")
        await asyncio.sleep(seconds)

    async def _execute(self, run: _Run) -> tuple[int, str | None]:
        spec = run.spec
        batches = self._batches(run)
        paged = spec.paginate is not None or len(batches) > 1
        if paged:
            run.records_path = records_file(run.job_id)
            run.records_path.parent.mkdir(parents=True, exist_ok=True)
            if not run.progress:
                run.records_path.unlink(missing_ok=True)
        async with outbound.client(run.rules, spec.request.timeout_s) as client:
            for index in range(run.progress.get("batch", 0), len(batches)):
                args = dict(run.args)
                if spec.batch is not None:
                    args[spec.batch.arg] = batches[index]
                    if len(batches) > 1:
                        await self._log(run, f"Batch {index + 1} of {len(batches)}: {len(batches[index])} values\n")
                try:
                    base = render_request(spec, args, self._lookup(run.resolved))
                except RenderError as exc:
                    raise Failed(str(exc)) from exc
                if spec.paginate is not None:
                    await self._pages(run, client, base, spec.paginate)
                else:
                    response = await self._send(run, client, base)
                    await self._take(run, response, paged)
                run.progress = {"batch": index + 1}
                await self._save_progress(run)
        return await self._output(run, paged)

    def _batches(self, run: _Run) -> list[list[Any]]:
        spec = run.spec
        if spec.batch is None:
            return [[]]
        values = run.args.get(spec.batch.arg) or []
        size = run.progress.get("batch_size") or spec.batch.size
        return [values[i:i + size] for i in range(0, len(values), size)] or [[]]

    @staticmethod
    def _lookup(resolved: variables.Resolved):
        def get(slot: str, field_name: str) -> str | None:
            return resolved.env.get(variables.env_name(slot, field_name))

        return get

    # ----- paging -----

    async def _pages(self, run: _Run, client: httpx.AsyncClient, base: Rendered, pg: Paginate) -> None:
        p = run.progress
        pages, items = p.get("pages", 0), p.get("items", 0)
        state = p.get("state")
        if pages == 0:
            state = None
        while pages < pg.stop.max_pages and items < pg.stop.max_items:
            request = self._page_request(base, pg, pages, state)
            if request is None:
                break
            started = time.monotonic()
            response = await self._send(run, client, request, quiet=pages > 0)
            try:
                doc = response.json()
            except ValueError:
                raise Failed(f"page {pages + 1} isn't JSON, so it can't be paged through") from None
            found = _dig(doc, pg.items) if pg.items else doc
            records = found if isinstance(found, list) else ([] if found is None else [found])
            records = records[: pg.stop.max_items - items]
            self._append(run, records)
            pages += 1
            items += len(records)
            await self._log(run, f"page {pages}: {len(records)} items ({int((time.monotonic() - started) * 1000)} ms)\n")
            state = self._next_state(response, doc, pg, pages, len(records), request)
            run.progress.update({"pages": pages, "items": items, "state": state})
            await self._save_progress(run)
            if state is None or not records:
                break
        else:
            await self._log(run, f"Stopped after {pages} pages and {items} items (the action's limit)\n")
        run.progress.update({"pages": 0, "items": items, "state": None})

    @staticmethod
    def _page_request(base: Rendered, pg: Paginate, pages: int, state: Any) -> Rendered | None:
        if pages == 0:
            first: dict[str, Any] = {}
            if pg.style == "offset":
                first = {pg.param: 0}
            elif pg.style == "page":
                first = {pg.param: pg.start}
            if pg.size_param and pg.size:
                first[pg.size_param] = pg.size
            return base.with_params(first) if first else base
        if state is None:
            return None
        if pg.style == "link":
            return Rendered(base.method, state, [], dict(base.headers), base.content, base.timeout_s)
        extra = {pg.cursor.param: state} if pg.style == "cursor" else {pg.param: state}
        if pg.size_param and pg.size:
            extra[pg.size_param] = pg.size
        return base.with_params(extra)

    @staticmethod
    def _next_state(response: httpx.Response, doc: Any, pg: Paginate, pages: int, got: int, request: Rendered) -> Any:
        if pg.style == "cursor":
            cursor = _dig(doc, pg.cursor.from_)
            return None if cursor in (None, "", False) else cursor
        if pg.style == "link":
            nxt = _dig(doc, pg.next) if pg.next else response.links.get("next", {}).get("url")
            if not nxt:
                return None
            url = urljoin(str(response.url), str(nxt))
            if urlsplit(url).hostname != urlsplit(request.url).hostname:
                raise Failed(f"the next page is on another host ({urlsplit(url).hostname}); Castor only follows them on the same one")
            return url
        if pg.size and got < pg.size:
            return None  # a short page is the last
        if pg.style == "offset":
            return pages * (pg.size or got)
        return pg.start + pages

    def _append(self, run: _Run, records: list[Any]) -> None:
        with run.records_path.open("a", encoding="utf-8") as out:
            for record in records:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ----- one request, with the fixed retry rules -----

    async def _send(self, run: _Run, client: httpx.AsyncClient, req: Rendered, *, quiet: bool = False) -> httpx.Response:
        retry = run.spec.retry or Retry()
        expect = run.spec.expect or Expect()
        attempt = 0
        told_waits = 0
        if not quiet:
            await self._log(run, self._request_text(run, req))
        while True:
            wait, full = await limiter.take(run.scopes)
            if wait > 0:
                await self._wait(run, wait, f"{full.label}, {full.rate}")
                continue
            started = time.monotonic()
            try:
                response = await self._request(run, client, req)
            except outbound.OutboundRefused as exc:
                raise Failed(f"Castor won't send this request: {exc}") from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                reason = "timed out" if isinstance(exc, httpx.TimeoutException) else (str(exc) or type(exc).__name__)
                if attempt >= retry.attempts:
                    raise Failed(f"{req.method} {_short(req.url)}: {reason}" + (f" (after {attempt + 1} tries)" if attempt else "")) from exc
                attempt += 1
                await self._log(run, f"{reason}; trying again ({attempt} of {retry.attempts})\n")
                await self._wait(run, _backoff(retry, attempt), "backing off")
                continue
            finally:
                limiter.release(run.scopes)
            ms = int((time.monotonic() - started) * 1000)
            if not quiet or not expect.ok(response.status_code):
                await self._log(run, self._response_text(response, ms))
            pause = _rate_pause(response)
            if pause:
                await limiter.pause([s for s in run.scopes if s.credential] or run.scopes, pause)
            status = response.status_code
            if status in (429, 503) and told_waits < 20:
                told_waits += 1
                after = _retry_after(response) or _backoff(retry, told_waits)
                await limiter.pause([s for s in run.scopes if s.credential] or run.scopes, after)
                await self._wait(run, after, f"the API answered {status}" + (" and said when to try again" if _retry_after(response) else ""))
                continue
            if status >= 500 and attempt < retry.attempts:
                attempt += 1
                await self._log(run, f"trying again ({attempt} of {retry.attempts})\n")
                await self._wait(run, _backoff(retry, attempt), "backing off")
                continue
            if not expect.ok(status):
                wanted = ", ".join(map(str, expect.status)) if expect.status else "2xx"
                snippet = response.text[:300].strip() if _is_text(response) else ""
                raise Failed(f"status {status}, expected {wanted}" + (f": {snippet}" if snippet else ""))
            return response

    async def _request(self, run: _Run, client: httpx.AsyncClient, req: Rendered) -> httpx.Response:
        """One request, following redirects to the same host only (each hop checked)."""
        url, method, content, headers = req.url, req.method, req.content, dict(req.headers)
        params: list[tuple[str, str]] | None = req.params
        for _ in range(6):
            run.rules.check_host(outbound.check_url(url))
            built = client.build_request(method, url, params=params, headers=headers, content=content, timeout=req.timeout_s)
            response = await client.send(built, stream=True)
            try:
                response._content = await self._read(response)
            finally:
                await response.aclose()
            if response.status_code not in (301, 302, 303, 307, 308) or "location" not in response.headers:
                return response
            target = urljoin(str(response.url), response.headers["location"])
            if urlsplit(target).hostname != urlsplit(url).hostname:
                return response  # another host: not followed (its status stands)
            if response.status_code == 303 or (response.status_code in (301, 302) and method == "POST"):
                method, content = "GET", None
                headers.pop("Content-Type", None)
            url, params = target, None
        raise Failed("too many redirects")

    @staticmethod
    async def _read(response: httpx.Response) -> bytes:
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > settings.http_max_bytes:
                raise Failed(f"the response is over {settings.http_max_bytes // (1024 * 1024)} MB")
            chunks.append(chunk)
        return b"".join(chunks)

    def _request_text(self, run: _Run, req: Rendered) -> str:
        """The request as the log shows it: nothing secret."""
        url = run.resolved.redact(req.url)
        if req.params:
            from urllib.parse import urlencode

            url += ("&" if "?" in url else "?") + run.resolved.redact(urlencode(req.params))
        lines = [f"> {req.method} {url}"]
        for name, value in req.headers.items():
            lines.append(f"  {name}: {'••••' if name.lower() in HIDDEN_HEADERS else run.resolved.redact(value)}")
        if req.content:
            lines.append(f"  ({_size(len(req.content))} body, {req.headers.get('Content-Type', 'no type')})")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _response_text(response: httpx.Response, ms: int) -> str:
        lines = [f"< {response.status_code} {response.reason_phrase} ({ms} ms, {_size(len(response.content))})"]
        for name, value in response.headers.items():
            if name.lower() in ("date", "server", "connection", "transfer-encoding", "content-encoding", "vary"):
                continue
            lines.append(f"  {name}: {'••••' if name.lower() in HIDDEN_HEADERS else value[:200]}")
        return "\n".join(lines) + "\n"

    # ----- results -----

    async def _take(self, run: _Run, response: httpx.Response, paged: bool) -> None:
        """One unpaged response: its records joined with the others (batches), kept as a
        file (save_as, or a body that isn't text), or the job's output."""
        req = run.spec.request
        if req.save_as or not _is_text(response):
            name = req.save_as or _file_name(response)
            await self._artifact(run, name, response)
            return
        if paged:
            try:
                doc = response.json()
            except ValueError:
                run.texts.append(response.text)
                return
            self._append(run, doc if isinstance(doc, list) else [doc])
            return
        run.texts.append(response.text)

    async def _artifact(self, run: _Run, name: str, response: httpx.Response) -> None:
        from .. import artifacts

        async def chunks():
            yield response.content

        async with SessionLocal() as db:
            job = await db.get(m.Job, run.job_id)
            try:
                await artifacts.store(db, job, name, response.headers.get("content-type"), chunks())
            except artifacts.ArtifactRefused as exc:
                raise Failed(f"couldn't keep {name}: {exc}") from exc
        await self._log(run, f"Kept the response as {name} ({_size(len(response.content))})\n")

    async def _output(self, run: _Run, paged: bool) -> tuple[int, str | None]:
        if paged and run.records_path is not None and run.records_path.exists():
            records = [json.loads(line) for line in run.records_path.read_text(encoding="utf-8").splitlines() if line]
            text = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            await self._log(run, f"{len(records)} records in all\n")
            if run.texts:
                text += "\n".join(run.texts)
        else:
            text = "\n".join(run.texts)
        if text:
            await self._log(run, text if text.endswith("\n") else text + "\n", stream="stdout")
        return 0, None


def _is_text(response: httpx.Response) -> bool:
    ct = response.headers.get("content-type", "").lower()
    return not ct or any(t in ct for t in TEXTUAL)


def _file_name(response: httpx.Response) -> str:
    import mimetypes
    import re

    disposition = response.headers.get("content-disposition", "")
    found = re.search(r'filename="?([^";]+)"?', disposition)
    if found:
        name = Path(found.group(1)).name
        if name and not name.startswith("."):
            return name[:200]
    ext = mimetypes.guess_extension(response.headers.get("content-type", "").split(";")[0].strip()) or ".bin"
    return f"response{ext}"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - datetime.now(UTC)).total_seconds(), 0)


def _rate_pause(response: httpx.Response) -> float:
    """X-RateLimit-Remaining: 0 with a reset time: how long until requests are allowed."""
    remaining = response.headers.get("x-ratelimit-remaining") or response.headers.get("ratelimit-remaining")
    reset = response.headers.get("x-ratelimit-reset") or response.headers.get("ratelimit-reset")
    if remaining is None or reset is None or remaining.strip() != "0":
        return 0.0
    try:
        value = float(reset)
    except ValueError:
        return 0.0
    if value > 1e9:  # an epoch time
        value -= time.time()
    return min(max(value, 0.0), 3600.0)


def _backoff(retry: Retry, attempt: int) -> float:
    return min(retry.backoff_s * (2 ** (attempt - 1)), retry.max_backoff_s)


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s" if seconds % 60 else f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} kB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _short(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


server_runner = ServerRunner()
