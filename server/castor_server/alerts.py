"""Chat and webhook alerts (Phase 3k): channels admins set up in Settings → Alerts, sent
when a card's run or a playbook run finishes, fails, runs long, or needs a decision.

A channel is a preset (Slack, Google Chat, ntfy…) or a custom webhook. Every kind comes down
to one request template: method, URL, headers, content type and a body with `{{ var }}`
placeholders (`render` escapes values for the content type, so a title can't break the
JSON around it). Secret fields are stored through the vault and never leave the server.
Sending happens off the event loop with a timeout; each attempt is an `alert_deliveries` row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from . import vault
from .config import settings

log = logging.getLogger(__name__)
_background: set[asyncio.Task[Any]] = set()

EVENTS: dict[str, str] = {
    "job.failed": "A card's run fails",
    "job.succeeded": "A card's run succeeds",
    "run.failed": "A playbook run fails",
    "run.succeeded": "A playbook run succeeds",
    "decision": "A playbook step needs a decision",
    "approval": "A trigger waits for approval",
    "long": "Something runs longer than…",
}
EMOJI = {"info": "ℹ️", "success": "✅", "warning": "✋", "error": "❌"}
VARIABLES: dict[str, str] = {
    "title": "One line: what happened",
    "body": "Details (may be empty)",
    "message": "Emoji, title, details and link, ready to post",
    "link": "Absolute link into Castor (needs CASTOR_PUBLIC_URL)",
    "event": "Event id, e.g. run.failed",
    "event_label": "Event in words",
    "severity": "info, success, warning or error",
    "emoji": "An emoji for the severity",
    "status": "succeeded, failed, running…",
    "project": "Project name",
    "project_code": "Project codename",
    "card": "Card title",
    "playbook": "Playbook name (runs)",
    "duration": "How long it ran, e.g. 12m 5s",
    "agent": "Agent that ran it",
    "time": "When, ISO 8601",
}


class AlertError(Exception):
    pass


# ---------- presets ----------


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text | url | password | textarea | select | number
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""
    options: list[str] = field(default_factory=list)
    default: str = ""


@dataclass
class Request:
    method: str
    url: str
    content_type: str  # json | form | text
    body: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Preset:
    kind: str
    label: str
    icon: str
    fields: list[Field]
    help: str = ""

    def request(self, f: dict[str, str]) -> Request:  # pragma: no cover - overridden
        raise NotImplementedError


def _url_field(placeholder: str, help: str = "") -> Field:
    return Field("url", "Webhook URL", "password", secret=True, placeholder=placeholder, help=help)


SLACK_BODY = '{"text": "{{ message }}"}'
TEAMS_BODY = """{
  "type": "message",
  "attachments": [{
    "contentType": "application/vnd.microsoft.card.adaptive",
    "content": {
      "type": "AdaptiveCard", "version": "1.4",
      "body": [
        {"type": "TextBlock", "text": "{{ emoji }} {{ title }}", "weight": "Bolder", "wrap": true},
        {"type": "TextBlock", "text": "{{ body }}", "wrap": true},
        {"type": "TextBlock", "text": "{{ link }}", "wrap": true, "isSubtle": true}
      ]
    }
  }]
}"""
NTFY_BODY = '{"topic": "{{ topic }}", "title": "{{ title }}", "message": "{{ body_or_title }}", "priority": {{ ntfy_priority }}, "tags": ["{{ ntfy_tag }}"], "click": "{{ link }}"}'
GOTIFY_BODY = '{"title": "{{ title }}", "message": "{{ body_or_title }}\\n{{ link }}", "priority": {{ gotify_priority }}}'
GENERIC_BODY = """{
  "event": "{{ event }}",
  "severity": "{{ severity }}",
  "title": "{{ title }}",
  "body": "{{ body }}",
  "link": "{{ link }}",
  "project": "{{ project_code }}",
  "status": "{{ status }}"
}"""


class _Simple(Preset):
    """A preset that's a webhook URL and a fixed JSON body."""

    def __init__(self, kind, label, icon, body, placeholder, help=""):
        super().__init__(kind, label, icon, [_url_field(placeholder, help)], help)
        self.body = body

    def request(self, f):
        return Request("POST", f["url"], "json", self.body)


class _Ntfy(Preset):
    def request(self, f):
        headers = {"Authorization": f"Bearer {f['token']}"} if f.get("token") else {}
        # JSON publishing goes to the server root with the topic in the body.
        return Request("POST", f["server"].rstrip("/"), "json", NTFY_BODY.replace("{{ topic }}", _json_text(f["topic"])), headers)


class _Gotify(Preset):
    def request(self, f):
        return Request("POST", f["server"].rstrip("/") + "/message", "json", GOTIFY_BODY, {"X-Gotify-Key": f["token"]})


class _Telegram(Preset):
    def request(self, f):
        body = json.dumps({"chat_id": f["chat_id"], "text": "@@MESSAGE@@", "disable_web_page_preview": True}).replace("@@MESSAGE@@", "{{ message }}")
        return Request("POST", f"https://api.telegram.org/bot{f['token']}/sendMessage", "json", body)


class _Pushover(Preset):
    def request(self, f):
        body = urlencode({"token": f["token"], "user": f["user"]}) + "&title={{ title }}&message={{ body_or_title }}&url={{ link }}&priority={{ pushover_priority }}"
        return Request("POST", "https://api.pushover.net/1/messages.json", "form", body)


class _Webhook(Preset):
    def request(self, f):
        headers: dict[str, str] = {}
        for line in (f.get("headers") or "").splitlines():
            if ":" in line:
                name, value = line.split(":", 1)
                if name.strip():
                    headers[name.strip()] = value.strip()
        return Request(f.get("method") or "POST", f["url"], f.get("content_type") or "json", f.get("body") or GENERIC_BODY, headers)


PRESETS: dict[str, Preset] = {p.kind: p for p in [
    _Simple("slack", "Slack", "tag", SLACK_BODY, "https://hooks.slack.com/services/…", "An incoming webhook from a Slack app."),
    _Simple("google_chat", "Google Chat", "chat", SLACK_BODY, "https://chat.googleapis.com/v1/spaces/…", "Space settings → Apps & integrations → Webhooks."),
    _Simple("discord", "Discord", "forum", '{"content": "{{ message }}"}', "https://discord.com/api/webhooks/…", "Channel settings → Integrations → Webhooks."),
    _Simple("teams", "Microsoft Teams", "groups", TEAMS_BODY, "https://….webhook.office.com/… or a Workflows URL", "An incoming webhook or a Workflows \"post to a channel when a webhook request is received\" URL."),
    _Simple("mattermost", "Mattermost", "forum", SLACK_BODY, "https://mattermost.example.com/hooks/…", "Integrations → Incoming webhooks."),
    _Ntfy("ntfy", "ntfy", "notifications", [
        Field("server", "Server", "url", default="https://ntfy.sh", placeholder="https://ntfy.sh"),
        Field("topic", "Topic", placeholder="castor-alerts"),
        Field("token", "Access token", "password", secret=True, required=False, help="For protected topics."),
    ], "Phone and desktop push through ntfy."),
    _Gotify("gotify", "Gotify", "notifications", [
        Field("server", "Server", "url", placeholder="https://gotify.example.com"),
        Field("token", "Application token", "password", secret=True),
    ]),
    _Telegram("telegram", "Telegram", "send", [
        Field("token", "Bot token", "password", secret=True, placeholder="123456:ABC…"),
        Field("chat_id", "Chat id", placeholder="-1001234567890", help="The group, channel or user the bot posts to."),
    ]),
    _Pushover("pushover", "Pushover", "phone_iphone", [
        Field("user", "User or group key", "password", secret=True),
        Field("token", "Application token", "password", secret=True),
    ]),
    _Webhook("webhook", "Webhook", "webhook", [
        Field("method", "Method", "select", options=["POST", "PUT", "PATCH", "GET"], default="POST"),
        Field("url", "URL", "password", secret=True, placeholder="https://example.com/hooks/castor", help="Placeholders work here too, URL-encoded."),
        Field("content_type", "Body", "select", options=["json", "form", "text"], default="json",
              help="json: values are JSON-escaped; form: URL-encoded; text: as they are."),
        Field("headers", "Headers", "textarea", secret=True, required=False, placeholder="Authorization: Bearer …\nX-Source: castor", help="One per line, Name: value."),
        Field("body", "Body template", "textarea", required=False, default=GENERIC_BODY),
    ], "Any HTTP endpoint, with your own method, headers and body."),
]}


def presets_out() -> list[dict[str, Any]]:
    """For the UI: each kind's form, and the request it makes (to start a custom webhook from)."""
    out = []
    for p in PRESETS.values():
        sample = {f.name: f.default or f"<{f.name}>" for f in p.fields}
        try:
            req = p.request(sample) if p.kind != "webhook" else None
        except Exception:  # noqa: BLE001
            req = None
        out.append({
            "kind": p.kind, "label": p.label, "icon": p.icon, "help": p.help,
            "fields": [f.__dict__ for f in p.fields],
            "template": {"method": req.method, "content_type": req.content_type, "body": req.body, "headers": "\n".join(f"{k}: {v}" for k, v in req.headers.items() if "<" not in v)} if req else None,
        })
    return out


# ---------- channel fields ----------


def split_fields(kind: str, given: dict[str, Any], old: m.AlertChannel | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Validate a channel's fields: (plain, vault-encrypted secrets). A blank secret keeps
    the stored one (the UI never sees it)."""
    preset = PRESETS.get(kind)
    if preset is None:
        raise AlertError(f"Unknown kind {kind}")
    plain: dict[str, str] = {}
    secret: dict[str, str] = {}
    for f in preset.fields:
        value = str(given.get(f.name) or "").strip() if f.type != "textarea" else str(given.get(f.name) or "")
        if f.secret:
            if value:
                secret[f.name] = vault.encrypt(value.encode())
            elif old is not None and f.name in (old.secrets or {}):
                secret[f.name] = old.secrets[f.name]
            elif f.required:
                raise AlertError(f"{f.label}: required")
            continue
        value = value or f.default
        if f.required and not value:
            raise AlertError(f"{f.label}: required")
        if f.type == "select" and value and f.options and value not in f.options:
            raise AlertError(f"{f.label}: one of {', '.join(f.options)}")
        if f.type == "url" and value and urlparse(value).scheme not in ("http", "https"):
            raise AlertError(f"{f.label}: an http(s) URL")
        plain[f.name] = value
    return plain, secret


def channel_values(ch: m.AlertChannel) -> dict[str, str]:
    values = dict(ch.fields or {})
    for name, token in (ch.secrets or {}).items():
        values[name] = vault.decrypt(token).decode()
    return values


# ---------- rendering ----------


PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _json_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def render(template: str, values: dict[str, Any], mode: str) -> str:
    """Fill `{{ name }}` placeholders. json: escaped for inside a JSON string; form/url:
    URL-encoded; text: as is. Unknown names become empty."""
    def sub(mt: re.Match[str]) -> str:
        v = values.get(mt.group(1), "")
        v = "" if v is None else str(v)
        if mode == "json":
            return _json_text(v)
        if mode in ("form", "url"):
            return quote(v, safe="")
        return v

    return PLACEHOLDER.sub(sub, template)


def build(ch: m.AlertChannel, values: dict[str, Any]) -> urllib.request.Request:
    """The HTTP request a channel makes for one alert."""
    preset = PRESETS.get(ch.kind)
    if preset is None:
        raise AlertError(f"Unknown kind {ch.kind}")
    req = preset.request(channel_values(ch))
    body = render(req.body, values, req.content_type)
    if req.content_type == "json" and body.strip():
        try:
            json.loads(body)
        except ValueError as exc:
            raise AlertError(f"The body isn't valid JSON once filled in ({exc.msg} at character {exc.pos})") from exc
    url = render(req.url, values, "url")
    if urlparse(url).scheme not in ("http", "https"):
        raise AlertError("The URL must start with http:// or https://")
    headers = {"User-Agent": "castor", **{k: render(v, values, "text") for k, v in req.headers.items()}}
    headers.setdefault("Content-Type", {"json": "application/json", "form": "application/x-www-form-urlencoded", "text": "text/plain; charset=utf-8"}[req.content_type])
    data = body.encode() if req.method != "GET" else None
    return urllib.request.Request(url, data=data, headers=headers, method=req.method)


def values_for(event: str, *, title: str, body: str = "", link: str = "", severity: str = "info", status: str = "",
               project: m.Project | None = None, card: str = "", playbook: str = "", started: datetime | None = None,
               finished: datetime | None = None, agent: str = "") -> dict[str, Any]:
    full = settings.public_url.rstrip("/") + link if link and settings.public_url else link
    if not full.startswith("http"):
        full = ""
    emoji = EMOJI.get(severity, "")
    message = f"{emoji} {title}".strip() + (f"\n{body}" if body else "") + (f"\n{full}" if full else "")
    duration = ""
    if started:
        secs = int(((finished or datetime.now(UTC)) - started).total_seconds())
        duration = f"{secs // 3600}h {secs % 3600 // 60}m" if secs >= 3600 else f"{secs // 60}m {secs % 60}s" if secs >= 60 else f"{secs}s"
    return {
        "title": title, "body": body, "body_or_title": body or title, "message": message[:1900], "link": full, "link_path": link,
        "event": event, "event_label": EVENTS.get(event, event), "severity": severity, "emoji": emoji, "status": status,
        "project": project.name if project else "", "project_code": project.codename if project else "",
        "card": card, "playbook": playbook, "duration": duration, "agent": agent, "time": datetime.now(UTC).isoformat(),
        "ntfy_priority": 4 if severity in ("warning", "error") else 3,
        "ntfy_tag": {"warning": "raised_hand", "error": "x", "success": "white_check_mark"}.get(severity, "information_source"),
        "gotify_priority": 8 if severity in ("warning", "error") else 5,
        "pushover_priority": 1 if severity == "error" else 0,
    }


def sample_values() -> dict[str, Any]:
    return values_for("run.failed", title="Test from Castor: “Internal sweep” failed", body="This is a test alert; nothing failed.",
                      link="/", severity="error", status="failed", card="Internal sweep", playbook="Internal sweep",
                      started=datetime.now(UTC) - timedelta(minutes=12, seconds=5), agent="lab-agent-01")


# ---------- sending ----------


def _post(request: urllib.request.Request) -> tuple[bool, int | None, str]:
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            resp.read(2048)
            return True, resp.status, ""
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace").strip()
        return False, exc.code, f"HTTP {exc.code}" + (f": {detail}" if detail else "")
    except Exception as exc:  # noqa: BLE001 - a dead endpoint must never affect a run
        return False, None, str(getattr(exc, "reason", exc))[:300]


async def send_now(channel: m.AlertChannel, values: dict[str, Any]) -> tuple[bool, int | None, str]:
    """Send one alert and wait for the answer (the Test button)."""
    try:
        request = build(channel, values)
    except (AlertError, vault.VaultError) as exc:
        return False, None, str(exc)
    return await asyncio.to_thread(_post, request)


async def _deliver(channel_id: uuid.UUID, event: str, subject: str, values: dict[str, Any], delivery_id: uuid.UUID | None) -> None:
    from .db import SessionLocal

    async with SessionLocal() as db:
        channel = await db.get(m.AlertChannel, channel_id)
        if channel is None:
            return
        ok, status, error = await send_now(channel, values)
        if not ok:
            log.warning("alert to %s failed: %s", channel.name, error)
        row = await db.get(m.AlertDelivery, delivery_id) if delivery_id else None
        if row is None:
            row = m.AlertDelivery(channel_id=channel_id, event=event, subject=subject, title=values.get("title", "")[:300])
            db.add(row)
        row.ok, row.status, row.error = ok, status, error
        await db.commit()


def _queue(channel_ids: list[uuid.UUID], event: str, subject: str, values: dict[str, Any],
           deliveries: dict[uuid.UUID, uuid.UUID] | None = None) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for cid in channel_ids:
        task = loop.create_task(_deliver(cid, event, subject, values, (deliveries or {}).get(cid)))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def drain() -> None:
    """Wait for queued alerts (tests, shutdown)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


# ---------- who gets what ----------


END_EVENTS = {"job.failed", "job.succeeded", "run.failed", "run.succeeded", "card.done"}


def wants(rules: dict[str, Any] | None, event: str, *, run: bool) -> bool:
    """Whether a card's alert settings send this event to its channels."""
    if not rules or not rules.get("channels"):
        return False
    on = rules.get("on", "failure")
    kind = "run" if run else "job"
    if event == f"{kind}.failed":
        return on in ("finish", "failure")
    if event in (f"{kind}.succeeded", "card.done"):
        return on == "finish"
    if event == "decision":
        return run and rules.get("decisions", True)
    return False


async def emit(db: AsyncSession, event: str, values: dict[str, Any], *, card: m.Task | None = None, run: bool = False,
               subject: str = "", actor_id: uuid.UUID | None = None) -> None:
    """Send an event to the channels that take it for everything, and to the card's own; and
    tell the people who asked to hear when the card finishes (not whoever finished it)."""
    channels = (await db.execute(select(m.AlertChannel).where(m.AlertChannel.enabled.is_(True)))).scalars().all()
    ids = {c.id for c in channels if event in (c.events or [])}
    if card is not None and wants(card.alerts, event, run=run):
        enabled = {c.id for c in channels}
        ids |= {uuid.UUID(str(c)) for c in card.alerts["channels"] if _is_uuid(c) and uuid.UUID(str(c)) in enabled}
    if ids:
        _queue(sorted(ids), event, subject, values)
    people = [uuid.UUID(str(p)) for p in ((card.alerts or {}).get("people") or []) if _is_uuid(p)] if card is not None else []
    people = [p for p in people if p != actor_id]
    if people and event in END_EVENTS:
        from . import notifications

        await notifications.notify(
            db, kind="task.finished", severity=values["severity"], title=values["title"], body=values["body"],
            link=values["link_path"], project_id=card.project_id, subject=f"task:{card.id}:finished",
            user_ids=people, webhooks=False,
        )


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


# ---------- hooks ----------


async def job_finished(db: AsyncSession, task: m.Task, job: m.Job, agent: str = "") -> None:
    """A card's run ended (steps of a playbook run alert through the run instead)."""
    if task.playbook_run_id is not None or job.status == m.JobStatus.cancelled:
        return
    ok = job.status == m.JobStatus.succeeded
    project = await db.get(m.Project, task.project_id)
    what = "succeeded" if ok else "was lost" if job.status == m.JobStatus.lost else "failed"
    detail = "" if ok else (job.error or (f"Exit code {job.exit_code}" if job.exit_code is not None else ""))
    values = values_for(f"job.{'succeeded' if ok else 'failed'}", title=f"“{task.title}” {what}", body=detail[:500],
                        link=f"/projects/{task.project_id}?task={task.id}", severity="success" if ok else "error",
                        status=job.status.value, project=project, card=task.title, started=job.started_at,
                        finished=job.finished_at, agent=agent)
    await emit(db, values["event"], values, card=task, subject=f"job:{job.id}")


async def card_done(db: AsyncSession, task: m.Task, actor: m.User | None) -> None:
    """A manual card moved to Done: "finished" for people and channels that asked."""
    if not task.alerts or task.kind != m.TaskKind.manual or task.playbook_run_id is not None:
        return
    if (await db.execute(select(m.PlaybookRun.id).where(m.PlaybookRun.card_id == task.id))).first():
        return  # a run card finishes with its run
    project = await db.get(m.Project, task.project_id)
    who = f" by {actor.display_name}" if actor else ""
    values = values_for("card.done", title=f"“{task.title}” is done", body=f"Moved to Done{who}.",
                        link=f"/projects/{task.project_id}?task={task.id}", severity="success", status="done",
                        project=project, card=task.title)
    await emit(db, "card.done", values, card=task, subject=f"task:{task.id}:done", actor_id=actor.id if actor else None)


async def run_finished(db: AsyncSession, run: m.PlaybookRun, ok: bool, detail: str, link: str) -> None:
    card = await db.get(m.Task, run.card_id) if run.card_id else None
    project = await db.get(m.Project, run.project_id)
    values = values_for(f"run.{'succeeded' if ok else 'failed'}", title=f"Playbook “{run.playbook_name}” " + ("succeeded" if ok else "failed"),
                        body=detail, link=link, severity="success" if ok else "error", status="succeeded" if ok else "failed",
                        project=project, card=card.title if card else "", playbook=run.playbook_name,
                        started=run.created_at, finished=run.finished_at)
    await emit(db, values["event"], values, card=card, run=True, subject=f"run:{run.id}")


async def decision(db: AsyncSession, run: m.PlaybookRun, step_name: str, reason: str, link: str) -> None:
    card = await db.get(m.Task, run.card_id) if run.card_id else None
    project = await db.get(m.Project, run.project_id)
    values = values_for("decision", title=f"{run.playbook_name}: “{step_name}” needs a decision", body=reason, link=link,
                        severity="warning", status="attention", project=project, card=card.title if card else "",
                        playbook=run.playbook_name, started=run.created_at)
    await emit(db, "decision", values, card=card, run=True, subject=f"run:{run.id}:decision:{step_name}")


async def approval(db: AsyncSession, project_id: uuid.UUID, title: str, body: str, link: str) -> None:
    project = await db.get(m.Project, project_id)
    values = values_for("approval", title=title, body=body, link=link, severity="warning", status="waiting", project=project)
    await emit(db, "approval", values)


# ---------- running long ----------


async def check_long_running(db: AsyncSession, now: datetime | None = None) -> int:
    """Alert once per channel for each card run or playbook run past its limit (the
    channel's own for everything, or the card's). Returns how many were queued."""
    now = now or datetime.now(UTC)
    channels = {c.id: c for c in (await db.execute(select(m.AlertChannel).where(m.AlertChannel.enabled.is_(True)))).scalars()}
    if not channels:
        return 0
    everything = [(c.id, c.long_minutes) for c in channels.values() if "long" in (c.events or []) and c.long_minutes]
    queued = 0
    pending: list[tuple[list[uuid.UUID], str, dict[str, Any], dict[uuid.UUID, uuid.UUID]]] = []

    async def consider(subject: str, started: datetime | None, card: m.Task | None, make) -> None:
        nonlocal queued
        if started is None:
            return
        minutes = (now - started).total_seconds() / 60
        due = [cid for cid, limit in everything if minutes >= limit]
        rules = card.alerts if card is not None else None
        if rules and rules.get("long_minutes") and minutes >= rules["long_minutes"]:
            due += [uuid.UUID(str(c)) for c in rules.get("channels", []) if _is_uuid(c) and uuid.UUID(str(c)) in channels]
        if not due:
            return
        sent = set((await db.execute(select(m.AlertDelivery.channel_id).where(m.AlertDelivery.subject == subject))).scalars())
        due = sorted(set(due) - sent)
        if due:
            values = await make()
            # Recorded (and committed) before sending, so the next sweep can't send them again.
            rows = {cid: m.AlertDelivery(channel_id=cid, event="long", subject=subject, title=values["title"][:300], error="sending")
                    for cid in due}
            db.add_all(rows.values())
            await db.flush()
            pending.append((due, subject, values, {cid: r.id for cid, r in rows.items()}))
            queued += len(due)

    jobs = (await db.execute(select(m.Job, m.Task).join(m.Task, m.Task.id == m.Job.task_id)
                             .where(m.Job.status == m.JobStatus.running, m.Task.playbook_run_id.is_(None)))).all()
    for job, task in jobs:
        async def make(job=job, task=task):
            project = await db.get(m.Project, task.project_id)
            return values_for("long", title=f"“{task.title}” is still running", body=f"Started {values_started(job.started_at)} ago.",
                              link=f"/projects/{task.project_id}?task={task.id}", severity="warning", status="running",
                              project=project, card=task.title, started=job.started_at)
        await consider(f"long:job:{job.id}", job.started_at, task, make)
    runs = (await db.execute(select(m.PlaybookRun).where(m.PlaybookRun.status == m.PlaybookRunStatus.running))).scalars().all()
    for run in runs:
        card = await db.get(m.Task, run.card_id) if run.card_id else None

        async def make(run=run, card=card):
            project = await db.get(m.Project, run.project_id)
            return values_for("long", title=f"Playbook “{run.playbook_name}” is still running", body=f"Started {values_started(run.created_at)} ago.",
                              link=f"/projects/{run.project_id}?task={run.card_id}" if run.card_id else "", severity="warning",
                              status="running", project=project, card=card.title if card else "", playbook=run.playbook_name,
                              started=run.created_at)
        await consider(f"long:run:{run.id}", run.created_at, card, make)
    if queued:
        await db.commit()
        for due, subject, values, rows in pending:
            _queue(due, "long", subject, values, rows)
    return queued


def values_started(started: datetime | None) -> str:
    return values_for("long", title="", started=started)["duration"] if started else "a while"


async def sweep_forever(interval: float = 60.0) -> None:
    from .db import SessionLocal

    while True:
        await asyncio.sleep(interval)
        try:
            async with SessionLocal() as db:
                await check_long_running(db)
        except Exception:
            log.exception("long-running alert sweep failed")
