"""Fill an HTTP action's request template (docs/ACTIONS.md) in one pass.

Every value is escaped for where it lands: percent-encoded in the URL, URL-encoded in query
and form fields, put in as a JSON value in a `json` body, refused in a header if it has a line
break, and escaped for the content type in a text body. Arguments and variable fields are
replaced in the same pass, so text in an argument that looks like `{{ creds.token }}` stays
text: a value is never read again for placeholders."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape as xml_escape

from .command_render import ArgumentError
from .command_spec import ArgSpec, CommandSpec

# {{ arg }} or {{ slot.field }}
ANY_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:\.([a-z][a-z0-9_]*))?\s*\}\}")

# (slot, field) → the value, or None when the set has none.
VarLookup = Callable[[str, str], str | None]


class RenderError(ValueError):
    """The request can't be made as filled in (a header with a line break, a URL that
    isn't http(s), ...)."""


@dataclass
class Rendered:
    method: str
    url: str
    params: list[tuple[str, str]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    timeout_s: int = 60

    def with_params(self, extra: dict[str, Any]) -> Rendered:
        names = set(extra)
        params = [(k, v) for k, v in self.params if k not in names] + [(k, str(v)) for k, v in extra.items()]
        return Rendered(self.method, self.url, params, dict(self.headers), self.content, self.timeout_s)


def display_lookup(slot: str, field: str) -> str:
    """Variables as written, for a card's preview before any set is chosen."""
    return f"{{{{ {slot}.{field} }}}}"


class _Filler:
    def __init__(self, spec: CommandSpec, args: dict[str, Any], lookup: VarLookup, preview: bool = False) -> None:
        self.by_name: dict[str, ArgSpec] = {a.name: a for a in spec.args}
        self.args = args
        self.lookup = lookup
        self.preview = preview  # variables stay as written, unescaped

    def value(self, m: re.Match[str]) -> Any:
        name, field_name = m.group(1), m.group(2)
        if field_name is not None:
            return self.lookup(name, field_name)
        return self.args.get(name)

    @staticmethod
    def as_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return ",".join(str(v) for v in value)
        return str(value)

    def text(self, template: str, escape: Callable[[str], str] = lambda s: s) -> str:
        def sub(m: re.Match[str]) -> str:
            text = self.as_text(self.value(m))
            return text if self.preview and m.group(2) is not None else escape(text)

        return ANY_PLACEHOLDER.sub(sub, template)

    def whole(self, template: str) -> tuple[bool, Any]:
        """A template that is exactly one placeholder: (True, its typed value)."""
        m = ANY_PLACEHOLDER.fullmatch(template.strip())
        return (True, self.value(m)) if m else (False, None)

    def json_value(self, template: Any) -> Any:
        if isinstance(template, str):
            whole, value = self.whole(template)
            return value if whole else self.text(template)
        if isinstance(template, dict):
            # A field that is only a placeholder with no value is left out (an optional
            # argument the card didn't fill), rather than sent as null.
            out = {}
            for key, value in template.items():
                if isinstance(value, str):
                    whole, typed = self.whole(value)
                    if whole and typed is None:
                        continue
                out[key] = self.json_value(value)
            return out
        if isinstance(template, list):
            return [self.json_value(v) for v in template]
        return template

    def pairs(self, template: dict[str, Any]) -> list[tuple[str, str]]:
        """Query or form fields: a list repeats the field, an empty value leaves it out."""
        out: list[tuple[str, str]] = []
        for key, raw in template.items():
            if not isinstance(raw, str):
                out.append((key, self.as_text(raw)))
                continue
            whole, value = self.whole(raw)
            if whole:
                if value is None or value == "" or value == []:
                    continue
                for item in value if isinstance(value, list) else [value]:
                    out.append((key, self.as_text(item)))
                continue
            out.append((key, self.text(raw)))
        return out


def _header(name: str, value: str) -> str:
    if "\r" in value or "\n" in value:
        raise RenderError(f"the {name} header would contain a line break")
    return value


def _body_escape(content_type: str) -> Callable[[str], str]:
    ct = content_type.lower()
    if "json" in ct:
        return lambda s: json.dumps(s, ensure_ascii=False)[1:-1]
    if "xml" in ct or "html" in ct:
        return lambda s: xml_escape(s, {'"': "&quot;", "'": "&apos;"})
    if "x-www-form-urlencoded" in ct:
        return lambda s: quote(s, safe="")
    return lambda s: s


def render_url(fill: _Filler, template: str, *, check: bool) -> str:
    """A placeholder that starts the URL goes in as it is (a base URL); every other value is
    percent-encoded, so it stays inside its path segment or query value."""
    template = template.strip()
    head = ANY_PLACEHOLDER.match(template)
    if head:
        base = fill.as_text(fill.value(head)).strip()
        if check and not re.match(r"^https?://[^/?#\s]+", base):
            name = f"{head.group(1)}.{head.group(2)}" if head.group(2) else head.group(1)
            raise RenderError(f"{name} must be an http:// or https:// address, not {base[:80]!r}")
        rest = fill.text(template[head.end():], lambda s: quote(s, safe=""))
        if base.endswith("/") and rest.startswith("/"):
            rest = rest[1:]
        url = base + rest
    else:
        url = fill.text(template, lambda s: quote(s, safe=""))
    if check:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RenderError(f"the URL must start with http:// or https://, not {url[:80]!r}")
    return url


def render_request(spec: CommandSpec, args: dict[str, Any], lookup: VarLookup, *, check: bool = True) -> Rendered:
    """The request to send. `args` are resolved values (command_render.resolve_args);
    `check` False renders a preview (variables may still be placeholders)."""
    req = spec.request
    if req is None:
        raise RenderError("this definition has no request")
    fill = _Filler(spec, args, lookup, preview=not check)
    out = Rendered(method=req.method, url=render_url(fill, req.url, check=check), timeout_s=req.timeout_s)
    out.params = fill.pairs(req.query)
    for name, template in req.headers.items():
        value = fill.text(template)
        if value.strip():
            out.headers[name] = _header(name, value)
    if req.auth is not None:
        if req.auth.bearer is not None:
            token = fill.text(req.auth.bearer)
            out.headers["Authorization"] = _header("Authorization", f"Bearer {token}")
        else:
            user, password = (fill.text(t) for t in req.auth.basic or ["", ""])
            if ":" in user:
                raise RenderError("a basic auth user can't contain ':'")
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            out.headers["Authorization"] = f"Basic {token}"
    if req.json_body is not None:
        out.content = json.dumps(fill.json_value(req.json_body), ensure_ascii=False).encode()
        out.headers.setdefault("Content-Type", "application/json")
    elif req.form is not None:
        from urllib.parse import urlencode

        out.content = urlencode(fill.pairs(req.form)).encode()
        out.headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif req.body is not None:
        content_type = req.content_type or "text/plain; charset=utf-8"
        out.content = fill.text(req.body, _body_escape(content_type)).encode()
        out.headers.setdefault("Content-Type", content_type)
    return out


def preview_line(spec: CommandSpec, args: dict[str, Any]) -> str:
    """The card's one-line summary (its `command`): method and URL with arguments filled in
    and variables as written."""
    try:
        rendered = render_request(spec, args, display_lookup, check=False)
    except (RenderError, ArgumentError):
        return f"{spec.request.method} {spec.request.url}" if spec.request else ""
    from urllib.parse import urlencode

    query = urlencode(rendered.params)
    return f"{rendered.method} {rendered.url}" + (("&" if "?" in rendered.url else "?") + query if query else "")


def request_preview(spec: CommandSpec, args: dict[str, Any]) -> dict[str, Any]:
    """The request for the card's "Will send" preview: method, URL with its query, headers
    (Authorization as ••••) and the body as text."""
    from urllib.parse import urlencode

    rendered = render_request(spec, args, display_lookup, check=False)
    query = urlencode(rendered.params)
    url = rendered.url + (("&" if "?" in rendered.url else "?") + query if query else "")
    headers = {k: ("••••" if k.lower() == "authorization" else v) for k, v in rendered.headers.items()}
    body = rendered.content.decode("utf-8", "replace") if rendered.content else None
    return {"method": rendered.method, "url": url, "headers": headers, "body": body}
