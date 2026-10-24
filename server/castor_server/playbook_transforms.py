"""Transform steps: reshape one step's output into another step's input, on the server.

A transform is a playbook step that runs no command. It takes one or more upstream values
(lists of parsed records such as hosts or services), applies a fixed pipeline of operations,
and publishes the result as its own output. Operations are data, never code: there is no
expression language, templates only substitute item fields, and regexes run through the
`regex` module with a timeout like the parsers do.

    transform:
      input: "{{ steps.svc_scan.outputs.services }}"
      ops:
        - filter: { field: name, in: [http, https] }
        - format: "http://{{ host }}:{{ port }}/"
        - unique: true
      output: { name: urls }
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import regex
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .parsers import REGEX_TIMEOUT_S

FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# {{ field }} in a format template; {{ value }} is the item itself when it is not a record.
FORMAT_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# Most a transform will emit; protects the run row and the UI from runaway inputs.
MAX_ITEMS = 10_000


class TransformError(ValueError):
    pass


def _check_field(value: str | None) -> str | None:
    if value is not None and not FIELD_PATTERN.match(value):
        raise ValueError(f"{value!r} is not a field name (letters, digits, underscores)")
    return value


@lru_cache(maxsize=256)
def _compile(pattern: str, ignore_case: bool) -> regex.Pattern[str]:
    try:
        return regex.compile(pattern, regex.IGNORECASE if ignore_case else 0)
    except regex.error as exc:
        raise ValueError(f"not a valid regular expression: {exc}") from exc


def _field(item: Any, name: str | None) -> Any:
    """A record's field, or the item itself for plain values (`value` or no field)."""
    if name is None or (name == "value" and not isinstance(item, dict)):
        return item
    return item.get(name) if isinstance(item, dict) else None


class Op(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def describe(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def apply(self, items: list[Any]) -> list[Any]:  # pragma: no cover - overridden
        raise NotImplementedError


FILTER_TESTS = ("equals", "not_equals", "in_", "not_in", "contains", "matches", "exists")


class FilterOp(Op):
    """Keep items whose field passes exactly one test."""

    field: str | None = None
    equals: Any = None
    not_equals: Any = None
    in_: list[Any] | None = Field(None, alias="in")
    not_in: list[Any] | None = None
    contains: str | None = None
    matches: str | None = None
    exists: bool | None = None
    ignore_case: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("field")
    @classmethod
    def check_field(cls, value: str | None) -> str | None:
        return _check_field(value)

    @model_validator(mode="after")
    def one_test(self) -> FilterOp:
        given = [t for t in FILTER_TESTS if t in self.model_fields_set]
        if len(given) != 1:
            names = ", ".join(t.rstrip("_") for t in FILTER_TESTS)
            raise ValueError(f"filter needs exactly one test ({names})")
        if self.matches is not None:
            _compile(self.matches, self.ignore_case)
        return self

    @property
    def test(self) -> str:
        return next(t for t in FILTER_TESTS if t in self.model_fields_set)

    def _norm(self, value: Any) -> Any:
        return value.lower() if self.ignore_case and isinstance(value, str) else value

    def keep(self, item: Any) -> bool:
        value = _field(item, self.field)
        test = self.test
        if test == "exists":
            return (value not in (None, "", [])) == self.exists
        if test == "equals":
            return self._norm(value) == self._norm(self.equals)
        if test == "not_equals":
            return self._norm(value) != self._norm(self.not_equals)
        if test == "in_":
            return self._norm(value) in [self._norm(v) for v in self.in_ or []]
        if test == "not_in":
            return self._norm(value) not in [self._norm(v) for v in self.not_in or []]
        if value is None:
            return False
        text = str(value)
        if test == "contains":
            return self._norm(self.contains) in self._norm(text)
        try:
            pattern = _compile(self.matches or "", self.ignore_case)
            return pattern.search(text, timeout=REGEX_TIMEOUT_S) is not None
        except TimeoutError as exc:
            raise TransformError(
                f"the pattern took longer than {REGEX_TIMEOUT_S:g}s; simplify it"
            ) from exc

    def apply(self, items: list[Any]) -> list[Any]:
        return [i for i in items if self.keep(i)]

    def describe(self) -> str:
        test = self.test
        value = getattr(self, test)
        symbol = {
            "equals": "=",
            "not_equals": "≠",
            "in_": "∈",
            "not_in": "∉",
            "contains": "contains",
            "matches": "~",
            "exists": "exists" if self.exists else "missing",
        }[test]
        subject = self.field or "value"
        if test == "exists":
            return f"{subject} {symbol}"
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        return f"{subject} {symbol} {value}"


class PluckOp(Op):
    """Replace each record with one of its fields (records without it are dropped)."""

    field: str
    @field_validator("field")
    @classmethod
    def check_field(cls, value: str | None) -> str | None:
        return _check_field(value)

    def apply(self, items: list[Any]) -> list[Any]:
        values = [_field(i, self.field) for i in items]
        return [v for v in values if v not in (None, "")]

    def describe(self) -> str:
        return f"pluck {self.field}"


class FormatOp(Op):
    """Render each item through a template: `{{ field }}` for records, `{{ value }}` otherwise."""

    template: str

    @field_validator("template")
    @classmethod
    def has_placeholder(cls, value: str) -> str:
        if not FORMAT_PLACEHOLDER.search(value):
            raise ValueError("format template needs at least one {{ field }}")
        return value

    def apply(self, items: list[Any]) -> list[Any]:
        def render(item: Any) -> str:
            def sub(m: re.Match[str]) -> str:
                value = _field(item, m.group(1))
                return "" if value is None else str(value)

            return FORMAT_PLACEHOLDER.sub(sub, self.template)

        return [render(i) for i in items]

    def describe(self) -> str:
        return f"format {self.template}"


class UniqueOp(Op):
    """Drop repeats, keeping the first; by a field when given."""

    field: str | None = None
    @field_validator("field")
    @classmethod
    def check_field(cls, value: str | None) -> str | None:
        return _check_field(value)

    def apply(self, items: list[Any]) -> list[Any]:
        seen: set[str] = set()
        out = []
        for item in items:
            key = repr(_field(item, self.field) if self.field else item)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    def describe(self) -> str:
        return f"unique by {self.field}" if self.field else "unique"


class SortOp(Op):
    field: str | None = None
    desc: bool = False
    @field_validator("field")
    @classmethod
    def check_field(cls, value: str | None) -> str | None:
        return _check_field(value)

    def apply(self, items: list[Any]) -> list[Any]:
        def key(item: Any) -> tuple[int, Any]:
            value = _field(item, self.field)
            # Numbers before text, missing values last; never compare across kinds.
            if isinstance(value, bool) or value is None:
                return (2, str(value))
            if isinstance(value, (int, float)):
                return (0, value)
            return (1, str(value))

        return sorted(items, key=key, reverse=self.desc)

    def describe(self) -> str:
        return f"sort {self.field or 'value'}{' desc' if self.desc else ''}"


class LimitOp(Op):
    count: int = Field(ge=0)

    def apply(self, items: list[Any]) -> list[Any]:
        return items[: self.count]

    def describe(self) -> str:
        return f"first {self.count}"


OPS: dict[str, type[Op]] = {
    "filter": FilterOp,
    "pluck": PluckOp,
    "format": FormatOp,
    "unique": UniqueOp,
    "sort": SortOp,
    "limit": LimitOp,
}
# Ops whose result is always a list of strings, whatever went in.
STRING_OPS = (PluckOp, FormatOp)


def parse_op(raw: Any) -> Op:
    """Parse one op entry; raises ValueError with a short message."""
    try:
        return _parse_op(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"])
        message = err["msg"].removeprefix("Value error, ")
        name = next(iter(raw)) if isinstance(raw, dict) and raw else raw
        raise ValueError(f"{name}: {where + ': ' if where else ''}{message}") from None


def _parse_op(raw: Any) -> Op:
    """Parse one `- name: argument` entry. Short forms: `pluck: ip`, `format: "..."`,
    `unique: true` / `unique: host`, `sort: port` / `sort: -port`, `limit: 10`."""
    if isinstance(raw, str):
        raw = {raw: True}
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError("each op is a mapping with one key, e.g. `- pluck: ip`")
    (name, arg), = raw.items()
    cls = OPS.get(name)
    if cls is None:
        raise ValueError(f"unknown op {name!r} (known: {', '.join(OPS)})")
    if isinstance(arg, dict):
        return cls.model_validate(arg)
    if cls is FilterOp:
        raise ValueError("filter takes a mapping, e.g. `filter: { field: port, in: [80, 443] }`")
    if cls is PluckOp:
        return PluckOp(field=str(arg))
    if cls is FormatOp:
        return FormatOp(template=str(arg))
    if cls is UniqueOp:
        return UniqueOp() if arg is True or arg is None else UniqueOp(field=str(arg))
    if cls is SortOp:
        if arg is True or arg is None:
            return SortOp()
        text = str(arg)
        return SortOp(field=text.lstrip("-"), desc=text.startswith("-"))
    if isinstance(arg, bool):
        raise ValueError("limit takes a number")  # noqa: TRY004 - callers handle ValueError
    return LimitOp(count=arg)


class TransformOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "items"
    # Declared result type; inferred from the ops and the input when left out.
    type: str | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _check_field(value) or value


class TransformSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # One `{{ steps.<id>.outputs.<name> }}` (or `{{ inputs.<name> }}`) reference, or a list
    # of them whose values are concatenated.
    input: str | list[str]
    ops: list[dict[str, Any] | str] = Field(default_factory=list)
    output: TransformOutput = Field(default_factory=TransformOutput)

    @field_validator("ops")
    @classmethod
    def check_ops(cls, value: list[Any]) -> list[Any]:
        for index, raw in enumerate(value):
            try:
                parse_op(raw)
            except ValueError as exc:
                raise ValueError(f"op {index + 1}: {str(exc).removeprefix('Value error, ')}") from exc
        return value

    @property
    def inputs(self) -> list[str]:
        return [self.input] if isinstance(self.input, str) else list(self.input)

    def parsed_ops(self) -> list[Op]:
        return [parse_op(raw) for raw in self.ops]

    def result_type(self, input_type: str | None) -> str:
        if self.output.type:
            return self.output.type
        if any(isinstance(op, STRING_OPS) for op in self.parsed_ops()):
            return "list<string>"
        if input_type and input_type.startswith("list<"):
            return input_type
        if input_type:
            return f"list<{input_type}>"
        return "list<string>"


def as_items(value: Any) -> list[Any]:
    """Upstream values as a list: lists as-is, None as empty, comma text split, else [value]."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def run_transform(spec: TransformSpec, values: list[Any]) -> list[Any]:
    """Apply the ops to the concatenated input values. Raises TransformError."""
    items: list[Any] = []
    for value in values:
        items.extend(as_items(value))
    for op in spec.parsed_ops():
        items = op.apply(items)
    if len(items) > MAX_ITEMS:
        raise TransformError(f"produced {len(items)} items; the limit is {MAX_ITEMS}")
    return items
