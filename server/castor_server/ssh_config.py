"""The SSH config SSH hosts use (Agents → SSH config), in OpenSSH's ssh_config format.

Castor reads it itself instead of handing it to a library, so the config can only shape how
it reaches a host (aliases, users, ports, jump hosts, keepalives) and never makes the server
run a command or read a file:
- used: Host, HostName (%h and %% tokens), Port, User, ProxyJump, ConnectTimeout,
  ServerAliveInterval, ServerAliveCountMax;
- refused (an error): ProxyCommand, Match and Include;
- anything else (IdentityFile, ForwardAgent…) is ignored with a warning, so a real
  ~/.ssh/config can be pasted. Each hop signs in with the SSH host's own key and has its own
  host key, trusted on first use.

As in OpenSSH, the first value found for a keyword wins, options before the first Host line
apply to every host, and an option given on the host itself (its port and username fields)
beats the config. `ProxyJump a,b` reaches b through a; each jump host gets its own config
(including its own ProxyJump, unless it's a later hop of a list)."""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass, field
from urllib.parse import urlsplit

USED = {"host", "hostname", "port", "user", "proxyjump", "connecttimeout", "serveraliveinterval", "serveralivecountmax"}
REFUSED = {
    "proxycommand": "ProxyCommand would run a command on the Castor server; use ProxyJump",
    "match": "Match blocks aren't supported; use Host patterns",
    "include": "Include would read files on the Castor server; paste their contents instead",
}
MAX_HOPS = 8
SETTING = "ssh_config"  # its app_settings key (admins only; not part of AppSettings)


class ConfigError(ValueError):
    pass


@dataclass
class Block:
    patterns: list[str]
    options: list[tuple[str, str, int]] = field(default_factory=list)  # keyword, value, line

    def matches(self, host: str) -> bool:
        host = host.lower()
        hit = False
        for pattern in self.patterns:
            negated = pattern.startswith("!")
            if fnmatch.fnmatchcase(host, pattern.lstrip("!").lower()):
                if negated:
                    return False
                hit = True
        return hit


@dataclass
class Parsed:
    blocks: list[Block]
    errors: list[str]
    warnings: list[str]


def parse(text: str) -> Parsed:
    blocks = [Block(["*"])]  # options before the first Host apply to every host
    errors: list[str] = []
    ignored: dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z]+)\s*(?:=\s*|\s+)(.*)$", line)
        if not match:
            errors.append(f"line {number}: expected “Keyword value”")
            continue
        keyword, rest = match.group(1).lower(), match.group(2).strip()
        try:
            args = shlex.split(rest, comments=False, posix=True)
        except ValueError:
            errors.append(f"line {number}: unbalanced quotes")
            continue
        if not args:
            errors.append(f"line {number}: {match.group(1)} needs a value")
            continue
        if keyword in REFUSED:
            errors.append(f"line {number}: {REFUSED[keyword]}")
        elif keyword == "host":
            blocks.append(Block(args))
        elif keyword in USED:
            if keyword in ("port", "connecttimeout", "serveraliveinterval", "serveralivecountmax") and not args[0].isdigit():
                errors.append(f"line {number}: {match.group(1)} must be a number")
                continue
            if keyword == "port" and not 0 < int(args[0]) < 65536:
                errors.append(f"line {number}: Port must be between 1 and 65535")
                continue
            if keyword == "hostname" and re.search(r"%[^h%]", args[0]):
                errors.append(f"line {number}: HostName supports only the %h and %% tokens")
                continue
            if keyword == "proxyjump" and args[0].lower() != "none":
                try:
                    for spec in args[0].split(","):
                        jump_spec(spec)
                except ConfigError as exc:
                    errors.append(f"line {number}: {exc}")
                    continue
            blocks[-1].options.append((keyword, args[0], number))
        else:
            ignored.setdefault(match.group(1), number)
    warnings = [f"line {n}: {k} is ignored (Castor only uses Host, HostName, Port, User, ProxyJump and keepalives;"
                f" every hop signs in with the SSH host's own key)" for k, n in ignored.items()]
    return Parsed(blocks, errors, warnings)


@dataclass
class Jump:
    host: str
    user: str | None = None
    port: int | None = None


def jump_spec(spec: str) -> Jump:
    """One ProxyJump entry: [user@]host[:port] or ssh://[user@]host[:port]."""
    spec = spec.strip()
    if not spec:
        raise ConfigError("empty ProxyJump entry")
    if spec.startswith("ssh://"):
        parts = urlsplit(spec)
        if not parts.hostname:
            raise ConfigError(f"bad ProxyJump entry {spec!r}")
        return Jump(parts.hostname, parts.username, parts.port)
    user, _, hostport = spec.rpartition("@")
    m = re.fullmatch(r"\[([^\]]+)\](?::(\d+))?|([^:\[\]]+)(?::(\d+))?", hostport)
    if not m or (user and not re.fullmatch(r"[^\s@]+", user)):
        raise ConfigError(f"bad ProxyJump entry {spec!r}")
    host = m.group(1) or m.group(3)
    port = m.group(2) or m.group(4)
    if port and not 0 < int(port) < 65536:
        raise ConfigError(f"bad port in ProxyJump entry {spec!r}")
    return Jump(host, user or None, int(port) if port else None)


@dataclass
class Hop:
    alias: str
    hostname: str
    port: int
    user: str
    connect_timeout: int | None = None
    keepalive_interval: int | None = None
    keepalive_count: int | None = None

    @property
    def label(self) -> str:
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{self.user}@{host}:{self.port}"


def _options(parsed: Parsed, alias: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for block in parsed.blocks:
        if block.matches(alias):
            for keyword, value, _ in block.options:
                found.setdefault(keyword, value)
    return found


def _hop(parsed: Parsed, alias: str, user: str | None, port: int | None, fallback_user: str) -> tuple[Hop, list[str]]:
    o = _options(parsed, alias)
    hostname = o.get("hostname", "%h").replace("%%", "\0").replace("%h", alias).replace("\0", "%")
    hop = Hop(
        alias=alias, hostname=hostname, port=port or int(o.get("port", 22)),
        user=user or o.get("user") or fallback_user,
        connect_timeout=int(o["connecttimeout"]) if "connecttimeout" in o else None,
        keepalive_interval=int(o["serveraliveinterval"]) if "serveraliveinterval" in o else None,
        keepalive_count=int(o["serveralivecountmax"]) if "serveralivecountmax" in o else None,
    )
    jump = o.get("proxyjump", "none")
    return hop, ([] if jump.lower() == "none" else jump.split(","))


def route(parsed: Parsed, host: str, user: str | None, port: int | None) -> list[Hop]:
    """The hops to reach `host`, the last being the host itself."""
    if parsed.errors:
        raise ConfigError("the SSH config has errors: " + "; ".join(parsed.errors))
    target, _ = _hop(parsed, host, user, port, user or "")
    if not target.user:
        raise ConfigError(f"no username for {host}: set one on the host or add a User line for it in the SSH config")

    def reach(alias: str, juser: str | None, jport: int | None, override: list[str] | None, depth: int) -> list[Hop]:
        if depth > MAX_HOPS:
            raise ConfigError(f"more than {MAX_HOPS} jump hosts (is there a ProxyJump loop?)")
        hop, own = _hop(parsed, alias, juser, jport, target.user)
        jumps = own if override is None else override
        if not jumps:
            return [hop]
        last = jump_spec(jumps[-1])
        return reach(last.host, last.user, last.port, jumps[:-1] or None, depth + 1) + [hop]

    return reach(host, user, port, None, 0)
