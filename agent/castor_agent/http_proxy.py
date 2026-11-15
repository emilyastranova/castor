"""HTTP actions sent through this agent (docs/ACTIONS.md phase 2).

The server keeps everything about an HTTP action (paging, retries, rate limits, progress); the
agent only makes single requests from its own network, when the server sends `http.request`,
and answers with `http.chunk` messages (the body, base64, in pieces) and one `http.response`.
Redirects are not followed here: the server decides about each hop. stdlib only."""

from __future__ import annotations

import base64
import socket
import urllib.error
import urllib.request
from typing import Any

CHUNK = 512 * 1024  # body bytes per http.chunk message (the server's socket takes up to 16 MB)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # hand the 3xx back as it is


# The machine's own proxy settings (HTTPS_PROXY, ...) apply: the request goes out the way
# anything else on this network would.
_OPENER = urllib.request.build_opener(_NoRedirects)


def fetch(msg: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Make one request; returns (the response's head, its body). Errors come back in the
    head as {"error", "error_kind": timeout|connect} rather than raised."""
    method = str(msg.get("method") or "GET").upper()
    body = base64.b64decode(msg["body"]) if msg.get("body") else None
    req = urllib.request.Request(str(msg["url"]), data=body, method=method,
                                 headers={"User-Agent": "castor", **(msg.get("headers") or {})})
    limit = int(msg.get("max_bytes") or 50 * 1024 * 1024)
    timeout = float(msg.get("timeout_s") or 60)
    try:
        resp = _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:  # 4xx/5xx (and unfollowed 3xx) are answers, not errors
        resp = exc
    except TimeoutError:
        return {"error": f"timed out after {timeout:g} s", "error_kind": "timeout"}, b""
    except urllib.error.URLError as exc:
        reason = exc.reason
        kind = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "connect"
        return {"error": str(reason), "error_kind": kind}, b""
    except (OSError, ValueError) as exc:
        return {"error": str(exc), "error_kind": "connect"}, b""
    try:
        data = resp.read(limit + 1)
    except TimeoutError:
        return {"error": f"timed out reading the response after {timeout:g} s", "error_kind": "timeout"}, b""
    except OSError as exc:
        return {"error": str(exc), "error_kind": "connect"}, b""
    finally:
        resp.close()
    if len(data) > limit:
        return {"error": f"the response is over {limit // (1024 * 1024)} MB", "error_kind": "too_big"}, b""
    head = {
        "status": resp.status if hasattr(resp, "status") else resp.code,
        "reason": getattr(resp, "reason", "") or "",
        "headers": [[k, v] for k, v in resp.headers.items()],
        "url": resp.geturl() if hasattr(resp, "geturl") else str(msg["url"]),
    }
    return head, data


def chunks(data: bytes) -> list[str]:
    return [base64.b64encode(data[i:i + CHUNK]).decode() for i in range(0, len(data), CHUNK)]
