"""Collect the files a job wrote under its /work directory and upload them to the server.

Only the standard library is used so the agent stays a single small dependency.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

log = logging.getLogger("castor.agent.artifacts")

CHUNK = 1024 * 1024


def collect(job_dir: Path, patterns: list[str]) -> list[tuple[Path, str]]:
    """Return (file, path relative to /work) for files matching the patterns.

    Patterns are paths or globs under /work (e.g. "/work/scan.xml", "/work/artifacts/**").
    Symlinks and anything resolving outside the job directory are skipped."""
    root = job_dir.resolve()
    found: dict[str, Path] = {}
    for pattern in patterns:
        relative = pattern.removeprefix("/work").lstrip("/")
        if not relative:
            continue
        if relative.endswith("/**"):
            base = job_dir / relative[:-3]
            candidates = base.rglob("*") if base.is_dir() else []
        else:
            candidates = job_dir.glob(relative)
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            found.setdefault(resolved.relative_to(root).as_posix(), resolved)
    return [(path, rel) for rel, path in sorted(found.items())]


def upload_url(server_ws_url: str, job_id: str) -> str:
    """ws://host:8000/ws/agent -> http://host:8000/api/jobs/<id>/artifacts (keeps any prefix)."""
    parts = urlsplit(server_ws_url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    prefix = parts.path.removesuffix("/ws/agent").rstrip("/")
    return urlunsplit((scheme, parts.netloc, f"{prefix}/api/jobs/{job_id}/artifacts", "", ""))


def _multipart(path: Path, relative: str, boundary: str) -> tuple[list[bytes], Iterator[bytes], bytes]:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="path"\r\n\r\n{relative}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body() -> Iterator[bytes]:
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                yield chunk

    return [head], body(), tail


def upload(url: str, token: str, path: Path, relative: str, timeout: float = 120) -> None:
    """Upload one file (streamed). Raises urllib.error.URLError / HTTPError on failure."""
    boundary = f"castor-{uuid.uuid4().hex}"
    head, body, tail = _multipart(path, relative, boundary)
    length = len(head[0]) + path.stat().st_size + len(tail)

    def stream() -> Iterator[bytes]:
        yield head[0]
        yield from body
        yield tail

    request = Request(
        url,
        data=stream(),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(length),
            "X-Castor-Job-Token": token,
        },
    )
    with urlopen(request, timeout=timeout) as response:  # URL is built from our own server URL
        response.read()
