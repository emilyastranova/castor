"""The parts of a library definition with `kind: http` (docs/ACTIONS.md): the request, how to
page through results, batching a list argument, what counts as success, retries and a rate
limit. Placeholders are the command format's: `{{ arg }}` and `{{ slot.field }}`."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
# A dot path into a JSON document, as parser definitions use: results, data.items, links.next
DOT_PATH = re.compile(r"^[A-Za-z0-9_$@-]+(\.[A-Za-z0-9_$@-]+)*$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def _dot_path(value: str | None) -> str | None:
    if value is not None and not DOT_PATH.match(value):
        raise ValueError(f"{value!r} isn't a dot path like results or data.items")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class HttpAuth(Strict):
    # Two templates: user and password. Sent as an Authorization: Basic header.
    basic: list[str] | None = None
    # A template for the token. Sent as Authorization: Bearer <token>.
    bearer: str | None = None

    @model_validator(mode="after")
    def one(self) -> HttpAuth:
        if (self.basic is None) == (self.bearer is None):
            raise ValueError("auth takes basic: [user, password] or bearer: token")
        if self.basic is not None and len(self.basic) != 2:
            raise ValueError("auth.basic is [user, password]")
        return self


class HttpRequest(Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    # A placeholder may start the URL ({{ api.base_url }}/issues): that value goes in as it is
    # and must be an http(s) address. Anywhere else a value is percent-encoded.
    url: str
    # Query parameters (values URL-encoded; a list argument repeats the parameter).
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth: HttpAuth | None = None
    # One body at most: JSON (values put in as JSON values), a form, or text.
    json_body: Any = Field(None, alias="json")
    form: dict[str, Any] | None = None
    body: str | None = None
    content_type: str | None = None
    timeout_s: int = Field(60, ge=1, le=600)
    # Keep the response body as a file on the card (an artifact) with this name.
    save_as: str | None = None

    @field_validator("method", mode="before")
    @classmethod
    def upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("headers")
    @classmethod
    def check_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not HEADER_NAME.match(name):
                raise ValueError(f"{name!r} isn't a header name")
        return {k: str(v) for k, v in value.items()}

    @field_validator("save_as")
    @classmethod
    def check_save_as(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "/" in value or "\\" in value or value.startswith(".")):
            raise ValueError("save_as is a plain file name, like report.pdf")
        return value

    @model_validator(mode="after")
    def one_body(self) -> HttpRequest:
        bodies = [b for b in (self.json_body, self.form, self.body) if b is not None]
        if len(bodies) > 1:
            raise ValueError("give json, form or body, not more than one")
        if bodies and self.method in ("GET", "HEAD"):
            raise ValueError(f"a {self.method} request has no body")
        if self.content_type and self.body is None:
            raise ValueError("content_type goes with body (json and form set their own)")
        if not self.url.strip():
            raise ValueError("url is required")
        return self


class Cursor(Strict):
    # Where the next cursor is in each response, and the parameter that sends it back.
    from_: str = Field(alias="from")
    param: str

    @field_validator("from_")
    @classmethod
    def path(cls, value: str) -> str | None:
        return _dot_path(value)


class Stop(Strict):
    max_pages: int = Field(50, ge=1, le=10000)
    max_items: int = Field(10000, ge=1, le=1_000_000)


class Paginate(Strict):
    style: Literal["cursor", "link", "offset", "page"]
    # Where each page's records are; none: the body itself is the list.
    items: str | None = None
    cursor: Cursor | None = None
    # link: a dot path to the next page's URL in the body; none: the Link header (rel="next").
    next: str | None = None
    # offset and page: the parameter, the page-size parameter and size, and the first page.
    param: str | None = None
    size_param: str | None = None
    size: int | None = Field(None, ge=1, le=10000)
    start: int = 1
    stop: Stop = Field(default_factory=Stop)

    @field_validator("items", "next")
    @classmethod
    def paths(cls, value: str | None) -> str | None:
        return _dot_path(value)

    @model_validator(mode="after")
    def fits_style(self) -> Paginate:
        if self.style == "cursor" and self.cursor is None:
            raise ValueError("paginate style cursor needs cursor: {from: <dot path>, param: <name>}")
        if self.style in ("offset", "page") and not self.param:
            raise ValueError(f"paginate style {self.style} needs param (the parameter to send)")
        if self.style == "offset" and not self.size:
            raise ValueError("paginate style offset needs size (records per page)")
        return self


class Batch(Strict):
    arg: str  # a list argument
    size: int = Field(ge=1, le=100_000)


class Expect(Strict):
    # Status codes that count as success; none: any 2xx.
    status: list[int] | None = None

    @field_validator("status")
    @classmethod
    def codes(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (not value or any(not 100 <= c <= 599 for c in value)):
            raise ValueError("status codes are 100 to 599")
        return value

    def ok(self, status: int) -> bool:
        return status in self.status if self.status else 200 <= status < 300


class Retry(Strict):
    # Tries after the first for 5xx, timeouts and dropped connections (429/503 with
    # Retry-After always wait as told, within the job's time).
    attempts: int = Field(3, ge=0, le=10)
    backoff_s: float = Field(2, ge=0.1, le=600)
    max_backoff_s: float = Field(60, ge=0.1, le=3600)


# "60/minute", "5/second", "1000/hour", "10000/day", optionally ", 2 at once"
RATE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour|day)s?\s*(?:,\s*(\d+)\s*at once\s*)?$", re.IGNORECASE)
PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


class RateLimit(BaseModel):
    count: int
    period_s: int
    concurrent: int | None = None

    def __str__(self) -> str:
        unit = next(k for k, v in PERIODS.items() if v == self.period_s)
        return f"{self.count}/{unit}" + (f", {self.concurrent} at once" if self.concurrent else "")


def parse_rate(text: str | None) -> RateLimit | None:
    """`60/minute` (optionally `, 2 at once`) → RateLimit; empty → None. Raises ValueError."""
    if text is None or not str(text).strip():
        return None
    match = RATE.match(str(text))
    if not match:
        raise ValueError("a rate limit looks like 60/minute, 5/second or 1000/day (optionally , 2 at once)")
    count, unit, concurrent = int(match.group(1)), match.group(2).lower(), match.group(3)
    if count < 1:
        raise ValueError("a rate limit allows at least 1 request")
    if concurrent is not None and int(concurrent) < 1:
        raise ValueError("at once is at least 1")
    return RateLimit(count=count, period_s=PERIODS[unit], concurrent=int(concurrent) if concurrent else None)


def check_rate(text: str | None) -> str | None:
    """A rate limit as stored (normalised text), or None."""
    rate = parse_rate(text)
    return str(rate) if rate else None
