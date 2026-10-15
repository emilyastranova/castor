"""Parsers turn a tool's output file (or stdout) into a typed value (Phase 2b).

Each parser takes raw bytes and returns JSON-serialisable data of one of the fixed output
types below, so later playbook steps can check what they are wired to. Parsers are pure
functions and run in a worker thread; raise ParseError for input they can't understand.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

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
)


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Parser:
    name: str
    type: str
    description: str
    func: Callable[[bytes], Any]

    @property
    def is_list(self) -> bool:
        return self.type.startswith("list<")


PARSERS: dict[str, Parser] = {}


def parser(name: str, type_: str, description: str):
    def register(func: Callable[[bytes], Any]) -> Callable[[bytes], Any]:
        PARSERS[name] = Parser(name, type_, description, func)
        return func

    return register


def get(name: str) -> Parser | None:
    return PARSERS.get(name)


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


@parser("nmap.hosts", "list<host>", "Hosts from nmap XML (-oX), with status and hostnames")
def nmap_hosts(data: bytes) -> list[dict[str, Any]]:
    hosts = []
    for host in _nmap_root(data).findall("host"):
        ip = _host_ip(host)
        if ip is None:
            continue
        status = host.find("status")
        hosts.append(
            {
                "ip": ip,
                "hostnames": _hostnames(host),
                "status": status.get("state", "unknown") if status is not None else "unknown",
            }
        )
    return hosts


@parser("nmap.services", "list<service>", "Open ports from nmap XML (-oX), with version info")
def nmap_services(data: bytes) -> list[dict[str, Any]]:
    services = []
    for host in _nmap_root(data).findall("host"):
        ip = _host_ip(host)
        if ip is None:
            continue
        for port in host.findall("ports/port"):
            state = port.find("state")
            state_name = state.get("state", "") if state is not None else ""
            # "open|filtered" (UDP) is kept; closed and filtered ports are noise here.
            if not state_name.startswith("open"):
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


@parser("ffuf.results", "list<url>", "Matched URLs from ffuf JSON output (-of json)")
def ffuf_results(data: bytes) -> list[dict[str, Any]]:
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
        if isinstance(r, dict) and r.get("url")
    ]


# ---------- generic ----------


@parser("lines", "list<string>", "One value per non-empty line")
def lines(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


@parser("text", "string", "The whole output as text")
def text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()
