"""Command line: stdio (default) or an optional streamable-HTTP mode for remote MCP clients.

HTTP mode is meant for clients that only talk to remote servers (such as ChatGPT connectors,
which support OAuth or no authentication). It listens on 127.0.0.1 only; expose it through a
tunnel or reverse proxy of your choice. Access control is a secret path segment: anyone who
knows the full URL can use the server, so treat the URL like a password.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

TOKEN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
MAX_SESSIONS = 32  # SDK default is 10,000; this is a personal server
MAX_BODY = 64 * 1024  # tool arguments are small; SDK default is 4 MiB


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gaspedaal-mcp", description="MCP server for the used-car listings of www.gaspedaal.nl."
    )
    p.add_argument("--http", action="store_true", help="serve streamable HTTP on 127.0.0.1 instead of stdio")
    p.add_argument("--port", type=int, default=int(os.environ.get("GASPEDAAL_MCP_HTTP_PORT", "8000")))
    p.add_argument(
        "--public-host",
        action="append",
        default=[h for h in os.environ.get("GASPEDAAL_MCP_PUBLIC_HOST", "").split(",") if h],
        metavar="HOST",
        help="hostname of your tunnel or proxy (repeatable); requests with another Host are refused",
    )
    args = p.parse_args(argv)
    if not 1 <= args.port <= 65535:
        p.error("--port must be 1-65535")
    args.public_host = [h.strip().lower() for h in args.public_host]
    for host in args.public_host:
        if not HOSTNAME.match(host):
            p.error(f"invalid --public-host {host!r}: give a bare hostname such as mcp.example.com")
    return args


def http_token() -> str:
    """The secret path segment: GASPEDAAL_MCP_HTTP_TOKEN, or a new random one per start."""
    token = os.environ.get("GASPEDAAL_MCP_HTTP_TOKEN") or secrets.token_urlsafe(32)
    if not TOKEN.match(token):
        raise SystemExit("GASPEDAAL_MCP_HTTP_TOKEN must be 24-128 characters of A-Z a-z 0-9 _ -")
    return token


def security(public_hosts: list[str]) -> TransportSecuritySettings:
    """Host header allowlist (DNS-rebinding protection): localhost plus the given public hosts."""
    hosts = LOCAL_HOSTS + [h for host in public_hosts for h in (host, f"{host}:*")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=LOCAL_ORIGINS
    )


def run_http(mcp: FastMCP, args: argparse.Namespace) -> None:  # pragma: no cover - exercised by test_http
    import uvicorn

    token = http_token()
    mcp.settings.streamable_http_path = f"/{token}/mcp"
    mcp.settings.transport_security = security(args.public_host)
    mcp.settings.max_sessions = MAX_SESSIONS
    mcp.settings.max_request_body_size = MAX_BODY
    fixed = bool(os.environ.get("GASPEDAAL_MCP_HTTP_TOKEN"))
    shown = f"/{token[:4]}…/mcp" if fixed else f"/{token}/mcp"
    print(f"gaspedaal-mcp: streamable HTTP on http://127.0.0.1:{args.port}{shown}", file=sys.stderr)
    for host in args.public_host:
        print(f"gaspedaal-mcp: public URL https://{host}{shown}", file=sys.stderr)
    if not os.environ.get("GASPEDAAL_MCP_HTTP_TOKEN"):
        print(
            "gaspedaal-mcp: random path for this run; set GASPEDAAL_MCP_HTTP_TOKEN to keep it",
            file=sys.stderr,
        )
    # No access log: it would write the secret path of every request to the log.
    uvicorn.run(
        mcp.streamable_http_app(), host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
    )
