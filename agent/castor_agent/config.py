"""Agent configuration from CLI flags and environment variables."""

from __future__ import annotations

import argparse
import os
import platform
import socket
from dataclasses import dataclass, field

VERSION = "0.1.0"


@dataclass
class AgentConfig:
    server_url: str
    secret: str
    name: str
    runtime: str  # auto | podman | docker | host
    allow_host: bool
    default_image: str
    tags: list[str] = field(default_factory=list)
    max_concurrency: int = 1
    workdir: str | None = None
    network: str = "host"
    extra_run_args: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    # Its own token from the server (Agents → Enroll agent); preferred over the secret.
    token: str = ""

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> AgentConfig:
        env = os.environ.get
        p = argparse.ArgumentParser(prog="castor-agent", description="Castor execution agent")
        p.add_argument("--server", default=env("CASTOR_SERVER_URL", "ws://localhost:8000/ws/agent"),
                       help="Server WebSocket URL (CASTOR_SERVER_URL)")
        p.add_argument("--token", default=env("CASTOR_AGENT_TOKEN", ""),
                       help="This agent's token, from Agents → Enroll agent (CASTOR_AGENT_TOKEN)")
        p.add_argument("--secret", default=env("CASTOR_AGENT_SECRET", ""),
                       help="Shared agent secret, if the server still allows it (CASTOR_AGENT_SECRET)")
        p.add_argument("--name", default=env("CASTOR_AGENT_NAME") or socket.gethostname(),
                       help="Agent name shown in the UI (CASTOR_AGENT_NAME)")
        p.add_argument("--runtime", default=env("CASTOR_AGENT_RUNTIME", "auto"),
                       choices=["auto", "podman", "docker", "host"],
                       help="Container runtime for jobs with an image (CASTOR_AGENT_RUNTIME)")
        p.add_argument("--allow-host", action="store_true",
                       default=env("CASTOR_AGENT_ALLOW_HOST", "true").lower() in ("1", "true", "yes"),
                       help="Allow jobs without an image to run in the host shell")
        p.add_argument("--no-allow-host", dest="allow_host", action="store_false")
        p.add_argument("--default-image", default=env("CASTOR_AGENT_DEFAULT_IMAGE", "docker.io/library/alpine:latest"),
                       help="Image used when a job asks for a container but names none")
        p.add_argument("--tags", default=env("CASTOR_AGENT_TAGS", ""),
                       help="Comma-separated tags for targeting (CASTOR_AGENT_TAGS)")
        p.add_argument("--max-concurrency", type=int, default=int(env("CASTOR_AGENT_MAX_CONCURRENCY", "1")))
        p.add_argument("--workdir", default=env("CASTOR_AGENT_WORKDIR"),
                       help="Parent directory for per-job work directories; each job's own "
                            "directory is mounted at /work (default: a temp directory)")
        p.add_argument("--network", default=env("CASTOR_AGENT_NETWORK", "host"),
                       help="Container network mode (default: host, so containers see the host network)")
        p.add_argument("--run-arg", action="append", default=[],
                       help="Extra argument passed to `<runtime> run` (repeatable)")
        p.add_argument("--log-level", default=env("CASTOR_AGENT_LOG_LEVEL", "INFO"))
        a = p.parse_args(argv)
        if not a.token and not a.secret:
            p.error("--token (CASTOR_AGENT_TOKEN) or --secret (CASTOR_AGENT_SECRET) is required")
        return cls(
            server_url=a.server,
            secret=a.secret,
            token=a.token,
            name=a.name,
            runtime=a.runtime,
            allow_host=a.allow_host,
            default_image=a.default_image,
            tags=[t.strip() for t in a.tags.split(",") if t.strip()],
            max_concurrency=max(1, a.max_concurrency),
            workdir=a.workdir,
            network=a.network,
            extra_run_args=a.run_arg,
            log_level=a.log_level,
        )


def host_info() -> dict[str, str]:
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "username": user,
        "version": VERSION,
    }
