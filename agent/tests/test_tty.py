"""Interactive jobs (3j): a real pty, keystrokes in, window size, prompt detection."""

import asyncio
import sys

import pytest

from castor_agent import runtime
from castor_agent.runtime import JobRunner

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="ptys are POSIX")


def _runner(tmp_path, command, **job):
    return JobRunner(
        {"id": "00000000-0000-0000-0000-000000000001", "command": command, "tty": True, **job},
        container_runtime=None, allow_host=True, default_image="", job_dir=str(tmp_path), network="", extra_run_args=[],
    )


def test_terminal_prompt_input_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "STRONG_QUIET", 0.3)
    command = 'stty size; echo "term=$TERM color=$COLORTERM"; [ -t 0 ] && echo tty-in; printf "Password: "; stty -echo; read -r secret; stty echo; echo; echo "got ${#secret} chars"'
    runner = _runner(tmp_path, command, tty_size={"cols": 100, "rows": 40})
    output: list[str] = []
    prompts: list[str | None] = []

    async def sink(stream, data):
        output.append(data)

    async def on_prompt(text):
        prompts.append(text)
        if text:  # answer it like a person at the terminal would
            await runner.write_input("hunter2\r")

    result = asyncio.run(runner.run(sink, on_prompt))
    text = "".join(output)
    assert result.exit_code == 0, text
    assert "40 100" in text and "term=xterm-256color color=truecolor" in text and "tty-in" in text
    assert "got 7 chars" in text and "hunter2" not in text  # echo off, like a real password prompt
    assert prompts[0] == "Password:" and prompts[-1] is None


def test_resize_reaches_the_job(tmp_path):
    runner = _runner(tmp_path, 'trap "stty size" WINCH; stty size; i=0; while [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done')
    output: list[str] = []

    async def sink(stream, data):
        output.append(data)

    async def go():
        task = asyncio.create_task(runner.run(sink))
        await asyncio.sleep(0.5)
        runner.resize(132, 50)
        return await task

    result = asyncio.run(go())
    text = "".join(output)
    assert result.exit_code == 0 and "32 120" in text and "50 132" in text, text


def test_ctrl_c_interrupts(tmp_path):
    runner = _runner(tmp_path, "sleep 30")

    async def sink(stream, data):
        pass

    async def go():
        task = asyncio.create_task(runner.run(sink))
        await asyncio.sleep(0.4)
        runner.send_signal("INT")
        return await asyncio.wait_for(task, 5)

    result = asyncio.run(go())
    assert result.exit_code not in (0, None)  # killed by SIGINT, through the terminal
