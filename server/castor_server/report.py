"""Timeline exports: a project's timeline (Home's "What's been happening") as a document, with
the same filters, as one self-contained HTML file or a PDF made from that HTML by WeasyPrint.

Self-contained and quiet: pictures stored in Castor (this project's attachments, uploaded
profile pictures) are embedded; anything else an entry links to stays a link, so opening an
export never reaches out to another server, and rendering the PDF never fetches anything
(`_no_fetch`). Markdown is rendered with markdown-it and sanitised with nh3, like the app's own
DOMPurify step.
"""

from __future__ import annotations

import base64
import html
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import nh3
from markdown_it import MarkdownIt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import avatars
from . import models as m
from .config import settings

LIMIT = 5000  # entries in one export
IMAGE_MAX = 10 * 1024 * 1024

KIND_LABELS = {
    "comment": "Comments", "job": "Jobs", "card": "Cards", "move": "Moves", "board": "Board", "playbook": "Playbooks",
    "library": "Library", "file": "Files", "note": "Notes", "asset": "Assets", "agent": "Agents", "admin": "Admin",
}
# Light-theme role colours (web/src/lib/m3/tokens.css, the default seed): container, on-container.
TONES = {
    "comment": ("#e1e0f9", "#444559"), "job": ("#e0e0ff", "#3d4279"), "card": ("#ffd7ef", "#5e3c53"),
    "playbook": ("#e0e0ff", "#3d4279"), "library": ("#e1e0f9", "#444559"), "note": ("#ffd7ef", "#5e3c53"),
    "asset": ("#e0e0ff", "#3d4279"),
}
NEUTRAL = ("#e4e1e9", "#46464f")
JOB_WORDS = {"succeeded": "Succeeded", "failed": "Failed", "lost": "Lost", "cancelled": "Cancelled", "running": "Running",
             "queued": "Queued", "dispatched": "Queued"}

_md = MarkdownIt("commonmark", {"breaks": True, "html": False}).enable(["table", "strikethrough"])
_TAGS = {"p", "br", "strong", "em", "del", "s", "code", "pre", "blockquote", "ul", "ol", "li", "a", "img", "h1", "h2", "h3",
         "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td", "input"}
_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "input": {"type", "checked", "disabled"}, "th": {"align"},
          "td": {"align"}, "ol": {"start"}}
_ATTACHMENT = re.compile(r"^/api/attachments/([0-9a-fA-F-]{36})/download")
_IMG = re.compile(r"<img\b[^>]*>")


@dataclass
class Filters:
    kinds: list[str] = field(default_factory=list)  # empty: everything
    mine: bool = False
    tag: str | None = None
    since: str | None = None  # YYYY-MM-DD, in tz
    until: str | None = None  # YYYY-MM-DD, inclusive
    tz: str = "UTC"
    oldest_first: bool = False

    def summary(self) -> str:
        parts = []
        if self.tag:
            parts.append(f"posts tagged #{self.tag.lstrip('#')}")
        elif self.kinds:
            parts.append(", ".join(KIND_LABELS.get(k, k) for k in self.kinds))
        else:
            parts.append("everything")
        if self.mine:
            parts.append("only what's about me")
        if self.since and self.until:
            parts.append(f"{self.since} to {self.until}")
        elif self.since:
            parts.append(f"from {self.since}")
        elif self.until:
            parts.append(f"up to {self.until}")
        if self.oldest_first:
            parts.append("oldest first")
        return " · ".join(parts)


def no_fetch():
    """WeasyPrint's URL fetcher for exports: only what's embedded in the document (data: URIs);
    never a file, the network or a redirect."""
    try:
        from weasyprint.urls import URLFetcher  # WeasyPrint 66+
    except ImportError:  # older: a function
        from weasyprint import default_url_fetcher

        def fetch(url: str, *args, **kwargs):
            if not url.startswith("data:"):
                raise ValueError("exports don't fetch anything")
            return default_url_fetcher(url, *args, **kwargs)

        return fetch
    return URLFetcher(allowed_protocols={"data"}, allow_redirects=False)


class _Images:
    """This project's attachments as data: URIs (others, and anything too large, stay links)."""

    def __init__(self, db: AsyncSession, project_id: uuid.UUID):
        self.db, self.project_id = db, project_id
        self.cache: dict[str, str | None] = {}

    async def preload(self, bodies: Iterable[str]) -> None:
        ids = set()
        for body in bodies:
            for m_ in re.finditer(r"/api/attachments/([0-9a-fA-F-]{36})/download", body or ""):
                ids.add(m_[1].lower())
        if not ids:
            return
        rows = (await self.db.execute(select(m.Attachment).where(m.Attachment.id.in_([uuid.UUID(i) for i in ids])))).scalars()
        for a in rows:
            self.cache[str(a.id)] = self._data(a)

    def _data(self, a: m.Attachment) -> str | None:
        if a.project_id != self.project_id or not (a.content_type or "").startswith("image/") or a.content_type == "image/svg+xml":
            return None
        path = settings.data_dir / a.storage_path
        try:
            if not path.is_file() or path.stat().st_size > IMAGE_MAX:
                return None
            return f"data:{a.content_type};base64,{base64.b64encode(path.read_bytes()).decode()}"
        except OSError:
            return None

    def get(self, src: str) -> str | None:
        found = _ATTACHMENT.match(src)
        return self.cache.get(found[1].lower()) if found else None


def render_markdown(text: str, images: _Images) -> str:
    clean = nh3.clean(_md.render(text or ""), tags=_TAGS, attributes=_ATTRS, url_schemes={"http", "https", "mailto"},
                      link_rel="noopener noreferrer")

    def img(match: re.Match) -> str:
        tag = match[0]
        src = html.unescape((re.search(r'\bsrc="([^"]*)"', tag) or [None, ""])[1])
        alt = html.unescape((re.search(r'\balt="([^"]*)"', tag) or [None, ""])[1])
        data = images.get(src)
        if data:
            return f'<img src="{data}" alt="{html.escape(alt)}">'
        label = html.escape(f"Image: {alt}" if alt else "Image")
        if src.startswith(("http://", "https://")):
            return f'<a class="ext-image" href="{html.escape(src)}">{label}</a>'
        return f'<span class="ext-image">{label}</span>'

    return _IMG.sub(img, clean)


def _initials(name: str) -> str:
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


def _first_lines(text: str, lines: int = 12) -> str:
    """A command's first lines (the card has the rest)."""
    parts = text.rstrip().split("\n")
    if len(parts) <= lines:
        return text.rstrip()
    rest = len(parts) - lines
    return "\n".join(parts[:lines]) + f"\n… {rest} more line{'s' if rest != 1 else ''}"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"{round(seconds)}s" if seconds < 60 else f"{int(seconds // 60)}m {round(seconds % 60)}s"


async def _faces(db: AsyncSession, usernames: set[str]) -> dict[str, str]:
    """Uploaded profile pictures as data: URIs, by username (others show initials)."""
    if not usernames:
        return {}
    out: dict[str, str] = {}
    for u in (await db.execute(select(m.User).where(m.User.username.in_(usernames)))).scalars():
        if u.avatar_kind == "upload" and u.avatar_file and "/" not in u.avatar_file:
            path = avatars.directory() / u.avatar_file
            try:
                if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
                    kind = avatars.image_type(path.read_bytes())
                    if kind:
                        out[u.username] = f"data:{kind[0]};base64,{base64.b64encode(path.read_bytes()).decode()}"
            except OSError:
                pass
    return out


def label_of(item) -> str:
    """The small label on an entry: its kind, one of them (a post is its own thing)."""
    if item.post:
        return "Post"
    return KIND_LABELS.get(item.kind, item.kind).rstrip("s") if item.kind not in ("library", "board", "admin") else KIND_LABELS[item.kind]


def _verb(item) -> str:
    if item.post:
        return "posted"
    if item.kind == "comment":
        if item.type == "comment.edited":
            return "edited a comment on"
        if item.type == "comment.deleted":
            return "deleted a comment on"
        if item.type == "comment.moved":
            what = "a post" if item.message.startswith("Moved a post") else "a comment"
            return f"moved {what} to" if item.task else f"moved {what}"
        return "commented on"
    return ""


async def timeline_html(db: AsyncSession, *, project: m.Project, items: list, filters: Filters, by: m.User,
                        truncated: bool, for_pdf: bool = False) -> str:
    """The document: a heading with what's in it, then the entries by day, newest first."""
    tz = ZoneInfo(filters.tz) if filters.tz else UTC
    images = _Images(db, project.id)
    await images.preload([i.comment["body"] for i in items if i.comment] + [i.post["body"] for i in items if i.post])
    faces = await _faces(db, {i.actor.get("username") for i in items if i.actor.get("username")})
    esc = html.escape
    now = datetime.now(tz)

    days: list[tuple[str, list]] = []
    for item in items:
        when = item.created_at.astimezone(tz)
        label = when.strftime("%A %-d %B %Y")
        if not days or days[-1][0] != label:
            days.append((label, []))
        days[-1][1].append((when, item))

    rows = []
    for label, entries in days:
        rows.append(f'<section class="day"><h2>{esc(label)}</h2><ol>')
        for when, item in entries:
            name = item.actor.get("name") or "castor"
            face = faces.get(item.actor.get("username") or "")
            bg, fg = TONES.get(item.kind, NEUTRAL)
            avatar = (f'<img class="face" src="{face}" alt="">' if face
                      else f'<span class="face" style="background:{bg};color:{fg}">{esc(_initials(name))}</span>')
            verb = _verb(item)
            card = f' <span class="card">{esc(item.task.name)}</span>' if item.task else ""
            outcome = ""
            if item.kind == "job":
                state = item.type.split(".", 1)[1] if "." in item.type else ""
                word = JOB_WORDS.get(state)
                if word:
                    outcome = f' <span class="outcome {esc(state)}">{esc(word)}</span>'
            via = ' <span class="via">via MCP</span>' if item.via == "mcp" else ""
            head = (f'<div class="head"><strong>{esc(name)}</strong>{via} {esc(verb)}{card}{outcome}'
                    f' <span class="kind" style="background:{bg};color:{fg}">{esc(label_of(item))}</span></div>')
            stamp = f'<time datetime="{item.created_at.isoformat()}">{when.strftime("%H:%M")}</time>'
            if item.post:
                body = f'<div class="bubble prose">{render_markdown(item.post["body"], images)}</div>'
            elif item.kind == "comment" and item.comment:
                body = f'<div class="bubble prose">{render_markdown(item.comment["body"], images)}</div>'
            elif item.kind == "job" and item.job:
                j = item.job
                facts = [x for x in (f"exit {j['exit_code']}" if j.get("exit_code") not in (None, 0) else "",
                                     _duration(j.get("seconds")), f"on {j['agent']}" if j.get("agent") else "") if x]
                body = (f'<div class="job">{"<p class=facts>" + esc(" · ".join(facts)) + "</p>" if facts else ""}'
                        f'<pre>{esc(_first_lines(j.get("command") or ""))}</pre></div>')
            else:
                body = f'<p class="message">{esc(item.message)}</p>'
            rows.append(f'<li class="entry">{avatar}<div class="what">{stamp}{head}{body}</div></li>')
        rows.append("</ol></section>")

    count = len(items)
    title = f"{project.codename} · Timeline"
    meta = (f"{esc(filters.summary())} · {count} {'entry' if count == 1 else 'entries'}"
            f"{' (the newest ' + str(LIMIT) + ')' if truncated else ''}")
    empty = '<p class="empty">Nothing on the timeline for these choices.</p>' if not items else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{_CSS.replace("__FOOTER__", esc(title).replace('"', "'"))}</style></head>
<body{' class="pdf"' if for_pdf else ''}>
<header class="top">
  <p class="codename">{esc(project.codename)}</p>
  <h1>{esc(project.name)}: timeline</h1>
  <p class="meta">{meta}</p>
  <p class="meta">Exported by {esc(by.display_name)} on {esc(now.strftime("%-d %B %Y at %H:%M"))} ({esc(filters.tz or "UTC")}) from Castor</p>
</header>
<main>{empty}{"".join(rows)}</main>
</body></html>"""


_CSS = """
@page { size: A4; margin: 16mm 15mm 18mm;
  @bottom-left { content: "__FOOTER__"; font: 7.5pt "Roboto", "DejaVu Sans", sans-serif; color: #777680; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 7.5pt "Roboto", "DejaVu Sans", sans-serif; color: #777680; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fbf8ff; color: #1b1b21; font: 14px/1.5 "Roboto Flex", "Roboto", "DejaVu Sans", system-ui, sans-serif; }
body.pdf { background: #fff; font-size: 9.5pt; }
.top, main { max-width: 860px; margin: 0 auto; padding: 0 20px; }
body.pdf .top, body.pdf main { max-width: none; padding: 0; }
.top { padding-top: 32px; padding-bottom: 12px; border-bottom: 1px solid #c7c5d0; margin-bottom: 8px; }
body.pdf .top { padding-top: 0; }
.codename { display: inline-block; margin: 0 0 6px; padding: 1px 8px; border-radius: 6px; background: #e4e1e9; color: #46464f;
  font: 500 11px/1.6 "Roboto Mono", "DejaVu Sans Mono", monospace; }
h1 { margin: 0 0 6px; font-size: 24px; font-weight: 500; line-height: 1.25; }
body.pdf h1 { font-size: 17pt; }
.meta { margin: 0; color: #46464f; font-size: 12.5px; }
body.pdf .meta { font-size: 8.5pt; }
.day h2 { margin: 22px 0 8px; font-size: 13px; font-weight: 500; color: #46464f; text-transform: none; letter-spacing: .01em; }
.day ol { list-style: none; margin: 0; padding: 0; }
.entry { position: relative; padding: 0 0 14px 43px; min-height: 45px; }
.entry .head, .entry > .face { break-after: avoid; }
.message, .job .facts { break-before: avoid; }
.day h2 { break-after: avoid; }
.entry::before { content: ""; position: absolute; left: 15px; top: 35px; bottom: 0; width: 1px; background: #c7c5d0; }
.entry:last-child::before { display: none; }
.face { position: absolute; left: 0; top: 0; width: 31px; height: 31px; border-radius: 50%; display: block;
  text-align: center; line-height: 31px; font-size: 11px; font-weight: 600; object-fit: cover; }
.what { min-width: 0; }
.what > time { float: right; margin-left: 8px; color: #46464f; font-size: 12.5px; font-variant-numeric: tabular-nums; }
body.pdf .what > time { font-size: 8.5pt; }
.head { color: #46464f; font-size: 12.5px; line-height: 1.6; }
body.pdf .head { font-size: 8.5pt; }
.head strong { color: #1b1b21; font-weight: 600; }
.card { color: #545a92; font-weight: 500; }
.via { font-size: 11px; }
.kind { display: inline-block; padding: 0 7px; border-radius: 9999px; font-size: 10.5px; font-weight: 500; line-height: 1.6; }
.outcome { font-weight: 600; }
.outcome.failed, .outcome.lost { color: #ba1a1a; }
.outcome.succeeded { color: #545a92; }
.message { margin: 2px 0 0; }
.bubble { margin-top: 6px; padding: 9px 13px; border-radius: 12px; background: #efedf4; overflow-wrap: anywhere; }
.job { margin-top: 6px; padding: 7px 12px; border: 1px solid #c7c5d0; border-radius: 12px; }
.job .facts { margin: 0 0 4px; color: #46464f; font-size: 12px; }
.job pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 11.5px/1.5 "Roboto Mono", "DejaVu Sans Mono", monospace; color: #46464f; }
.prose > :first-child { margin-top: 0; } .prose > :last-child { margin-bottom: 0; }
.prose p, .prose ul, .prose ol, .prose pre, .prose blockquote, .prose table { margin: 0 0 .6em; }
.prose ul, .prose ol { padding-left: 1.4em; }
.prose a { color: #545a92; }
.prose code { font: .88em "Roboto Mono", "DejaVu Sans Mono", monospace; background: #e4e1e9; padding: .05em .35em; border-radius: 4px; }
.prose pre { background: #e4e1e9; padding: 8px 11px; border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
.prose pre code { background: none; padding: 0; }
.prose blockquote { border-left: 3px solid #c7c5d0; padding-left: .75em; color: #46464f; }
.prose img { display: block; max-width: 100%; max-height: 420px; margin: .4em 0; border-radius: 8px; border: 1px solid #c7c5d0; }
body.pdf .prose img { max-height: 110mm; }
.prose table { border-collapse: collapse; } .prose th, .prose td { border: 1px solid #c7c5d0; padding: 2px 8px; }
.ext-image { font-style: italic; }
.empty { margin: 40px 0; text-align: center; color: #46464f; }
@media print { body { background: #fff; } }
"""
