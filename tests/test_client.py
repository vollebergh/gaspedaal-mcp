"""Tests voor client.py met httpx.MockTransport."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from gaspedaal_mcp.cache import DiskCache
from gaspedaal_mcp.client import ClientError, GaspedaalClient


def _client(handler, cache=None):
    transport = httpx.MockTransport(handler)
    c = GaspedaalClient(
        cache=cache if cache is not None else DiskCache(max_mb=0),
        transport=transport,
    )
    return c


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>x</html>")


@pytest.mark.asyncio
async def test_allowed_host():
    c = _client(_ok_handler)
    html = await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert html == "<html>x</html>"


@pytest.mark.asyncio
async def test_blocks_other_host():
    c = _client(_ok_handler)
    with pytest.raises(ClientError):
        await c.get_html("https://evil.example.com/zoeken")


@pytest.mark.asyncio
async def test_blocks_redirect_to_other_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.gaspedaal.nl":
            return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
        return httpx.Response(200, text="evil")

    c = _client(handler)
    with pytest.raises(ClientError):
        await c.get_html("https://www.gaspedaal.nl/start")


@pytest.mark.asyncio
async def test_blocks_proxy_redirect_path():
    c = _client(_ok_handler)
    with pytest.raises(ClientError):
        await c.get_html("https://www.gaspedaal.nl/api/proxy/redirect/vehicle/1")


@pytest.mark.asyncio
async def test_429_retry_after_seconds():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    c = _client(handler)
    with pytest.raises(ClientError, match="pausing 30 s") as exc_info:
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert exc_info.value.retry_after == 30


@pytest.mark.asyncio
async def test_429_retry_after_http_date():
    import email.utils
    from datetime import datetime, timedelta

    future = datetime.now(UTC) + timedelta(seconds=120)
    date = email.utils.format_datetime(future)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": date})

    c = _client(handler)
    with pytest.raises(ClientError) as exc_info:
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert exc_info.value.retry_after is not None


@pytest.mark.asyncio
async def test_403_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="challenge")

    c = _client(handler)
    with pytest.raises(ClientError, match="blocked the request"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


@pytest.mark.asyncio
async def test_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    c = _client(handler)
    with pytest.raises(ClientError, match="Unknown brand or model"):
        await c.get_html("https://www.gaspedaal.nl/onbekend")


@pytest.mark.asyncio
async def test_size_limit():
    big = "a" * (9 * 1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    c = _client(handler)
    with pytest.raises(ClientError, match="Response too large"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


@pytest.mark.asyncio
async def test_size_limit_without_content_length_stops_reading():
    """Zonder Content-Length moet het lezen stoppen zodra de limiet is overschreden."""
    produced = {"bytes": 0}
    chunk = b"a" * (1024 * 1024)

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):  # 20 MB als de client alles zou lezen
                produced["bytes"] += len(chunk)
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Body())

    c = _client(handler)
    with pytest.raises(ClientError, match="Response too large"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert produced["bytes"] <= 9 * 1024 * 1024


@pytest.mark.asyncio
async def test_cache_hit(tmp_path):
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, text="<html>x</html>")

    cache = DiskCache(directory=tmp_path, max_mb=10, max_days=7)
    c = _client(handler, cache=cache)

    url = "https://www.gaspedaal.nl/zoeken"
    result1 = await c.get_parsed(url, 3600, lambda html: {"v": html})
    result2 = await c.get_parsed(url, 3600, lambda html: {"v": html})
    assert result1 == result2
    assert hits["n"] == 1


@pytest.mark.asyncio
async def test_cache_expired(tmp_path):
    import time

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>x</html>")

    cache = DiskCache(directory=tmp_path, max_mb=10, max_days=7)
    c = _client(handler, cache=cache)
    url = "https://www.gaspedaal.nl/zoeken"
    await c.get_parsed(url, 3600, lambda html: {"v": html})

    # verdraai de _cached_at zodat de TTL verlopen is
    import glob
    import json as _json

    f = glob.glob(str(tmp_path / "*.json"))[0]
    data = _json.loads("{}")  # placeholder
    with open(f) as fh:
        data = _json.load(fh)
    data["_cached_at"] = time.time() - 99999
    with open(f, "w") as fh:
        _json.dump(data, fh)

    result = await c.get_parsed(url, 3600, lambda html: {"v": "fresh"})
    assert result == {"v": "fresh"}


@pytest.mark.asyncio
async def test_cache_disabled_mb_zero():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, text="<html>x</html>")

    c = _client(handler, cache=DiskCache(max_mb=0))
    url = "https://www.gaspedaal.nl/zoeken"
    await c.get_parsed(url, 3600, lambda html: {"v": html})
    await c.get_parsed(url, 3600, lambda html: {"v": html})
    assert hits["n"] == 2


@pytest.mark.asyncio
async def test_cache_namespace_separates_entries(tmp_path):
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, text="<html>x</html>")

    cache = DiskCache(directory=tmp_path, max_mb=10, max_days=7)
    c = _client(handler, cache=cache)
    url = "https://www.gaspedaal.nl/toyota"
    await c.get_parsed(url, 3600, lambda html: {"kind": "models"}, namespace="models")
    await c.get_parsed(url, 3600, lambda html: {"kind": "search"}, namespace="search")
    assert hits["n"] == 2
    # zelfde namespace is wél een hit
    again = await c.get_parsed(url, 3600, lambda html: {"kind": "search2"}, namespace="search")
    assert again == {"kind": "search"}
    assert hits["n"] == 2


@pytest.mark.asyncio
async def test_cache_filename_is_sha256():
    cache = DiskCache(max_mb=10)
    long_key = "https://www.gaspedaal.nl/zoeken?" + "x" * 500
    path = cache._path(long_key)
    assert len(path.name) == 64 + len(".json")


@pytest.mark.asyncio
async def test_blocked_until_remembered():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"})

    c = _client(handler)
    with pytest.raises(ClientError, match="pausing 60 s"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    # tweede verzoek moet nu direct de "paused for another" fout geven
    with pytest.raises(ClientError, match="paused for another"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")
