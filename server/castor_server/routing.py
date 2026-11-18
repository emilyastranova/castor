"""Network routes: the rules that don't need a database (docs/ROUTES.md).

A route is a chain of hops (SSH jumps, SOCKS4/5 proxies, HTTP CONNECT proxies) that runs on the
agent. How a job's traffic gets into it depends on the tool, so a tool (or a command) says what it
can take, in a `route:` block:

    route:
      proxy: "-proxy {url}"     # native: its own flag; {url} is the route's local end, {hostport} without scheme
      scheme: http              # what that flag (and env) speaks: socks5h (default), socks5, socks4a or http
      env: true                 # honours ALL_PROXY / HTTPS_PROXY / HTTP_PROXY
      hook: true                # dynamically linked: proxychains (LD_PRELOAD) works
      flags: "-sT -Pn -n"       # added wherever it's routed (nmap: a connect scan, no ping, no reverse DNS)
      only: [native, hooked]    # the only ways that give true answers (a port scanner, a DNS tool)
      unroutable: "why"         # can't go through a route at all (UDP scans, ping sweeps)

The command line says where those flags go with `{{ route }}` (rendered as ${CASTOR_ROUTE_ARGS}, empty
when the card isn't routed), so nothing about the route is ever written into the command itself.

`plan` turns that into the ways to try, best first:
- in a container (the job joins the route box's network): native, env, hooked, then transparent
  (the box sends every connection into the route anyway, so transparent always works for TCP);
- on the agent's host: native, hooked (proxychains on the host), env. Nothing holds a host job to
  the route, so a strict route ("only through the route") refuses host jobs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shlex
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KINDS = ("ssh", "socks5", "socks4", "http")
KIND_LABELS = {"ssh": "SSH", "socks5": "SOCKS5", "socks4": "SOCKS4", "http": "HTTP proxy"}
DEFAULT_PORTS = {"ssh": 22, "socks5": 1080, "socks4": 1080, "http": 8080}
METHODS = ("native", "env", "hooked", "transparent")
METHOD_WORDS = {"native": "its own proxy setting", "env": "proxy environment variables", "hooked": "proxychains",
                "transparent": "sending every connection through (in a container)"}
SCHEMES = ("socks5h", "socks5", "socks4a", "http")
MAX_HOPS = 8
HOST = re.compile(r"^[A-Za-z0-9_.:%\-\[\]]{1,253}$")
USER = re.compile(r"^[A-Za-z0-9_.@\\\-$]{0,128}$")
# {{ route }} in a command: where a tool's route flags go. ${CASTOR_ROUTE_ARGS} is unquoted on purpose:
# it's several words when set, nothing when not.
ROUTE = "route"
ROUTE_PLACEHOLDER = re.compile(r"\{\{\s*route\s*\}\}")
ROUTE_REF = "${CASTOR_ROUTE_ARGS}"
NONE = "none"  # a card's route: go direct even when the project has a default


class RouteError(ValueError):
    pass


# ---------- what a tool can take ----------

class RouteCaps(BaseModel):
    """A tool's or command's `route:` block."""

    model_config = ConfigDict(extra="forbid")

    proxy: str = ""
    scheme: Literal["socks5h", "socks5", "socks4a", "http"] = "socks5h"
    env: bool = False
    hook: bool = False
    flags: str = ""
    only: list[Literal["native", "env", "hooked", "transparent"]] = Field(default_factory=list)
    unroutable: str = ""

    @field_validator("proxy")
    @classmethod
    def check_proxy(cls, value: str) -> str:
        value = value.strip()
        if value and "{url}" not in value and "{hostport}" not in value:
            raise ValueError("proxy: the flag must say where the proxy goes, with {url} or {hostport}")
        if "${" in value or "`" in value or "$(" in value:
            raise ValueError("proxy: a flag, not shell expansions")
        return value

    @field_validator("flags")
    @classmethod
    def check_flags(cls, value: str) -> str:
        value = value.strip()
        try:
            words = shlex.split(value)
        except ValueError as exc:
            raise ValueError(f"flags: {exc}") from exc
        if any(not w.startswith("-") for w in words):
            raise ValueError("flags: options only (each starts with -)")
        if any(c in value for c in "$`;|&<>\n"):
            raise ValueError("flags: options only, no shell")
        return value


# A typed shell command (no tool, no library command): Castor can't know what it runs, so it tries
# proxy variables and proxychains (in a container, everything goes through anyway).
SHELL = RouteCaps(env=True, hook=True)


def merge_caps(tool: RouteCaps | None, command: RouteCaps | None, *, shell: bool = False) -> RouteCaps:
    """A command's block overrides its tool's, key by key."""
    if tool is None and command is None:
        return SHELL if shell else RouteCaps()
    base = tool.model_dump() if tool else {}
    if command is not None:
        base.update(command.model_dump(exclude_unset=True))
    return RouteCaps.model_validate(base)


# ---------- the command itself ----------

# Programs that can't go through a route at all (they need raw sockets, ICMP or UDP).
RAW_PROGRAMS = {
    "ping": "ping uses ICMP", "ping6": "ping uses ICMP", "fping": "fping uses ICMP", "traceroute": "traceroute uses ICMP or UDP",
    "tracepath": "tracepath uses UDP", "masscan": "masscan sends raw packets", "zmap": "zmap sends raw packets",
    "hping3": "hping3 sends raw packets", "arp-scan": "arp-scan works on the local network only",
    "nping": "nping sends raw packets", "unicornscan": "unicornscan sends raw packets",
}
NMAP_RAW = {
    "-sS": "a SYN scan", "-sU": "a UDP scan", "-sA": "an ACK scan", "-sW": "a window scan", "-sM": "a Maimon scan",
    "-sN": "a null scan", "-sF": "a FIN scan", "-sX": "an Xmas scan", "-sO": "an IP protocol scan", "-sY": "an SCTP scan",
    "-sZ": "an SCTP scan", "-sn": "a ping sweep", "-sP": "a ping sweep", "-PE": "ICMP pings", "-PP": "ICMP pings",
    "-PM": "ICMP pings", "-PU": "UDP pings", "-PY": "SCTP pings", "-O": "OS detection", "--traceroute": "a traceroute",
    "-sI": "an idle scan", "-b": "an FTP bounce scan",
}


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=True)
    except ValueError:
        return command.split()


def raw_reason(command: str, flags: str = "") -> str | None:
    """Why this command line can't go through a route (None: it can, as far as Castor can tell).
    `flags` are the tool's route flags, added where the command says {{ route }}."""
    words = _words(command.replace(ROUTE_REF, flags))
    for i, word in enumerate(words):
        prog = word.rsplit("/", 1)[-1]
        if prog in RAW_PROGRAMS and (i == 0 or words[i - 1] in ("|", "&&", "||", ";", "sudo", "exec", "then", "do")):
            return f"{RAW_PROGRAMS[prog]}, which can't go through a route"
        if prog == "nmap":
            rest = words[i + 1:]
            for flag in rest:
                if flag in ("|", "&&", "||", ";"):
                    break
                if flag in NMAP_RAW:
                    hint = " (use -sT -Pn -n: a connect scan)" if flag in ("-sS", "-sA", "-sW", "-sM", "-sN", "-sF", "-sX") else ""
                    return f"nmap's {NMAP_RAW[flag]} ({flag}) can't go through a route{hint}"
            if "-sT" not in rest:
                return "nmap would send raw packets (a SYN scan and pings) that can't go through a route: add -sT -Pn -n"
            if "-Pn" not in rest:
                return "nmap's host discovery pings can't go through a route: add -Pn"
    return None


def plan(caps: RouteCaps, command: str, *, in_container: bool, strict: bool, agent: dict[str, Any]) -> list[dict[str, Any]]:
    """The ways to send this run through its route, best first, as the agent wants them. Raises
    RouteError with the reason when there's none."""
    if caps.unroutable:
        raise RouteError(caps.unroutable)
    if not in_container and strict:
        raise RouteError("this route lets nothing out except through it, and that needs a container: give the card an "
                         "image or a tool that runs in one, or turn off “Only through the route”")
    has_slot = ROUTE_REF in command
    reason = raw_reason(command, caps.flags)
    if reason:
        raise RouteError(reason)
    native = bool(caps.proxy) and has_slot
    order = ["native", "env", "hooked", "transparent"] if in_container else ["native", "hooked", "env"]
    usable = {
        "native": native,
        "env": caps.env,
        # A container image gets proxychains added (if it can); a host needs it installed.
        "hooked": caps.hook and (in_container or bool((agent.get("routes") or {}).get("proxychains"))),
        "transparent": in_container,
    }
    allowed = set(caps.only) if caps.only else set(METHODS)
    ways = [m for m in order if usable[m] and m in allowed]
    if not ways:
        hint = "" if in_container else " (install proxychains-ng on the agent, or run it in a container)"
        if caps.only:
            needs = " or ".join(METHOD_WORDS[m] for m in caps.only)
            missing = "; its command has no {{ route }} for the proxy flag" if "native" in caps.only and caps.proxy and not has_slot else ""
            raise RouteError(f"through a route, this tool only gives true answers by {needs}, and that isn't available here{missing}{hint}")
        raise RouteError(f"nothing here can send it through the route: it has no proxy setting Castor knows, and proxychains isn't available{hint}")
    flags = caps.flags if has_slot else ""
    return [{"method": m, "scheme": caps.scheme, "args": caps.proxy if m == "native" else "", "flags": flags} for m in ways]


# ---------- hops and routes ----------

class HopIn(BaseModel):
    """One hop as people send it. Secrets are write-only: `password` or `private_key` (with its
    `passphrase`), or `generate_key`; leaving them out keeps what's stored."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    kind: Literal["ssh", "socks5", "socks4", "http"]
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = ""
    auth: Literal["none", "password", "key"] | None = None
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    generate_key: bool = False
    host_key: str | None = None  # SSH: pin a key ahead of time ("ssh-ed25519 AAAA...")
    label: str = Field(default="", max_length=80)

    @field_validator("host")
    @classmethod
    def check_host(cls, value: str) -> str:
        value = value.strip()
        if not HOST.match(value) or value.startswith("-"):
            raise ValueError("a host name or address (no spaces, not starting with -)")
        return value

    @field_validator("username")
    @classmethod
    def check_user(cls, value: str) -> str:
        value = value.strip()
        if not USER.match(value) or value.startswith("-"):
            raise ValueError("a user name without spaces")
        return value

    @field_validator("host_key")
    @classmethod
    def check_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parts = value.split()
        if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-")):
            raise ValueError("an OpenSSH public key line, like ssh-ed25519 AAAA...")
        return f"{parts[0]} {parts[1]}"

    @model_validator(mode="after")
    def check_auth(self) -> HopIn:
        if self.kind == "socks4" and (self.password or self.auth == "password"):
            raise ValueError("SOCKS4 has no passwords (only a user id); use SOCKS5")
        if self.kind != "ssh" and (self.private_key or self.generate_key or self.auth == "key"):
            raise ValueError("only SSH hops sign in with a key")
        if self.kind == "ssh" and not self.username:
            raise ValueError("an SSH hop needs a user name")
        return self


class RouteAgents(BaseModel):
    """The agents a route works from: these, and any agent with one of these tags."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(default_factory=list, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=50)


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    hops: list[HopIn] = Field(min_length=1, max_length=MAX_HOPS)
    dns: Literal["remote", "local"] = "remote"
    strict: bool = True
    segment: str = Field(default="", max_length=80)
    # Only from these agents (None: any agent).
    agents: RouteAgents | None = None


def chain_label(hops: list[dict[str, Any]]) -> str:
    """agent → SSH ops@jump01:22 → SOCKS5 10.0.0.5:1080 → targets"""
    parts = ["agent"]
    for h in hops:
        user = f"{h['username']}@" if h.get("username") and h.get("kind") == "ssh" else ""
        host = h.get("host", "")
        host = f"[{host}]" if ":" in host else host
        parts.append(f"{KIND_LABELS.get(h.get('kind'), h.get('kind'))} {user}{host}:{h.get('port')}")
    return " → ".join([*parts, "targets"])


def route_key(hops: list[dict[str, Any]], dns: str, strict: bool) -> str:
    """Identifies a route as it is now (its hops with secrets, its options): a change means a new box."""
    material = json.dumps({"hops": hops, "dns": dns, "strict": strict}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def fingerprint(key_line: str | None) -> str | None:
    """SHA256:… as OpenSSH prints it."""
    if not key_line:
        return None
    import base64

    parts = key_line.split()
    if len(parts) < 2:
        return None
    try:
        raw = base64.b64decode(parts[1])
    except ValueError:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


# ---------- names the route box stood in for ----------

FAKE_NET = ipaddress.ip_network("198.18.0.0/15")


def stand_ins(names: Any) -> dict[str, str]:
    """The route box's {stand-in address: name} map, as an agent reported it: only addresses in
    198.18.0.0/15 and names that look like names (it came over the wire)."""
    out: dict[str, str] = {}
    if not isinstance(names, dict):
        return out
    for ip, name in list(names.items())[:2000]:
        try:
            if ipaddress.ip_address(str(ip)) not in FAKE_NET:
                continue
        except ValueError:
            continue
        name = str(name).strip().lower().rstrip(".")
        if name and len(name) <= 253 and re.fullmatch(r"[a-z0-9_.-]+", name):
            out[str(ip)] = name
    return out


def unfake(value: Any, names: dict[str, str]) -> Any:
    """Parsed results with the box's stand-in addresses put back as the names they stood for (a
    tool that resolved intranet.lab in the box saw 198.18.0.1, which means nothing anywhere else)."""
    if isinstance(value, str):
        if value in names:
            return names[value]
        for ip, name in names.items():
            if ip in value:
                value = re.sub(rf"(?<![\d.]){re.escape(ip)}(?![\d])", name, value)
        return value
    if isinstance(value, list):
        return [unfake(v, names) for v in value]
    if isinstance(value, dict):
        return {k: unfake(v, names) for k, v in value.items()}
    return value
