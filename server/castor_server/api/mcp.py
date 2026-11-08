"""POST /api/mcp: the built-in MCP server (mcp.py). A bearer token is required (an API
token, or a session JWT sent as one); cookies aren't accepted, so a web page can't drive it
with someone's session."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import mcp
from ..deps import CurrentUser

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.post("")
async def mcp_endpoint(request: Request, user: CurrentUser):
    """MCP over Streamable HTTP (JSON replies, no sessions). Tools follow the token's scopes."""
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Send a token as Authorization: Bearer", headers={"WWW-Authenticate": "Bearer"})
    try:
        body = json.loads(await request.body())
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    if isinstance(body, list):
        replies = [r for r in [await mcp.handle(request.app, authorization, user, msg) for msg in body] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await mcp.handle(request.app, authorization, user, body)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@router.get("")
async def mcp_stream():
    """No server-initiated stream: replies come back on the POST."""
    return Response(status_code=405, headers={"Allow": "POST"})
