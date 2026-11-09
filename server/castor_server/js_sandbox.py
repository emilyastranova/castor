"""JavaScript for parsers, run in a V8 isolate (mini-racer) that can't reach anything.

The isolate has no file system, network, `require`, `process` or timers: the code gets the
tool's output as a string and can only compute on it. Each run gets a fresh isolate, a time
limit and a memory cap, and the result comes back as JSON. Python can't be sandboxed like
this in-process (a parser could read the vault key and the database), which is why parsers
people and the AI write in code are JavaScript.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from py_mini_racer import (
    JSEvalException,
    JSOOMException,
    JSParseException,
    JSTimeoutException,
    MiniRacer,
)

TIMEOUT_S = 3.0
CHECK_TIMEOUT_S = 0.5  # compiling and defining parse: anything slower is a problem anyway
MAX_MEMORY = 256 * 1024 * 1024
MAX_INPUT = 32 * 1024 * 1024
MAX_RESULT = 64 * 1024 * 1024
# Calls the parser and hands back JSON: plain data only, whatever the code returned.
WRAPPER = "\n;function __castor_run(text) { const out = parse(text); return JSON.stringify(out === undefined ? null : out); }"


class JsError(ValueError):
    pass


# mini-racer won't run a timed evaluation on a thread with a running event loop (it expects
# its async API there), so from async code the isolate runs on a worker thread of its own.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="castor-js")


def _off_loop(fn: Callable[[], Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn()
    return _pool.submit(fn).result()


def _message(exc: Exception) -> str:
    if isinstance(exc, JSTimeoutException):
        return f"the code took longer than {TIMEOUT_S:g}s (an endless loop?)"
    if isinstance(exc, JSOOMException):
        return f"the code used more than {MAX_MEMORY // (1024 * 1024)} MB of memory"
    text = str(exc).strip()
    # V8 reports "<anonymous>:LINE: Error: message" then the source line and a caret.
    first = text.splitlines()[0] if text else type(exc).__name__
    return first.replace("<anonymous>:", "line ")


def check(code: str) -> None:
    """Compile the code and make sure it defines parse(text)."""
    kind = _off_loop(lambda: _check(code))
    if kind != "function":
        raise JsError("define a function parse(text) that returns the results")


def _check(code: str) -> str:
    with MiniRacer() as ctx:
        try:
            ctx.eval(code, timeout_sec=CHECK_TIMEOUT_S, max_memory=MAX_MEMORY)
            return ctx.eval("typeof parse", timeout_sec=CHECK_TIMEOUT_S)
        except JSTimeoutException as exc:
            raise JsError("the code runs too long just being defined: only define parse(text) at the top level") from exc
        except (JSParseException, JSEvalException, JSOOMException) as exc:
            raise JsError(_message(exc)) from exc


def run(code: str, text: str) -> Any:
    """parse(text) in a fresh isolate; JSON-shaped data, or JsError."""
    if len(text) > MAX_INPUT:
        raise JsError(f"the output is larger than {MAX_INPUT // (1024 * 1024)} MB, too big for a code parser")
    return _off_loop(lambda: _run(code, text))


def _run(code: str, text: str) -> Any:
    with MiniRacer() as ctx:
        try:
            ctx.eval(code + WRAPPER, timeout_sec=TIMEOUT_S, max_memory=MAX_MEMORY)
            out = ctx.call("__castor_run", text, timeout_sec=TIMEOUT_S, max_memory=MAX_MEMORY)
        except (JSParseException, JSEvalException, JSTimeoutException, JSOOMException) as exc:
            raise JsError(_message(exc)) from exc
    if not isinstance(out, str) or len(out) > MAX_RESULT:
        raise JsError("parse(text) must return plain data (objects, arrays, strings, numbers)")
    return json.loads(out)
