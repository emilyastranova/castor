"""Which addresses Castor's own outbound requests may reach (HTTP actions and alert channels).

Loopback, link-local (where cloud metadata services live), multicast and reserved addresses
are refused unless an admin lists them (`app_settings.http_allow_addresses`, or
CASTOR_HTTP_ALLOW_ADDRESSES), and so are private networks when an admin turns
`http_block_private` on. HTTP actions can also be held to a list of hosts
(`http_allowed_hosts`).

The check is made on the addresses a name resolves to at connect time (`GuardedBackend`
for httpx), so a name that resolves to a public address when checked and an internal one when
connecting can't slip past it."""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from .config import settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

ALWAYS_BLOCKED: list[Network] = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
        "100.100.100.200/32",  # Alibaba Cloud's metadata service
        "::/128", "::1/128", "fe80::/10", "ff00::/8",
        "fd00:ec2::254/128",  # AWS's metadata service over IPv6
    )
]
PRIVATE: list[Network] = [
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
]


class OutboundRefused(Exception):
    """A request Castor won't make; the message says why, for the card's log."""


def _networks(items: list[str] | str | None) -> list[Network]:
    if isinstance(items, str):
        items = [i for i in items.replace(",", " ").split() if i]
    out = []
    for item in items or []:
        try:
            out.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return out


@dataclass
class Policy:
    allow: list[Network] = field(default_factory=list)  # exempt from every block
    block_private: bool = False
    hosts: list[str] = field(default_factory=list)  # empty: any host

    def check_ip(self, ip: IPAddress) -> None:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if any(ip in n for n in self.allow):
            return
        if any(ip in n for n in ALWAYS_BLOCKED):
            raise OutboundRefused(
                f"{ip} is a loopback, link-local or reserved address; an admin can allow it in Settings → General"
            )
        if self.block_private and any(ip in n for n in PRIVATE):
            raise OutboundRefused(f"{ip} is a private address, and private addresses are blocked in Settings → General")

    def check_host(self, host: str) -> None:
        """The allowed hosts (HTTP actions only): exact names or *.example.com."""
        if not self.hosts:
            return
        host = host.lower().rstrip(".")
        for pattern in self.hosts:
            pattern = pattern.lower().strip().rstrip(".")
            if host == pattern or (pattern.startswith("*.") and fnmatch.fnmatchcase(host, pattern)):
                return
        raise OutboundRefused(f"{host} isn't one of the hosts HTTP actions may call (Settings → General)")


def check_url(url: str) -> str:
    """The URL's host; raises OutboundRefused for anything but http(s) with a host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise OutboundRefused("the URL must start with http:// or https://")
    if not parts.hostname:
        raise OutboundRefused("the URL has no host")
    if parts.username or parts.password:
        raise OutboundRefused("put credentials in auth or a header, not in the URL")
    return parts.hostname


async def policy(db: Any, *, actions: bool = True) -> Policy:
    """The policy from app settings and the environment. `actions` False (alert channels):
    the allowed-host list doesn't apply."""
    from .app_settings import load

    s = await load(db)
    return Policy(
        allow=_networks(s.http_allow_addresses) + _networks(settings.http_allow_addresses),
        block_private=s.http_block_private,
        hosts=list(s.http_allowed_hosts) if actions else [],
    )


async def resolve(host: str, port: int, rules: Policy) -> list[str]:
    """The addresses to connect to, every one of them checked; raises OutboundRefused."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        rules.check_ip(literal)
        return [str(literal)]
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundRefused(f"can't resolve {host}: {exc.strerror or exc}") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise OutboundRefused(f"{host} has no address")
    for address in addresses:
        rules.check_ip(ipaddress.ip_address(address.split("%")[0]))
    return addresses


def resolve_sync(host: str, port: int, rules: Policy) -> None:
    """resolve() for threads that use urllib (alert channels)."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        rules.check_ip(literal)
        return
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundRefused(f"can't resolve {host}: {exc.strerror or exc}") from exc
    for info in infos:
        rules.check_ip(ipaddress.ip_address(str(info[4][0]).split("%")[0]))


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """httpcore's network backend, connecting only to checked addresses. TLS still verifies
    the certificate for the name in the URL (httpcore passes it separately)."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend, rules: Policy) -> None:
        self.inner = inner
        self.rules = rules

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[override]
        addresses = await resolve(host, port, self.rules)
        error: Exception | None = None
        for address in addresses:
            try:
                return await self.inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                                    socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        raise error or httpcore.ConnectError(f"can't connect to {host}")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # type: ignore[override]
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def client(rules: Policy, timeout: float) -> httpx.AsyncClient:
    """An httpx client whose every connection goes through the policy. Proxies from the
    environment are not used (they'd connect for us, past the check), and redirects are
    followed by the caller, one hop at a time."""
    transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    pool = transport._pool
    pool._network_backend = GuardedBackend(pool._network_backend, rules)
    return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
                             headers={"User-Agent": "castor"})
