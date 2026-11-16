"""Runs the jobs no agent runs: HTTP actions (docs/ACTIONS.md).

The runner speaks to the gateway's job handlers in-process (`job.started`, `job.log`,
`job.finished`) as an agent would, so a server-run job gets the same logs, masking, activity,
alerts, parsing, playbook steps and summaries as any other job.

A request waits for its rate limits, follows the fixed retry rules (Retry-After on 429/503,
backoff on 5xx, timeouts and dropped connections; nothing for other 4xx) and pages through
results. Progress is saved after every page and batch (`Job.progress`, with the records so far
in a file), so a retry or a resume carries on from there. A wait longer than `PARK_AFTER_S`
parks the job (status `waiting`, `resume_at`): it gives its slot back and the dispatcher
starts it again when the time comes.

When the fixed rules run out (`Trouble`) on a playbook step with `on_failure: ai`, the AI picks a
recovery move (`_recover`): wait, smaller batches or pages, slower, skip the batch, or leave it to
a person. A job with `via_agent_id` sends each request through that agent (`http.request`), so
it goes out from the agent's network; everything else stays here."""

from __future__ import annotations

import asyncio
import contextlib
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
# Paged results past either of these are kept as a JSON-lines artifact (RECORDS_FILE) rather
# than printed as one array: the console shows the first few, parsers read the file.
LARGE_RECORDS = 20_000
LARGE_BYTES = 8 * 1024 * 1024
RECORDS_FILE = "records.jsonl"
SHOWN_RECORDS = 20
LOG_CHUNK = 32 * 1024
TEXTUAL = ("text/", "json", "xml", "javascript", "x-www-form-urlencoded", "yaml", "csv")
HIDDEN_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}


@dataclass
class ServerConnection:
    """What the gateway's handlers see as the 'agent' of a server-run job."""

    agent_id: None = None
    name: str = NAME
    running: set[uuid.UUID] = field(default_factory=set)
    services: set[uuid.UUID] = field(default_factory=set)  # never any: services run on agents


class Park(Exception):
    def __init__(self, seconds: float, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds
        self.reason = reason


class Failed(Exception):
    """The job fails with this message (exit 1)."""


class Trouble(Exception):
    """A request went wrong after the fixed retry rules: the AI may recover (`_recover`),
    otherwise the job fails with the message."""

    def __init__(self, message: str, request: Rendered, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.request = request
        self.response = response


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
    # The agent its requests go out through (None: from here).
    via: uuid.UUID | None = None
    # A playbook step with on_failure: ai: {"config": AiStepConfig, "run_id", "step_id"}.
    ai: dict[str, Any] | None = None
    # More secrets to mask in the log: an MCP connection's (its variable set isn't the card's).
    extra: variables.Resolved | None = None
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

        if run.extra is not None:
            data = run.extra.redact(data)
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
            spec = CommandSpec.model_validate({"id": "job", "name": task.title, "kind": job.kind,
                                               **(job.request or {}).get("spec", {})})
            seq = (await db.execute(select(func.max(m.JobLogChunk.seq)).where(m.JobLogChunk.job_id == job.id))).scalar() or 0
            started = job.started_at
            run = _Run(
                job_id=job.id, task_id=task.id, project_id=task.project_id, spec=spec,
                args=(job.request or {}).get("args", {}), resolved=resolved,
                scopes=await scopes_for(db, task, job.var_bindings or {}, spec.rate_limit, spec.plugin.id if spec.plugin else None),
                rules=await outbound.policy(db),
                deadline=(started.timestamp() + job.timeout_s) if job.timeout_s else started.timestamp() + MAX_WAIT_S,
                progress=dict(job.progress or {}), seq=seq, via=job.via_agent_id,
                ai=await _ai_settings(db, task),
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
        if p.get("offset"):
            parts.append(f"value {p['offset'] + 1}")
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
        if spec.kind in ("mcp", "plugin"):
            # One call; when the fixed rules run out, recovery (or failing) decides like a request's.
            while True:
                try:
                    return await (self._call_mcp(run) if spec.kind == "mcp" else self._call_plugin(run))
                except Trouble as trouble:
                    await self._recover(run, trouble, None)  # returns to try again, or raises
        p = run.progress
        values = list(run.args.get(spec.batch.arg) or []) if spec.batch is not None else None
        if values is not None and "batch" in p and "offset" not in p:  # saved before batches counted values
            p["offset"] = p.pop("batch") * spec.batch.size
        paged = spec.paginate is not None or (values is not None and len(values) > spec.batch.size)
        if paged:
            run.records_path = records_file(run.job_id)
            run.records_path.parent.mkdir(parents=True, exist_ok=True)
            if not p:
                run.records_path.unlink(missing_ok=True)
        async with outbound.client(run.rules, spec.request.timeout_s) as client:
            while True:
                offset = p.get("offset", 0)
                chunk: list[Any] | None = None
                args = dict(run.args)
                if values is not None:
                    if p.get("done") or (values and offset >= len(values)):
                        break
                    size = p.get("batch_size") or spec.batch.size
                    chunk = values[offset:offset + size]
                    args[spec.batch.arg] = chunk
                    if len(values) > len(chunk) and not p.get("pages"):
                        which = f"Value {offset + 1}" if len(chunk) == 1 else f"Values {offset + 1}–{offset + len(chunk)}"
                        await self._log(run, f"{which} of {len(values)}\n")
                elif p.get("done"):
                    break
                try:
                    base = render_request(spec, args, self._lookup(run.resolved))
                except RenderError as exc:
                    raise Failed(str(exc)) from exc
                try:
                    if spec.paginate is not None:
                        await self._pages(run, client, base, spec.paginate)
                    else:
                        response = await self._send(run, client, base)
                        await self._take(run, response, paged)
                except Trouble as trouble:
                    if await self._recover(run, trouble, chunk) == "skip":
                        p.setdefault("skipped", []).extend(chunk or [])
                        await self._log(run, f"Skipped values {offset + 1}–{offset + len(chunk or [])}: "
                                             f"{', '.join(map(str, (chunk or [])[:5]))}{' …' if len(chunk or []) > 5 else ''}\n")
                        self._next_piece(run, values, offset, chunk)
                        await self._save_progress(run)
                    continue  # carry on: the same piece again, or the one after a skipped one
                self._next_piece(run, values, offset, chunk)
                await self._save_progress(run)
        if p.get("skipped"):
            await self._log(run, f"{len(p['skipped'])} values were skipped\n")
        return await self._output(run, paged)

    @staticmethod
    def _next_piece(run: _Run, values: list[Any] | None, offset: int, chunk: list[Any] | None) -> None:
        p = run.progress
        p.update({"pages": 0, "page_items": 0, "state": None})
        if values is None or not values:
            p["done"] = True
        else:
            p["offset"] = offset + len(chunk or [])

    @staticmethod
    def _lookup(resolved: variables.Resolved):
        def get(slot: str, field_name: str) -> str | None:
            return resolved.env.get(variables.env_name(slot, field_name))

        return get

    # ----- paging -----

    async def _pages(self, run: _Run, client: httpx.AsyncClient, base: Rendered, pg: Paginate) -> None:
        p = run.progress
        pages, items, here = p.get("pages", 0), p.get("items", 0), p.get("page_items", 0)
        state = p.get("state")
        if pages == 0:
            state, here = None, 0
        size = p.get("page_size") or pg.size
        while pages < pg.stop.max_pages and items < pg.stop.max_items:
            request = self._page_request(base, pg, pages, state, size)
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
            here += len(records)
            await self._log(run, f"page {pages}: {len(records)} items ({int((time.monotonic() - started) * 1000)} ms)\n")
            state = self._next_state(response, doc, pg, pages, len(records), request, size, here)
            run.progress.update({"pages": pages, "items": items, "page_items": here, "state": state})
            await self._save_progress(run)
            if state is None or not records:
                break
        else:
            await self._log(run, f"Stopped after {pages} pages and {items} items (the action's limit)\n")
        run.progress.update({"pages": 0, "items": items, "page_items": 0, "state": None})

    @staticmethod
    def _page_request(base: Rendered, pg: Paginate, pages: int, state: Any, size: int | None) -> Rendered | None:
        if pages == 0:
            first: dict[str, Any] = {}
            if pg.style == "offset":
                first = {pg.param: 0}
            elif pg.style == "page":
                first = {pg.param: pg.start}
            if pg.size_param and size:
                first[pg.size_param] = size
            return base.with_params(first) if first else base
        if state is None:
            return None
        if pg.style == "link":
            return Rendered(base.method, state, [], dict(base.headers), base.content, base.timeout_s)
        extra = {pg.cursor.param: state} if pg.style == "cursor" else {pg.param: state}
        if pg.size_param and size:
            extra[pg.size_param] = size
        return base.with_params(extra)

    @staticmethod
    def _next_state(response: httpx.Response, doc: Any, pg: Paginate, pages: int, got: int, request: Rendered,
                    size: int | None = None, here: int = 0) -> Any:
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
        if size and got < size:
            return None  # a short page is the last
        if pg.style == "offset":
            return here  # the records read so far (so a smaller page size later still lines up)
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
                    raise Trouble(f"{req.method} {_short(req.url)}: {reason}" + (f" (after {attempt + 1} tries)" if attempt else ""), req) from exc
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
            if status in (429, 503) and told_waits >= 20:
                raise Trouble(f"the API kept answering {status}", req, response)
            if status in (429, 503):
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
                snippet = run.resolved.redact(response.text[:300].strip()) if _is_text(response) else ""
                raise Trouble(f"status {status}, expected {wanted}" + (f": {snippet}" if snippet else ""), req, response)
            return response

    async def _request(self, run: _Run, client: httpx.AsyncClient, req: Rendered) -> httpx.Response:
        """One request, following redirects to the same host only (each hop checked)."""
        url, method, content, headers = req.url, req.method, req.content, dict(req.headers)
        params: list[tuple[str, str]] | None = req.params
        for _ in range(6):
            run.rules.check_host(outbound.check_url(url))
            if run.via is not None:
                full = httpx.URL(url).copy_merge_params(params) if params else httpx.URL(url)
                response = await self._through_agent(run, method, str(full), headers, content, req.timeout_s)
            else:
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

    async def _through_agent(self, run: _Run, method: str, url: str, headers: dict[str, str], content: bytes | None,
                             timeout: int) -> httpx.Response:
        """One request made by the agent the job goes through (gateway.http_request). Its
        network decides where the request can go, so the address checks don't apply; the
        allowed hosts still do (checked by the caller)."""
        import base64

        from .gateway import AgentGone, gateway

        conn = gateway.connections.get(run.via)
        if conn is None:
            raise Park(15, "the agent it sends through isn't connected")
        payload = {"job_id": str(run.job_id), "method": method, "url": url, "headers": headers, "timeout_s": timeout,
                   "body": base64.b64encode(content).decode() if content else None, "max_bytes": settings.http_max_bytes}
        try:
            answer = await gateway.http_request(conn, payload, timeout + 30)
        except AgentGone as exc:
            raise Park(15, f"{exc}; it carries on through an agent that's connected") from exc
        except TimeoutError as exc:
            raise httpx.ReadTimeout(f"{conn.name} didn't answer") from exc
        if answer.get("error"):
            kind, error = answer.get("error_kind"), f"{answer['error']} (from {conn.name})"
            if kind == "timeout":
                raise httpx.ReadTimeout(error)
            if kind in ("too_big", "refused"):
                raise Failed(error)
            raise httpx.ConnectError(error)
        body = base64.b64decode(answer.get("body") or "")
        # The agent hands the body over as it came; httpx shouldn't decode or measure it again.
        kept = [tuple(h) for h in answer.get("headers") or [] if str(h[0]).lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(int(answer.get("status") or 0), headers=kept,
                              content=body, request=httpx.Request(method, answer.get("url") or url))

    # ----- when the fixed rules run out -----

    async def _recover(self, run: _Run, trouble: Trouble, chunk: list[Any] | None) -> str:
        """Ask the AI for one move (a playbook step with on_failure: ai); returns "retry" or
        "skip" for the caller, or raises Park (wait) or Failed (no AI, ask, fail, or out of moves)."""
        from .. import ai
        from ..http_spec import parse_rate

        if run.ai is None:
            raise Failed(trouble.message)
        config = run.ai["config"]
        p = run.progress
        moves = p.setdefault("ai_moves", [])
        if len(moves) >= config.max_retries:
            raise Failed(f"{trouble.message} (the AI has already made {len(moves)} recovery move{'s' if len(moves) != 1 else ''})")
        spec = run.spec
        batch_size = p.get("batch_size") or (spec.batch.size if spec.batch else None)
        page_size = p.get("page_size") or (spec.paginate.size if spec.paginate else None)
        response = trouble.response
        context = {
            "what_went_wrong": trouble.message,
            "request": {"method": trouble.request.method, "url": run.resolved.redact(_short(trouble.request.url))},
            "response": None if response is None else {
                "status": response.status_code,
                "headers": {k: v for k, v in response.headers.items() if k.lower() in (
                    "retry-after", "content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
                    "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset")},
                "body_start": run.resolved.redact(response.text[:1000]) if _is_text(response) else "(not text)",
            },
            "progress": {"values_done": p.get("offset", 0), "values_in_all": len(run.args.get(spec.batch.arg) or []) if spec.batch else None,
                         "pages_done_in_this_piece": p.get("pages", 0), "records_so_far": p.get("items", 0)},
            "what_can_change": {
                "batch_size": batch_size, "page_size": page_size if spec.paginate and spec.paginate.style != "page" and spec.paginate.size_param else None,
                "rate_limit": str(run.scopes[-1].rate) if run.scopes else None, "longest_wait_s": config.max_wait_s,
                "can_skip_a_batch": spec.batch is not None,
            },
            "earlier_moves": moves[-5:],
            "operator_instructions": config.instructions,
        }
        try:
            async with SessionLocal() as db:
                said = await ai.recover_http(db, context)
        except ai.AiError as exc:
            raise Failed(f"{trouble.message} (the AI couldn't decide: {exc})") from exc
        move, reason = said["move"], str(said.get("reason") or "")[:500]
        allowed = {"wait": "retry", "smaller": "retry", "slower": "retry", "skip_batch": "skip"}
        if move in allowed and allowed[move] not in config.allow:
            move, reason = "ask", f"it chose {said['move']}, which it may not do on its own here. {reason}"
        elif move not in ("ask", "fail") and said["confidence"] < config.confidence:
            move, reason = "ask", f"it was only {round(said['confidence'] * 100)}% sure about {said['move']}. {reason}"
        moves.append({"move": move, "reason": reason, "model": said["model"], "at": utcnow().isoformat()})
        await self._log(run, f"AI ({said['model']}): {move.replace('_', ' ')}. {reason}\n")
        if move == "wait":
            seconds = max(1.0, min(float(said.get("seconds") or 60), float(config.max_wait_s)))
            await self._save_progress(run)
            await self._wait(run, seconds, "the AI's call")
            return "retry"
        if move == "smaller":
            size = int(said.get("size") or 0)
            if spec.batch is not None and batch_size and 1 <= size < batch_size:
                p["batch_size"] = size
            elif context["what_can_change"]["page_size"] and 1 <= size < page_size:
                p["page_size"] = size
            else:
                move = "ask"
        elif move == "slower":
            try:
                rate = parse_rate(str(said.get("rate") or ""))
            except ValueError:
                rate = None
            if rate is None:
                move = "ask"
            else:
                await self._slow_down(run, str(rate))
        elif move == "skip_batch":
            if chunk is None:
                move = "ask"
            else:
                await self._save_progress(run)
                return "skip"
        if move in ("ask", "fail"):
            p["ai_said"] = {"move": move, "reason": reason}
            await self._save_progress(run)
            raise Failed(f"{trouble.message}. The AI {'left it to a person' if move == 'ask' else 'stopped it'}: {reason}")
        await self._save_progress(run)
        return "retry"

    async def _slow_down(self, run: _Run, rate: str) -> None:
        """The AI's "slower": the card's own rate limit, for this run and after."""
        from .. import rate_limits

        async with SessionLocal() as db:
            task = await db.get(m.Task, run.task_id)
            job = await db.get(m.Job, run.job_id)
            if task is None or job is None:
                return
            task.rate_limit = rate
            rate_limits.limiter.forget(f"task:{task.id}")
            run.scopes = await scopes_for(db, task, job.var_bindings or {}, run.spec.rate_limit)
            await db.commit()
            await publish_task(db, task)

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
        await self._keep(run, name, response.headers.get("content-type"), response.content)
        await self._log(run, f"Kept the response as {name} ({_size(len(response.content))})\n")

    async def _keep(self, run: _Run, name: str, content_type: str | None, data: bytes) -> None:
        from .. import artifacts

        async def chunks():
            yield data

        async with SessionLocal() as db:
            job = await db.get(m.Job, run.job_id)
            try:
                await artifacts.store(db, job, name, content_type, chunks())
            except artifacts.ArtifactRefused as exc:
                raise Failed(f"couldn't keep {name}: {exc}") from exc

    # ----- plugin actions -----

    async def _call_plugin(self, run: _Run) -> tuple[int, str | None]:
        """Run a plugin's library action (docs/ACTIONS.md phase 4): its handler gets the arguments,
        the variable sets' values and the settings of the person who started the card; its log
        lines and progress go to the console; what it returns as `output` is the job's output."""
        from .. import plugins
        from ..plugin_runtime import PluginRuntimeError

        call = run.spec.plugin
        async with SessionLocal() as db:
            p = await db.get(m.Plugin, call.id)
            job = await db.get(m.Job, run.job_id)
            task = await db.get(m.Task, run.task_id)
            project = await db.get(m.Project, run.project_id)
            if p is None:
                raise Failed(f"the plugin {call.id} isn't installed")
            if not p.enabled:
                raise Failed(f"{p.name} is turned off")
            if not await plugins.enabled_in(db, p, run.project_id):
                raise Failed(f"{p.name} isn't enabled in this project")
            person = await db.get(m.User, job.created_by) if job and job.created_by else None
            if person is not None and (missing := await plugins.missing_for(db, p, person.id)):
                raise Failed(f"{p.name} needs {person.display_name}'s own {', '.join(missing)} first (Your plugin settings)")
            params = {
                "run_id": str(run.job_id), "action": call.action,
                "user": {"id": str(person.id), "username": person.username, "display_name": person.display_name} if person else None,
                "project": {"id": str(project.id), "codename": project.codename, "name": project.name} if project else None,
                "task": {"id": str(task.id), "title": task.title} if task else None,
                "asset": None, "file": None,
                "settings": await plugins.settings_of(db, p, person.id if person else None),
                "job": {"id": str(run.job_id)},
                "args": run.args,
                "vars": self._var_values(run),
            }
            name = p.name
        await self._log(run, f"> {name}: {call.action}\n  {json.dumps(run.args, ensure_ascii=False)[:4000]}\n")
        while True:
            wait, full = await limiter.take(run.scopes)
            if wait <= 0:
                break
            await self._wait(run, wait, f"{full.label}, {full.rate}")

        async def to_job(text: str) -> None:
            await self._log(run, text)

        plugins.job_runs[str(run.job_id)] = to_job
        started = time.monotonic()
        proc = None
        try:
            proc = await plugins.ensure_running(call.id)
            result = await proc.request("action", params, timeout=call.timeout_minutes * 60)
        except asyncio.CancelledError:
            if proc is not None:  # the card's job was cancelled: tell the handler (run.signal)
                with contextlib.suppress(Exception):
                    await asyncio.shield(proc.notify("cancel", {"run_id": str(run.job_id)}))
            raise
        except (plugins.PluginError, PluginRuntimeError) as exc:
            raise Trouble(f"{name} · {call.action}: {exc}", Rendered("PLUGIN", f"plugin://{call.id}/{call.action}")) from exc
        finally:
            # Log lines the plugin sent just before its result are handled in tasks of their own:
            # keep taking them for a moment.
            asyncio.get_running_loop().call_later(3, plugins.job_runs.pop, str(run.job_id), None)
            limiter.release(run.scopes)
        result = result if isinstance(result, dict) else {}
        message = str(result.get("message") or "")
        output = result.get("output")
        await self._log(run, f"< done ({int((time.monotonic() - started) * 1000)} ms)" + (f": {message}" if message else "") + "\n")
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, indent=2) if output is not None else ""
        if text:
            await self._log(run, text if text.endswith("\n") else text + "\n", stream="stdout")
        return 0, None

    @staticmethod
    def _var_values(run: _Run) -> dict[str, dict[str, str]]:
        """{slot: {field: value}} from the bound variable sets (files aren't passed)."""
        out: dict[str, dict[str, str]] = {}
        for slot in run.spec.vars:
            prefix = f"CASTOR_VAR_{slot.name.upper()}_"
            out[slot.name] = {k[len(prefix):].lower(): v for k, v in run.resolved.env.items() if k.startswith(prefix)}
        return out

    # ----- MCP actions -----

    async def _call_mcp(self, run: _Run) -> tuple[int, str | None]:
        """Call the tool on the connection (docs/ACTIONS.md phase 3). The server's log messages
        and progress go to the console as they come; the result is the job's output, with
        images, audio and files kept on the card."""
        from .. import mcp_client, mcp_connections
        from ..http_render import mcp_arguments

        spec, call = run.spec, run.spec.mcp
        retry = spec.retry or Retry()
        try:
            arguments = mcp_arguments(spec, run.args, self._lookup(run.resolved))
        except RenderError as exc:
            raise Failed(str(exc)) from exc
        target = Rendered("MCP", f"mcp://{call.server}/{call.tool}")
        attempt = 0
        while True:
            wait, full = await limiter.take(run.scopes)
            if wait > 0:
                await self._wait(run, wait, f"{full.label}, {full.rate}")
                continue
            try:
                async with SessionLocal() as db:
                    row = await db.get(m.McpConnection, call.server)
                    if row is None:
                        raise Failed(f"there's no MCP server {call.server} in Settings → MCP servers")
                    if not mcp_connections.allowed(row, run.project_id):
                        raise Failed(f"{row.name} can't be used in this project")
                    name = row.name
                    session, secret, where = await mcp_connections.open_session(db, row, run.project_id, call.timeout_s)
                run.extra = secret
                if attempt == 0:
                    info = session.server_info or {}
                    await self._log(run, f"> {name} ({info.get('name') or 'MCP server'} {info.get('version') or ''}".rstrip()
                                         + f", from {where}): {call.tool}\n  {json.dumps(arguments, ensure_ascii=False)[:4000]}\n")
                started = time.monotonic()
                try:
                    result = await asyncio.wait_for(mcp_client.call_tool(session, call.tool, arguments, self._mcp_note(run)), call.timeout_s)
                finally:
                    await session.close()
                break
            except mcp_connections.Unusable as exc:
                raise Failed(f"{call.server}: {exc}") from exc
            except outbound.OutboundRefused as exc:
                raise Failed(f"Castor won't connect to {call.server}: {exc}") from exc
            except mcp_client.McpRpcError as exc:
                raise Failed(f"{call.tool}: {exc}") from exc
            except (mcp_client.McpError, TimeoutError) as exc:
                reason = f"timed out after {call.timeout_s} s" if isinstance(exc, TimeoutError) else str(exc)
                if attempt >= retry.attempts:
                    raise Trouble(f"{call.server} · {call.tool}: {reason}" + (f" (after {attempt + 1} tries)" if attempt else ""), target) from exc
                attempt += 1
                await self._log(run, f"{reason}; trying again ({attempt} of {retry.attempts})\n")
                await self._wait(run, _backoff(retry, attempt), "backing off")
            finally:
                limiter.release(run.scopes)
        return await self._mcp_result(run, result, int((time.monotonic() - started) * 1000))

    def _mcp_note(self, run: _Run):
        async def note(msg: dict[str, Any]) -> None:
            params = msg.get("params") or {}
            if msg.get("method") == "notifications/message":
                data = params.get("data")
                text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
                await self._log(run, f"[{params.get('level', 'info')}] {text}\n")
            elif msg.get("method") == "notifications/progress":
                total = f" of {params['total']}" if params.get("total") is not None else ""
                await self._log(run, f"progress {params.get('progress')}{total}" + (f": {params['message']}" if params.get("message") else "") + "\n")

        return note

    async def _mcp_result(self, run: _Run, result: dict[str, Any], ms: int) -> tuple[int, str | None]:
        import base64
        import mimetypes

        tool = run.spec.mcp.tool
        texts: list[str] = []
        content = [c for c in (result.get("content") or []) if isinstance(c, dict)]
        for index, block in enumerate(content, 1):
            kind = block.get("type")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in ("image", "audio"):
                mime = str(block.get("mimeType") or "application/octet-stream")
                name = f"{tool}-{index}{mimetypes.guess_extension(mime) or '.bin'}"
                await self._keep(run, name, mime, base64.b64decode(block.get("data") or ""))
                await self._log(run, f"Kept the {kind} it returned as {name}\n")
            elif kind == "resource":
                res = block.get("resource") or {}
                if "text" in res:
                    texts.append(str(res["text"]))
                elif res.get("blob"):
                    name = Path(str(res.get("uri") or "")).name or f"{tool}-{index}.bin"
                    await self._keep(run, name[:200], res.get("mimeType"), base64.b64decode(res["blob"]))
                    await self._log(run, f"Kept {name}\n")
            elif kind == "resource_link":
                await self._log(run, f"Link: {block.get('name') or ''} {block.get('uri') or ''}\n")
        await self._log(run, f"< {'error' if result.get('isError') else 'result'} ({ms} ms, {len(content)} content block{'s' if len(content) != 1 else ''}"
                             + (", structured" if result.get("structuredContent") is not None else "") + ")\n")
        if result.get("isError"):
            said = " ".join(t.strip() for t in texts if t.strip())[:500]
            raise Trouble(f"{tool} said it failed" + (f": {said}" if said else ""), Rendered("MCP", f"mcp://{run.spec.mcp.server}/{tool}"))
        structured = result.get("structuredContent")
        text = json.dumps(structured, ensure_ascii=False, indent=2) if structured is not None else "\n".join(texts)
        if text:
            await self._log(run, text if text.endswith("\n") else text + "\n", stream="stdout")
        return 0, None

    async def _output(self, run: _Run, paged: bool) -> tuple[int, str | None]:
        if paged and run.records_path is not None and run.records_path.exists() and await self._large(run):
            return 0, None
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


    async def _large(self, run: _Run) -> bool:
        """Too many records to print as one array: keep them as RECORDS_FILE (JSON lines) and
        show the first few. `progress.records_file` sends the parsers to the file."""
        path = run.records_path
        size = path.stat().st_size
        with path.open("rb") as f:
            count = sum(1 for line in f if line.strip())
        if size <= LARGE_BYTES and count <= LARGE_RECORDS:
            return False
        from .. import artifacts

        async def chunks():
            with path.open("rb") as f:
                while block := f.read(1024 * 1024):
                    yield block

        async with SessionLocal() as db:
            job = await db.get(m.Job, run.job_id)
            try:
                await artifacts.store(db, job, RECORDS_FILE, "application/x-ndjson", chunks())
            except artifacts.ArtifactRefused as exc:
                raise Failed(f"{count} records ({_size(size)}) are too many to print, and keeping them as {RECORDS_FILE} failed: {exc}") from exc
        run.progress["records_file"] = RECORDS_FILE
        await self._save_progress(run)
        with path.open(encoding="utf-8") as f:
            first = [json.loads(line) for _, line in zip(range(SHOWN_RECORDS), f, strict=False) if line.strip()]
        await self._log(run, f"{count} records in all ({_size(size)}): too many to print, so they're kept as {RECORDS_FILE} "
                             f"(one JSON record per line), and parsers read that. The first {len(first)} are below.\n")
        text = json.dumps(first, ensure_ascii=False, indent=2) + "\n"
        if run.texts:
            text += "\n".join(run.texts)
        await self._log(run, text if text.endswith("\n") else text + "\n", stream="stdout")
        return True


async def _ai_settings(db: Any, task: m.Task) -> dict[str, Any] | None:
    """The AI settings of the playbook step this card runs, if it says on_failure: ai."""
    if task.playbook_run_id is None or not task.playbook_step_id:
        return None
    from ..playbook_spec import AiStepConfig
    from ..services import _run_spec

    run = await db.get(m.PlaybookRun, task.playbook_run_id)
    step = next((st for st in _run_spec(run).steps if st.id == task.playbook_step_id), None) if run else None
    if step is None or step.on_failure != "ai":
        return None
    return {"config": step.ai or AiStepConfig(), "run_id": run.id, "step_id": step.id}


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
