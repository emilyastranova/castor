"""Parsers people and the AI write: declarative formats, or JavaScript in a sandbox.

A parser definition (YAML, like commands and tools) names a format and maps what the tool
printed onto one of Castor's output types:

    id: masscan-list
    name: masscan list output
    type: list<service>
    format: regex
    pattern: '^open (?P<proto>\\w+) (?P<port>\\d+) (?P<host>\\S+)'
    sample: |
      open tcp 80 10.0.0.1 1700000000

Formats: `lines` (one record per non-empty line), `regex` (one record per match; named
groups are its fields), `json` / `jsonl` (objects, found with a dot path), `csv` (rows, by
column name or index), `xml` (elements, with `@attr` and `child/path` fields) and
`javascript`: `code` defines `parse(text)` returning the records, run in a V8 isolate with no
files, network or imports, a time limit and a memory cap (js_sandbox.py). The others run on
the same guarded pieces as the built-ins: regexes with a time limit and XML through
defusedxml. `fields` maps an output field to where it comes from in a record (by default a
field of the same name), `values` sets constants. Records missing a required field (a host's
ip, a URL's url…) are left out.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Literal

import yaml
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import parsers
from .parsers import OUTPUT_TYPES, ParseError

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
MAX_RECORDS = 100_000
FORMATS = ("lines", "regex", "json", "jsonl", "csv", "xml", "javascript")

# The fields of each output type's items: kind, and whether a record needs it.
SHAPES: dict[str, dict[str, tuple[str, bool]]] = {
    "host": {"ip": ("str", True), "hostnames": ("list", False), "status": ("str", False)},
    "service": {
        "host": ("str", True), "port": ("int", True), "proto": ("str", False), "state": ("str", False),
        "name": ("str", False), "product": ("str", False), "version": ("str", False),
    },
    "url": {
        "url": ("str", True), "status": ("int", False), "length": ("int", False), "words": ("int", False),
        "lines": ("int", False), "title": ("str", False), "content_type": ("str", False), "tech": ("list", False),
    },
    "smb_host": {
        "host": ("str", True), "port": ("int", False), "name": ("str", False), "domain": ("str", False),
        "os": ("str", False), "signing": ("bool", False), "smbv1": ("bool", False),
    },
    "string": {"value": ("str", True)},
    "number": {"value": ("float", True)},
    "boolean": {"value": ("bool", True)},
}
DEFAULTS: dict[str, dict[str, Any]] = {
    "host": {"hostnames": [], "status": "up"},
    "service": {"proto": "tcp", "state": "open", "name": "", "product": "", "version": ""},
    "url": {"title": "", "content_type": "", "tech": []},
    "smb_host": {"port": 445, "name": "", "domain": "", "os": "", "signing": None, "smbv1": None},
}


def item_type(type_: str) -> str:
    return type_[5:-1] if type_.startswith("list<") else type_


class ParserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    type: str
    format: Literal["lines", "regex", "json", "jsonl", "csv", "xml", "javascript"]
    pattern: str | None = Field(None, description="regex: one record per match, named groups are fields; lines: keep matching lines")
    ignore_case: bool = False
    records: str | None = Field(None, description="json: dot path to the records; xml: element path, e.g. .//host")
    delimiter: str = ","
    header: bool = True
    code: str | None = Field(None, description="javascript: defines parse(text), returning the records (objects) or values")
    fields: dict[str, str] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    unique: bool = True
    sample: str = ""

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not ID_RE.match(value):
            raise ValueError("ids use lowercase letters, digits, dots, dashes and underscores")
        if value in parsers.PARSERS:
            raise ValueError(f"{value} is a built-in parser; pick another id")
        return value

    @field_validator("type")
    @classmethod
    def check_type(cls, value: str) -> str:
        if value not in OUTPUT_TYPES:
            raise ValueError(f"type must be one of {', '.join(OUTPUT_TYPES)}")
        return value

    @model_validator(mode="after")
    def check(self) -> ParserSpec:
        shape = SHAPES[item_type(self.type)]
        unknown = sorted((set(self.fields) | set(self.values)) - set(shape))
        if unknown:
            raise ValueError(f"{', '.join(unknown)}: not a field of {item_type(self.type)} (fields: {', '.join(shape)})")
        if self.format == "regex" and not self.pattern:
            raise ValueError("format regex needs a pattern")
        if self.format == "javascript":
            if not (self.code or "").strip():
                raise ValueError("format javascript needs code defining parse(text)")
            from .js_sandbox import JsError, check

            try:
                check(self.code or "")
            except JsError as exc:
                raise ValueError(f"code: {exc}") from exc
        elif self.code:
            raise ValueError("code is for format javascript")
        if self.pattern:
            try:
                parsers._compile(self.pattern, self.ignore_case)
            except ValueError as exc:
                raise ValueError(f"pattern: {exc}") from exc
        if self.format == "csv" and len(self.delimiter) != 1:
            raise ValueError("delimiter must be one character (\\t for tabs)")
        return self


def parse_parser_spec(text: str) -> ParserSpec:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("a parser definition is a mapping (id, name, type, format…)")  # noqa: TRY004 - callers catch ValueError
    try:
        return ParserSpec.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            problems.append(f"{where}: {msg}" if where else msg)
        raise ValueError("; ".join(problems)) from exc


# ---------- reading records ----------


def _dig(value: Any, path: str) -> Any:
    """`a.b.0.c` into nested objects and lists; None when it isn't there."""
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            i = int(part)
            value = value[i] if -len(value) <= i < len(value) else None
        else:
            return None
    return value


def _records(spec: ParserSpec, text: str) -> list[dict[str, Any]]:
    if spec.format == "javascript":
        from .js_sandbox import JsError
        from .js_sandbox import run as run_js

        try:
            found = run_js(spec.code or "", text)
        except JsError as exc:
            raise ParseError(str(exc)) from exc
        items = found if isinstance(found, list) else [] if found is None else [found]
        return [i if isinstance(i, dict) else {"value": i} for i in items]
    if spec.format == "lines":
        pattern = parsers._compile(spec.pattern, spec.ignore_case) if spec.pattern else None
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if pattern is None:
                out.append({"line": line, "value": line})
                continue
            found = parsers._search(pattern, line)
            if found:
                m = found[0]
                out.append({"line": line, "value": m.group(1) if m.re.groups else m.group(0), **{k: v for k, v in m.groupdict().items() if v is not None}})
        return out
    if spec.format == "regex":
        pattern = parsers._compile(spec.pattern or "", spec.ignore_case)
        out = []
        for m in parsers._search(pattern, text):
            rec: dict[str, Any] = {"0": m.group(0), "value": m.group(1) if m.re.groups else m.group(0)}
            rec.update({str(i): g for i, g in enumerate(m.groups(), 1) if g is not None})
            rec.update({k: v for k, v in m.groupdict().items() if v is not None})
            out.append(rec)
        return out
    if spec.format == "json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"not valid JSON: {exc}") from exc
        found = _dig(doc, spec.records) if spec.records else doc
        if found is None:
            raise ParseError(f"nothing at {spec.records!r} in the JSON")
        items = found if isinstance(found, list) else [found]
        return [i if isinstance(i, dict) else {"value": i} for i in items]
    if spec.format == "jsonl":
        out = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParseError(f"line {n} isn't JSON: {exc.msg}") from exc
            obj = _dig(obj, spec.records) if spec.records else obj
            if obj is not None:
                out.append(obj if isinstance(obj, dict) else {"value": obj})
        return out
    if spec.format == "csv":
        delimiter = "\t" if spec.delimiter in ("\\t", "tab") else spec.delimiter
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = [r for r in reader if any(c.strip() for c in r)]
        if not rows:
            return []
        if spec.header:
            names = [h.strip() for h in rows[0]]
            return [{**{str(i): c for i, c in enumerate(r)}, **dict(zip(names, r, strict=False))} for r in rows[1:]]
        return [{str(i): c for i, c in enumerate(r)} for r in rows]
    # xml
    try:
        root = ElementTree.fromstring(text.encode())
    except DefusedXmlException as exc:
        raise ParseError(f"refused unsafe XML ({type(exc).__name__})") from exc
    except ElementTree.ParseError as exc:
        raise ParseError(f"not valid XML: {exc}") from exc
    elements = root.findall(spec.records) if spec.records else [root]
    return [{"__element__": el} for el in elements]


def _xml_value(el: Any, path: str) -> Any:
    if path in (".", "text()"):
        return (el.text or "").strip()
    if path.startswith("@"):
        return el.get(path[1:])
    if "/@" in path:
        sub, attr = path.rsplit("/@", 1)
        found = el.find(sub)
        return found.get(attr) if found is not None else None
    found = el.find(path)
    return (found.text or "").strip() if found is not None else None


def _source(spec: ParserSpec, record: dict[str, Any], key: str) -> Any:
    src = spec.fields.get(key, key)
    if "__element__" in record:
        return _xml_value(record["__element__"], src)
    if src in record:
        return record[src]
    return _dig(record, src) if "." in src else None


def _coerce(value: Any, kind: str) -> Any:
    if value is None or value == "":
        return None
    if kind == "int":
        try:
            return int(float(str(value).strip()))
        except ValueError:
            return None
    if kind == "float":
        try:
            return float(str(value).strip())
        except ValueError:
            return None
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return True if text in ("true", "yes", "1", "on", "enabled", "required") else False if text in ("false", "no", "0", "off", "disabled") else None
    if kind == "list":
        if isinstance(value, list):
            return [str(v) for v in value if v not in (None, "")]
        return [v.strip() for v in re.split(r"[,;\s]+", str(value)) if v.strip()]
    return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def run(spec: ParserSpec, data: bytes) -> Any:
    """Parse `data` as the definition says; the value has the definition's type."""
    text = data.decode("utf-8", errors="replace")
    records = _records(spec, text)
    if len(records) > MAX_RECORDS:
        raise ParseError(f"more than {MAX_RECORDS} records; narrow the pattern or the path")
    kind = item_type(spec.type)
    shape = SHAPES[kind]
    out: list[Any] = []
    seen: set[str] = set()
    missing: dict[str, int] = {}
    for record in records:
        defaults = DEFAULTS.get(kind, {})
        item: dict[str, Any] = {}
        for key, (ftype, _) in shape.items():
            value = _coerce(spec.values[key], ftype) if key in spec.values else _coerce(_source(spec, record, key), ftype)
            if value is not None:
                item[key] = value
            elif key in defaults:
                item[key] = defaults[key]
        lacking = [k for k, (_, required) in shape.items() if required and item.get(k) in (None, "")]
        if lacking:
            missing[lacking[0]] = missing.get(lacking[0], 0) + 1
            continue
        value = item["value"] if kind in ("string", "number", "boolean") else item
        if spec.unique:
            key = json.dumps(value, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
        out.append(value)
    if records and not out and missing:
        field = next(iter(missing))
        raise ParseError(f"found {len(records)} record(s) but none had a {field} (check `fields`)")
    if spec.type.startswith("list<"):
        return out
    if not out:
        raise ParseError("found nothing to read")
    return out[0]


def build(spec: ParserSpec) -> parsers.Parser:
    """A registry entry for the definition (no options: everything is in the definition)."""
    return parsers.Parser(
        name=spec.id, type=spec.type, description=spec.description or spec.name,
        func=lambda data, _options, spec=spec: run(spec, data), origin="user", sample_value=spec.sample,
    )
