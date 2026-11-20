import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from gaspedaal_mcp import serve

TOKEN = "t" * 20 + "-secret_" + "x" * 8  # 36 allowed characters
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_defaults_are_stdio_and_local():
    args = serve.parse_args([])
    assert args.http is False and args.port == 8000 and args.public_host == []


@pytest.mark.parametrize("bad", ["https://x.example", "x.example/path", "x.example:443", "-x.example", "a b"])
def test_public_host_must_be_a_bare_hostname(bad):
    with pytest.raises(SystemExit):
        serve.parse_args(["--http", "--public-host", bad])


@pytest.mark.parametrize("bad", ["0", "65536", "-1"])
def test_port_range(bad):
    with pytest.raises(SystemExit):
        serve.parse_args(["--port", bad])


def test_token(monkeypatch):
    monkeypatch.delenv("GASPEDAAL_MCP_HTTP_TOKEN", raising=False)
    first, second = serve.http_token(), serve.http_token()
    assert first != second and serve.TOKEN.match(first)
    monkeypatch.setenv("GASPEDAAL_MCP_HTTP_TOKEN", "short")
    with pytest.raises(SystemExit):
        serve.http_token()
    monkeypatch.setenv("GASPEDAAL_MCP_HTTP_TOKEN", "../" + "a" * 30)
    with pytest.raises(SystemExit):
        serve.http_token()


def test_security_allows_localhost_and_public_hosts_only():
    s = serve.security(["mcp.example.test"])
    assert s.enable_dns_rebinding_protection
    assert "mcp.example.test" in s.allowed_hosts and "mcp.example.test:*" in s.allowed_hosts
    assert all(o.startswith("http://") for o in s.allowed_origins)  # no remote browser origins


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ | {"GASPEDAAL_MCP_HTTP_TOKEN": TOKEN, "GASPEDAAL_MCP_CACHE_MB": "0"}
    code = "from gaspedaal_mcp.server import main; main()"
    args = [sys.executable, "-c", code, "--http", "--port", str(port), "--public-host", "mcp.example.test"]
    proc = subprocess.Popen(args, env=env, stderr=subprocess.PIPE, text=True)  # noqa: S603
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url, timeout=0.2)
            break
        except httpx.TransportError:
            time.sleep(0.1)
    yield url, proc
    proc.terminate()
    proc.wait(timeout=10)


def _post(url, path, **headers):
    return httpx.post(url + path, json=INIT, headers=HEADERS | headers, timeout=10)


def test_http_serves_on_secret_path(http_server):
    url, _ = http_server
    r = _post(url, f"/{TOKEN}/mcp")
    assert r.status_code == 200 and '"name":"gaspedaal"' in r.text


def test_http_tunnel_host_is_accepted(http_server):
    url, _ = http_server
    assert _post(url, f"/{TOKEN}/mcp", Host="mcp.example.test").status_code == 200


@pytest.mark.parametrize("path", ["/mcp", f"/{TOKEN[:-1]}/mcp", "/"])
def test_http_without_the_secret_path_is_404(http_server, path):
    url, _ = http_server
    assert _post(url, path).status_code == 404


def test_http_foreign_host_and_origin_are_refused(http_server):
    url, _ = http_server
    assert _post(url, f"/{TOKEN}/mcp", Host="evil.example").status_code == 421
    assert _post(url, f"/{TOKEN}/mcp", Origin="https://evil.example").status_code == 403


def test_http_large_request_body_is_refused(http_server):
    url, _ = http_server
    big = INIT | {"params": INIT["params"] | {"padding": "x" * (serve.MAX_BODY + 1)}}
    r = httpx.post(url + f"/{TOKEN}/mcp", json=big, headers=HEADERS, timeout=10)
    assert r.status_code == 413


# Last: stops the shared server to read its output.
def test_http_secret_path_is_not_logged(http_server):
    url, proc = http_server
    _post(url, f"/{TOKEN}/mcp")
    proc.terminate()
    _, err = proc.communicate(timeout=10)
    assert TOKEN not in err and f"/{TOKEN[:4]}…/mcp" in err
