"""ToolPreparer against a fake container runtime (a shell script standing in for docker)."""

import asyncio
import os
import stat
from pathlib import Path

from castor_agent.tools import ToolPreparer

FAKE = """#!/bin/sh
state="{state}"
case "$1 $2" in
  "image inspect") [ -f "$state/built" ] || [ -f "$state/pulled" ]; exit $? ;;
  "image ls") [ -f "$state/built" ] && echo "castor-tools/demo:abc123"; exit 0 ;;
esac
case "$1" in
  build) echo "#1 building $3"; cp "$4/Dockerfile" "$state/Dockerfile"; [ -z "$FAIL_BUILD" ] || exit 7; touch "$state/built"; echo "#2 done" ;;
  pull) echo "pulling $2"; touch "$state/pulled" ;;
esac
"""


def _runtime(tmp_path: Path) -> str:
    script = tmp_path / "fake-docker"
    script.write_text(FAKE.format(state=tmp_path))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _run(coro):
    return asyncio.run(coro)


def test_build_once_then_reuse(tmp_path):
    prep = ToolPreparer(_runtime(tmp_path), False, str(tmp_path / "work"))
    lines: list[str] = []

    async def sink(_stream, data):
        lines.append(data)

    tool = {"id": "demo", "name": "Demo", "kind": "build", "ref": "castor-tools/demo:abc123",
            "files": {"Dockerfile": "FROM alpine\n", "castor-install.sh": "true\n"}}
    assert _run(prep.prepare(tool, sink, str(tmp_path))) is None
    assert any("building castor-tools/demo:abc123" in line for line in lines)  # streamed to the job
    assert (tmp_path / "Dockerfile").read_text() == "FROM alpine\n"
    assert prep.cached == ["castor-tools/demo:abc123"]
    lines.clear()
    assert _run(prep.prepare(tool, sink, str(tmp_path))) is None
    assert lines == []  # cached: nothing to do
    assert not list((tmp_path / "work").iterdir())  # the build context was removed


def test_failed_build_and_bad_file_names(tmp_path):
    prep = ToolPreparer(_runtime(tmp_path), False, None)

    async def sink(_stream, _data):
        pass

    os.environ["FAIL_BUILD"] = "1"
    try:
        tool = {"id": "demo", "name": "Demo", "kind": "build", "ref": "castor-tools/demo:abc123", "files": {"Dockerfile": "FROM x\n"}}
        assert "building Demo failed (exit 7)" in _run(prep.prepare(tool, sink, str(tmp_path)))
    finally:
        del os.environ["FAIL_BUILD"]
    sneaky = {"id": "demo", "name": "Demo", "kind": "build", "ref": "castor-tools/demo:x", "files": {"Dockerfile": "FROM x\n", "../evil": "x"}}
    assert "refusing a recipe file" in _run(prep.prepare(sneaky, sink, str(tmp_path)))


def test_image_is_pulled_when_missing(tmp_path):
    prep = ToolPreparer(_runtime(tmp_path), False, None)
    lines: list[str] = []

    async def sink(_stream, data):
        lines.append(data)

    assert _run(prep.prepare({"id": "nmap", "name": "Nmap", "kind": "image", "ref": "docker.io/x/nmap"}, sink, str(tmp_path))) is None
    assert any("pulling docker.io/x/nmap" in line for line in lines)
    no_runtime = ToolPreparer(None, False, None)
    error = _run(no_runtime.prepare({"id": "n", "name": "N", "kind": "image", "ref": "x"}, sink, str(tmp_path)))
    assert "needs a container runtime" in error


def test_host_check_and_opt_in_install(tmp_path):
    async def sink(_stream, _data):
        pass

    marker = tmp_path / "installed"
    tool = {"id": "t", "name": "Thing", "kind": "host", "check": f"test -f {marker}", "install": "", "install_hint": f"touch {marker}"}
    error = _run(ToolPreparer(None, False, None).prepare(tool, sink, str(tmp_path)))
    assert "isn't installed on this agent's host" in error and f"touch {marker}" in error
    allowed = {**tool, "install": f"touch {marker}"}
    assert _run(ToolPreparer(None, True, None).prepare(allowed, sink, str(tmp_path))) is None
    assert marker.exists()
