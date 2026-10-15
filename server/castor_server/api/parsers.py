from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import parsers
from ..deps import CurrentUser
from ..outputs import run_parser

router = APIRouter(prefix="/parsers", tags=["parsers"])

# Test input is typed or pasted in the browser; real artifacts go through outputs.py.
MAX_TEST_BYTES = 2 * 1024 * 1024


class OptionFieldOut(BaseModel):
    name: str
    kind: str
    description: str
    required: bool
    default: Any
    example: Any


class ParserOut(BaseModel):
    name: str
    type: str
    description: str
    options: list[OptionFieldOut]
    sample: str
    sample_options: dict[str, Any]


class ParseTestIn(BaseModel):
    input: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class ParseTestOut(BaseModel):
    type: str
    value: Any = None
    error: str | None = None
    option_errors: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: float = 0


def _out(p: parsers.Parser) -> ParserOut:
    return ParserOut(
        name=p.name,
        type=p.type,
        description=p.description,
        options=[OptionFieldOut(**f) for f in parsers.option_fields(p.options)],
        sample=p.sample_text(),
        sample_options=p.sample_options,
    )


@router.get("", response_model=list[ParserOut])
async def list_parsers(_: CurrentUser):
    return [_out(p) for p in parsers.PARSERS.values()]


@router.post("/{name}/test", response_model=ParseTestOut)
async def test_parser(name: str, body: ParseTestIn, _: CurrentUser):
    """Run a parser on sample input. Problems come back in the body, not as HTTP errors,
    so the Parsers page can show them inline while the user types."""
    parser = parsers.get(name)
    if parser is None:
        raise HTTPException(404, "Parser not found")
    data = body.input.encode()
    if len(data) > MAX_TEST_BYTES:
        raise HTTPException(413, f"Test input is limited to {MAX_TEST_BYTES // (1024 * 1024)} MB")
    try:
        options = parser.configure(body.options)
    except parsers.OptionsError as exc:
        return ParseTestOut(type=parser.type, error="Fix the options above", option_errors=exc.errors)
    started = time.perf_counter()
    try:
        value = await asyncio.to_thread(run_parser, parser, options, [data])
    except parsers.ParseError as exc:
        return ParseTestOut(type=parser.type, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a parser bug: show it rather than a bare 500
        return ParseTestOut(type=parser.type, error=f"parser error: {type(exc).__name__}: {exc}")
    elapsed = (time.perf_counter() - started) * 1000
    return ParseTestOut(type=parser.type, value=value, elapsed_ms=round(elapsed, 1))
