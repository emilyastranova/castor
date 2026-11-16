"""Parsers turn a tool's output file (or stdout) into a typed value (Phase 2b).

Each parser takes raw bytes plus its validated options and returns JSON-serialisable data
of one of the fixed output types below, so later playbook steps can check what they are
wired to. Parsers are pure functions and run in a worker thread; raise ParseError for input
they can't understand. Options are a pydantic model per parser (set per output in a command
definition, and on the Parsers page); every parser ships a sample input for that page.
"""

from __future__ import annotations

import json
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import regex
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Output types. Objects: host = {ip, hostnames[], status}; service = {host, port, proto,
# state, name, product, version}; url = {url, status, length, words, lines}.
OUTPUT_TYPES = (
    "string",
    "number",
    "boolean",
    "list<string>",
    "host",
    "list<host>",
    "service",
    "list<service>",
    "url",
    "list<url>",
    "smb_host",
    "list<smb_host>",
    # Anything else: objects with whatever fields the parser gives them (a table of rows).
    "object",
    "list<object>",
)

SAMPLES_DIR = Path(__file__).parent / "parser_samples"

# User-supplied patterns run with a time limit, so a catastrophic pattern can't wedge a
# worker thread (the `regex` module supports timeouts; the stdlib `re` does not).
REGEX_TIMEOUT_S = 2.0


class ParseError(ValueError):
    pass


class OptionsError(ValueError):
    """Invalid parser options; `errors` maps option name to message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _compile(pattern: str, ignore_case: bool = False) -> regex.Pattern[str]:
    try:
        return regex.compile(pattern, regex.MULTILINE | (regex.IGNORECASE if ignore_case else 0))
    except regex.error as exc:
        raise ValueError(f"not a valid regular expression: {exc}") from exc


@dataclass(frozen=True)
class Parser:
    name: str
    type: str
    description: str
    func: Callable[[bytes, Any], Any]
    options: type[Options] = Options
    sample: str | None = None  # file in parser_samples/
    sample_options: dict[str, Any] = field(default_factory=dict)
    # Parsers people (or the AI) wrote are declarative (parser_spec.py) and carry their
    # sample inline; built-ins are Python here with a sample file.
    origin: str = "builtin"
    sample_value: str | None = None

    @property
    def is_list(self) -> bool:
        return self.type.startswith("list<")

    def configure(self, raw: dict[str, Any] | None) -> Options:
        try:
            return self.options.model_validate(raw or {})
        except ValidationError as exc:
            errors: dict[str, str] = {}
            for err in exc.errors():
                key = ".".join(str(p) for p in err["loc"]) or "options"
                message = err["msg"].removeprefix("Value error, ")
                if err["type"] == "extra_forbidden":
                    message = f"{self.name} has no option {key!r}"
                errors.setdefault(key, message)
            raise OptionsError(errors) from exc

    def run(self, data: bytes, options: Options) -> Any:
        return self.func(data, options)

    def sample_text(self) -> str:
        if self.sample_value is not None:
            return self.sample_value
        return (SAMPLES_DIR / self.sample).read_text() if self.sample else ""


PARSERS: dict[str, Parser] = {}
# Parsers from the library (parser_library.py keeps this in step with the database). One
# server process serves the API, so a dict is enough.
USER: dict[str, Parser] = {}


def parser(
    name: str,
    type_: str,
    description: str,
    options: type[Options] = Options,
    sample: str | None = None,
    sample_options: dict[str, Any] | None = None,
):
    def register(func: Callable[[bytes, Any], Any]) -> Callable[[bytes, Any], Any]:
        PARSERS[name] = Parser(
            name, type_, description, func, options, sample, sample_options or {}
        )
        return func

    return register


def get(name: str) -> Parser | None:
    return PARSERS.get(name) or USER.get(name)


def all_parsers() -> list[Parser]:
    """Built-ins first, then the library's, by name."""
    return [*PARSERS.values(), *sorted(USER.values(), key=lambda p: p.name)]


def option_fields(model: type[Options]) -> list[dict[str, Any]]:
    """Describe an options model for the UI: name, kind, description, default, example."""
    out = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        args = typing.get_args(annotation)
        if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
            args = tuple(a for a in args if a is not type(None))
            annotation = args[0] if len(args) == 1 else str
        kind = {bool: "boolean", int: "number", str: "string"}.get(annotation, "string")
        if typing.get_origin(annotation) is list:
            kind = "list"
        out.append(
            {
                "name": name,
                "kind": kind,
                "description": info.description or "",
                "required": info.is_required(),
                "default": None if info.is_required() else info.get_default(call_default_factory=True),
                "example": (info.examples or [None])[0],
            }
        )
    return out


# ---------- nmap ----------


def _nmap_root(data: bytes) -> Element:
    try:
        root = ElementTree.fromstring(data)
    except DefusedXmlException as exc:
        raise ParseError(f"refused unsafe XML ({type(exc).__name__})") from exc
    except ElementTree.ParseError as exc:
        # nmap killed mid-scan leaves the XML unterminated; say so rather than "line 40".
        raise ParseError(f"not valid nmap XML (was the scan interrupted?): {exc}") from exc
    if root.tag != "nmaprun":
        raise ParseError(f"expected an <nmaprun> document, got <{root.tag}>")
    # nmap records a fatal error here and writes no hosts; an empty list would read as
    # "nothing found", so report it instead.
    finished = root.find("runstats/finished")
    if finished is not None and finished.get("exit") == "error" and root.find("host") is None:
        raise ParseError(f"nmap stopped with an error: {finished.get('errormsg') or 'unknown'}")
    return root


def _host_ip(host: Element) -> str | None:
    addresses = host.findall("address")
    for family in ("ipv4", "ipv6"):
        for a in addresses:
            if a.get("addrtype") == family and a.get("addr"):
                return a.get("addr")
    return addresses[0].get("addr") if addresses else None


def _hostnames(host: Element) -> list[str]:
    names = [h.get("name", "") for h in host.findall("hostnames/hostname")]
    return [*dict.fromkeys(n for n in names if n)]


class NmapHostsOptions(Options):
    include_down: bool = Field(True, description="Keep hosts nmap reported as down")


@parser(
    "nmap.hosts",
    "list<host>",
    "Hosts from nmap XML (-oX), with status and hostnames",
    NmapHostsOptions,
    sample="nmap-ping-sweep.xml",
)
def nmap_hosts(data: bytes, options: NmapHostsOptions | None = None) -> list[dict[str, Any]]:
    options = options or NmapHostsOptions()
    hosts = []
    for host in _nmap_root(data).findall("host"):
        ip = _host_ip(host)
        if ip is None:
            continue
        status = host.find("status")
        state = status.get("state", "unknown") if status is not None else "unknown"
        if state != "up" and not options.include_down:
            continue
        hosts.append({"ip": ip, "hostnames": _hostnames(host), "status": state})
    return hosts


class NmapServicesOptions(Options):
    include_closed: bool = Field(False, description="Also keep closed and filtered ports")


@parser(
    "nmap.services",
    "list<service>",
    "Open ports from nmap XML (-oX), with version info",
    NmapServicesOptions,
    sample="nmap-service-scan.xml",
)
def nmap_services(
    data: bytes, options: NmapServicesOptions | None = None
) -> list[dict[str, Any]]:
    options = options or NmapServicesOptions()
    services = []
    for host in _nmap_root(data).findall("host"):
        ip = _host_ip(host)
        if ip is None:
            continue
        for port in host.findall("ports/port"):
            state = port.find("state")
            state_name = state.get("state", "") if state is not None else ""
            # "open|filtered" (UDP) counts as open; closed and filtered are usually noise.
            if not state_name.startswith("open") and not options.include_closed:
                continue
            service = port.find("service")
            svc = service.attrib if service is not None else {}
            services.append(
                {
                    "host": ip,
                    "port": int(port.get("portid", "0")),
                    "proto": port.get("protocol", "tcp"),
                    "state": state_name,
                    "name": svc.get("name", ""),
                    "product": svc.get("product", ""),
                    "version": " ".join(v for v in (svc.get("version"), svc.get("extrainfo")) if v),
                }
            )
    return services


# ---------- ffuf ----------


class FfufOptions(Options):
    status: list[int] = Field(
        default_factory=list,
        description="Only keep these HTTP status codes (empty keeps everything)",
        examples=[[200, 301, 302]],
    )


@parser(
    "ffuf.results",
    "list<url>",
    "Matched URLs from ffuf JSON output (-of json)",
    FfufOptions,
    sample="ffuf.json",
)
def ffuf_results(data: bytes, options: FfufOptions | None = None) -> list[dict[str, Any]]:
    options = options or FfufOptions()
    try:
        doc = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParseError(f"not valid ffuf JSON: {exc}") from exc
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list):
        raise ParseError("expected ffuf JSON with a results list")
    return [
        {
            "url": r.get("url", ""),
            "status": r.get("status"),
            "length": r.get("length"),
            "words": r.get("words"),
            "lines": r.get("lines"),
        }
        for r in results
        if isinstance(r, dict)
        and r.get("url")
        and (not options.status or r.get("status") in options.status)
    ]


class HttpxOptions(Options):
    status: list[int] = Field(default_factory=list, description="Only keep these status codes (empty: all)")


@parser(
    "httpx.results",
    "list<url>",
    "Live web servers from httpx JSON lines (-json), with status, size, title and technologies",
    HttpxOptions,
    sample="httpx.jsonl",
)
def httpx_results(data: bytes, options: HttpxOptions | None = None) -> list[dict[str, Any]]:
    options = options or HttpxOptions()
    out = []
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ParseError(f"line {n} isn't httpx JSON: {exc}") from exc
        if not isinstance(r, dict) or not r.get("url") or r.get("failed"):
            continue
        if options.status and r.get("status_code") not in options.status:
            continue
        out.append({
            "url": r["url"], "status": r.get("status_code"), "length": r.get("content_length"),
            "words": r.get("words"), "lines": r.get("lines"), "title": r.get("title") or "",
            "content_type": r.get("content_type") or "", "tech": r.get("tech") or [], "webserver": r.get("webserver") or "",
            "final_url": r.get("final_url") or "", "favicon": str(r.get("favicon") or ""),
        })
    return out


# ---------- gowitness ----------


def _default_port(url: str) -> str:
    """gowitness writes every port ("https://app.example.com:443"); httpx doesn't. One URL, one asset."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    if (parts.scheme, port) in (("http", 80), ("https", 443)):
        host = parts.hostname or ""
        host = f"[{host}]" if ":" in host else host
        netloc = (f"{parts.username}@" if parts.username else "") + host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def _gw_time(value: Any) -> str:
    text = str(value or "")
    return "" if not text or text.startswith("0001-01-01") else text


class GowitnessOptions(Options):
    status: list[int] = Field(default_factory=list, description="Only keep these status codes (empty: all)")
    with_screenshot: bool = Field(False, description="Only keep pages it took a screenshot of")


@parser(
    "gowitness.results",
    "list<url>",
    "Web pages from gowitness JSON lines (--write-jsonl): status, title, technologies, TLS certificate, headers and the screenshot",
    GowitnessOptions,
    sample="gowitness.jsonl",
)
def gowitness_results(data: bytes, options: GowitnessOptions | None = None) -> list[dict[str, Any]]:
    """One row per page, with `screenshot` (the file gowitness saved, matched to the job's artifacts),
    `headers` and `certificate`, which Castor keeps on the URL asset as evidence."""
    options = options or GowitnessOptions()
    out = []
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ParseError(f"line {n} isn't gowitness JSON: {exc}") from exc
        if not isinstance(r, dict) or not r.get("url"):
            continue
        status = r.get("response_code") or None
        if options.status and status not in options.status:
            continue
        if options.with_screenshot and not r.get("file_name"):
            continue
        headers = {str(h.get("key")): str(h.get("value") or "") for h in r.get("headers") or [] if isinstance(h, dict) and h.get("key")}
        lower = {k.lower(): v for k, v in headers.items()}
        tls = r.get("tls") if isinstance(r.get("tls"), dict) else {}
        cert = {}
        if tls.get("subject_name"):
            cert = {"subject": tls.get("subject_name"), "issuer": tls.get("issuer") or "",
                    "sans": [s.get("value") for s in tls.get("san_list") or [] if isinstance(s, dict) and s.get("value")],
                    "valid_from": _gw_time(tls.get("valid_from")), "valid_to": _gw_time(tls.get("valid_to")),
                    "protocol": tls.get("protocol") or "", "cipher": tls.get("cipher") or ""}
        final = _default_port(str(r.get("final_url") or ""))
        if final.rstrip("/") == _default_port(str(r["url"])).rstrip("/"):
            final = ""
        out.append({
            "url": _default_port(str(r["url"])), "status": status, "title": r.get("title") or "",
            "length": r.get("content_length") or None,
            "final_url": final,
            "tech": [t.get("value") for t in r.get("technologies") or [] if isinstance(t, dict) and t.get("value")],
            "webserver": lower.get("server", ""), "content_type": lower.get("content-type", "").split(";")[0].strip(),
            "cert_subject": cert.get("subject", ""), "cert_issuer": cert.get("issuer", ""), "cert_expires": cert.get("valid_to", ""),
            "screenshot": r.get("file_name") or "", "headers": headers, "certificate": cert,
            "failed": bool(r.get("failed")), "failed_reason": r.get("failed_reason") or "",
        })
    return out


# ---------- netexec ----------

ANSI_RE = regex.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
NETEXEC_SMB_PREFIX = regex.compile(
    r"SMB\s+(?P<host>[0-9a-fA-F.:]+)\s+(?P<port>\d+)\s+(?P<hdr_host>\S+)\s+\[\*\]\s+(?P<rest>.*)"
)
NETEXEC_NAME_RE = regex.compile(r"\(name:([^)]*)\)")
NETEXEC_DOMAIN_RE = regex.compile(r"\(domain:([^)]*)\)")
NETEXEC_SIGNING_RE = regex.compile(r"\(signing:(True|False)\)", regex.IGNORECASE)
NETEXEC_SMBV1_RE = regex.compile(r"\(SMBv1:(True|False)\)", regex.IGNORECASE)


class NetExecSmbOptions(Options):
    signing_only: bool = Field(
        False, description="Only keep hosts where SMB signing is disabled"
    )
    unique: bool = Field(True, description="Drop repeated hosts (keeps the first)")


@parser(
    "netexec.smb",
    "list<smb_host>",
    "SMB host status and SMB signing requirement from NetExec stdout",
    NetExecSmbOptions,
    sample="netexec-smb.txt",
)
def netexec_smb(data: bytes, options: NetExecSmbOptions | None = None) -> list[dict[str, Any]]:
    options = options or NetExecSmbOptions()
    text = data.decode("utf-8", errors="replace")
    results = []
    seen = set()
    for line in text.splitlines():
        cleaned = ANSI_RE.sub("", line).strip()
        m = NETEXEC_SMB_PREFIX.search(cleaned)
        if not m:
            continue
        host = m.group("host")
        port = int(m.group("port"))
        hdr_host = m.group("hdr_host")
        rest = m.group("rest")

        name_m = NETEXEC_NAME_RE.search(rest)
        domain_m = NETEXEC_DOMAIN_RE.search(rest)
        signing_m = NETEXEC_SIGNING_RE.search(rest)
        smbv1_m = NETEXEC_SMBV1_RE.search(rest)

        os_str = rest.split("(")[0].strip()
        is_signing = signing_m.group(1).lower() == "true" if signing_m else False
        is_smbv1 = smbv1_m.group(1).lower() == "true" if smbv1_m else False

        if options.signing_only and is_signing:
            continue

        key = (host, port)
        if options.unique and key in seen:
            continue
        seen.add(key)

        results.append(
            {
                "host": host,
                "port": port,
                "name": name_m.group(1) if name_m else (hdr_host if hdr_host != "NONE" else ""),
                "domain": domain_m.group(1) if domain_m else "",
                "os": os_str,
                "signing": is_signing,
                "smbv1": is_smbv1,
            }
        )
    return results


# ---------- generic ----------


def _search(pattern: regex.Pattern[str], text: str):
    try:
        return list(pattern.finditer(text, timeout=REGEX_TIMEOUT_S))
    except TimeoutError as exc:
        raise ParseError(
            f"the pattern took longer than {REGEX_TIMEOUT_S:g}s on this input; simplify it"
        ) from exc


class LinesOptions(Options):
    match: str | None = Field(
        None,
        description="Only keep lines matching this regular expression",
        examples=[r"\.acme\.example$"],
    )
    ignore_case: bool = Field(False, description="Match without regard to case")
    unique: bool = Field(False, description="Drop repeated lines (keeps the first)")

    @field_validator("match")
    @classmethod
    def check_match(cls, value: str | None) -> str | None:
        if value:
            _compile(value)
        return value or None


@parser(
    "lines",
    "list<string>",
    "One value per non-empty line, optionally filtered",
    LinesOptions,
    sample="subdomains.txt",
)
def lines(data: bytes, options: LinesOptions | None = None) -> list[str]:
    options = options or LinesOptions()
    values = [ln.strip() for ln in data.decode("utf-8", errors="replace").splitlines()]
    values = [v for v in values if v]
    if options.match:
        pattern = _compile(options.match, options.ignore_case)
        values = [v for v in values if _search(pattern, v)]
    return [*dict.fromkeys(values)] if options.unique else values


HREF_EXAMPLE = r"""href=["']([^"']+)"""


class RegexOptions(Options):
    pattern: str = Field(
        description="Regular expression; every match becomes a value",
        examples=[HREF_EXAMPLE],
    )
    group: int | str = Field(
        0,
        description="Capture group to keep: a number (0 = the whole match) or a group name",
    )
    ignore_case: bool = Field(False, description="Match without regard to case")
    unique: bool = Field(True, description="Drop repeated values (keeps the first)")

    @field_validator("pattern")
    @classmethod
    def check_pattern(cls, value: str) -> str:
        if not value:
            raise ValueError("enter a pattern")
        _compile(value)
        return value

    @field_validator("group")
    @classmethod
    def check_group(cls, value: int | str) -> int | str:
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return value.strip() if isinstance(value, str) else value


@parser(
    "regex",
    "list<string>",
    "Every match of a regular expression (or one of its capture groups)",
    RegexOptions,
    sample="http-response.txt",
    sample_options={"pattern": HREF_EXAMPLE, "group": 1},
)
def regex_matches(data: bytes, options: RegexOptions) -> list[str]:
    pattern = _compile(options.pattern, options.ignore_case)
    group = options.group
    if isinstance(group, int) and group > pattern.groups:
        raise ParseError(f"the pattern has {pattern.groups} capture group(s), not {group}")
    if isinstance(group, str) and group not in pattern.groupindex:
        raise ParseError(f"the pattern has no group named {group!r}")
    values = [m.group(group) or "" for m in _search(pattern, data.decode("utf-8", errors="replace"))]
    values = [v for v in values if v]
    return [*dict.fromkeys(values)] if options.unique else values


class JsonRecordsOptions(Options):
    records: str = Field(
        "",
        description="Dot path to the records (results, data.items); empty: the document itself",
        examples=["results"],
    )


def _json_doc(data: bytes) -> Any:
    import json

    try:
        return json.loads(data.decode("utf-8", errors="replace") or "null")
    except ValueError as exc:
        raise ParseError(f"not valid JSON: {exc}") from exc


@parser(
    "json-records",
    "list<object>",
    "The records of a JSON document: an array, or the array at a dot path (an API's response)",
    JsonRecordsOptions,
    sample="api-page.json",
    sample_options={"records": "results"},
)
def json_records(data: bytes, options: JsonRecordsOptions | None = None) -> list[dict[str, Any]]:
    from .parser_spec import _dig

    options = options or JsonRecordsOptions()
    lines = _json_lines(data)
    if lines is not None:  # JSON lines (records.jsonl of a large paged result): one record per line
        return [i if isinstance(i, dict) else {"value": i} for i in lines]
    doc = _json_doc(data)
    found = _dig(doc, options.records) if options.records.strip() else doc
    if found is None:
        raise ParseError(f"nothing at {options.records!r} in the JSON")
    items = found if isinstance(found, list) else [found]
    return [i if isinstance(i, dict) else {"value": i} for i in items]


def _json_lines(data: bytes) -> list[Any] | None:
    """Records of JSON lines (one value per line), or None when it isn't that."""
    import json

    text = data.decode("utf-8", "replace").strip()
    if not text or text[0] not in "{[" or "\n" not in text:
        return None
    try:
        json.loads(text)
        return None  # one JSON document that spans lines
    except json.JSONDecodeError:
        pass
    out = []
    for line in text.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                return None
    return out


class JsonValueOptions(Options):
    path: str = Field(description="Dot path to the value (key, data.id, items.0.name)", examples=["key"])


@parser(
    "json-value",
    "string",
    "One value of a JSON document, by dot path (an issue's key, a new record's id)",
    JsonValueOptions,
    sample="api-page.json",
    sample_options={"path": "results.0.name"},
)
def json_value(data: bytes, options: JsonValueOptions) -> str:
    import json

    from .parser_spec import _dig

    found = _dig(_json_doc(data), options.path)
    if found is None:
        raise ParseError(f"nothing at {options.path!r} in the JSON")
    return found if isinstance(found, str) else json.dumps(found, ensure_ascii=False)


@parser("text", "string", "The whole output as text", sample="http-response.txt")
def text(data: bytes, options: Options | None = None) -> str:
    return data.decode("utf-8", errors="replace").strip()
