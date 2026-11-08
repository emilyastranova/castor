"""castor-mcp: Castor's MCP server for clients that only speak stdio (Claude Desktop).

Reads JSON-RPC messages from stdin, one per line, forwards each to <server>/api/mcp and
writes the replies to stdout. The token comes from CASTOR_TOKEN (never an argument, so it
stays out of the process list). Standard library only, like the rest of the agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def forward(url: str, token: str, line: str, timeout: float = 120) -> str | None:
    """Castor's reply to one message; None for a notification. Errors become JSON-RPC
    errors, so the client sees why instead of hanging."""
    request = urllib.request.Request(url, data=line.encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode() or None  # 202 (a notification) has no body
    except urllib.error.HTTPError as exc:
        problem = f"Castor answered HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}"
    except (urllib.error.URLError, OSError) as exc:
        problem = f"Can't reach Castor at {url}: {exc}"
    try:
        message_id = json.loads(line).get("id")
    except (ValueError, AttributeError):
        message_id = None
    print(f"castor-mcp: {problem}", file=sys.stderr)
    if message_id is None:
        return None
    return json.dumps({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": problem}})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="castor-mcp", description="Castor's MCP server over stdio. Set CASTOR_TOKEN to an API token.")
    parser.add_argument("--server", default=os.environ.get("CASTOR_URL"),
                        help="Castor's address, e.g. https://castor.example.internal (default: $CASTOR_URL)")
    args = parser.parse_args(argv)
    token = os.environ.get("CASTOR_TOKEN", "")
    if not args.server or not token:
        print("castor-mcp: set CASTOR_TOKEN and --server (or CASTOR_URL)", file=sys.stderr)
        return 2
    url = args.server.rstrip("/") + "/api/mcp"
    # shortcut: one message at a time, so a slow tool call delays the next message; fine for one assistant.
    for line in sys.stdin:
        if line.strip():
            reply = forward(url, token, line.strip())
            if reply:
                sys.stdout.write(reply + "\n")
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
