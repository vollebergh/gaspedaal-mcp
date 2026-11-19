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


async def test_allowed_host():
    c = _client(_ok_handler)
    html = await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert html == "<html>x</html>"


async def test_blocks_other_host():
    c = _client(_ok_handler)
    with pytest.raises(ClientError):
        await c.get_html("https://evil.example.com/zoeken")


async def test_blocks_redirect_to_other_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.gaspedaal.nl":
            return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
        return httpx.Response(200, text="evil")

    c = _client(handler)
    with pytest.raises(ClientError):
        await c.get_html("https://www.gaspedaal.nl/start")


async def test_blocks_proxy_redirect_path():
    c = _client(_ok_handler)
    with pytest.raises(ClientError):
        await c.get_html("https://www.gaspedaal.nl/api/proxy/redirect/vehicle/1")


async def test_429_retry_after_seconds():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    c = _client(handler)
    with pytest.raises(ClientError, match="pausing 30 s") as exc_info:
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert exc_info.value.retry_after == 30


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


async def test_403_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="challenge")

    c = _client(handler)
    with pytest.raises(ClientError, match="blocked the request"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


async def test_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    c = _client(handler)
    with pytest.raises(ClientError, match="Unknown brand or model"):
        await c.get_html("https://www.gaspedaal.nl/onbekend")


async def test_size_limit():
    big = "a" * (9 * 1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)

    c = _client(handler)
    with pytest.raises(ClientError, match="Response too large"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


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
    with open(f) as fh:
        data = _json.load(fh)
    data["_cached_at"] = time.time() - 99999
    with open(f, "w") as fh:
        _json.dump(data, fh)

    result = await c.get_parsed(url, 3600, lambda html: {"v": "fresh"})
    assert result == {"v": "fresh"}


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


async def test_cache_filename_is_sha256():
    cache = DiskCache(max_mb=10)
    long_key = "https://www.gaspedaal.nl/zoeken?" + "x" * 500
    path = cache._path(long_key)
    assert len(path.name) == 64 + len(".json")


async def test_blocked_until_remembered():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"})

    c = _client(handler)
    with pytest.raises(ClientError, match="pausing 60 s"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")
    # tweede verzoek moet nu direct de "paused for another" fout geven
    with pytest.raises(ClientError, match="paused for another"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


async def test_throttle_waits_between_requests(monkeypatch):
    import gaspedaal_mcp.client as client_mod

    clock = {"t": 1000.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(client_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    c = _client(_ok_handler)
    await c.get_html("https://www.gaspedaal.nl/zoeken")
    clock["t"] += 0.25
    await c.get_html("https://www.gaspedaal.nl/zoeken")
    assert sleeps == [pytest.approx(0.75)]


async def test_503_pauses_like_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    c = _client(handler)
    with pytest.raises(ClientError, match=r"HTTP 503\); pausing 300 s"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


async def test_cache_key_contains_version(tmp_path, monkeypatch):
    """CR-01: een andere versie leest geen entry van een vorige versie."""
    import gaspedaal_mcp.client as client_mod

    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, text="<html>x</html>")

    cache = DiskCache(directory=tmp_path, max_mb=10, max_days=7)
    url = "https://www.gaspedaal.nl/zoeken"
    monkeypatch.setattr(client_mod, "__version__", "0.1.0")
    await _client(handler, cache=cache).get_parsed(url, 3600, lambda html: {"v": 1}, namespace="x")
    monkeypatch.setattr(client_mod, "__version__", "0.2.0")
    result = await _client(handler, cache=cache).get_parsed(url, 3600, lambda html: {"v": 2}, namespace="x")
    assert result == {"v": 2}
    assert hits["n"] == 2


def test_cache_file_and_dir_permissions(tmp_path):
    import stat

    directory = tmp_path / "cache"
    cache = DiskCache(directory=directory, max_mb=10, max_days=7)
    cache.set("k", {"v": 1})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache._path("k").stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["{not json", "[]", '{"value": [1]}', '{"value": {"a": 1}}'])
def test_cache_corrupt_entry_is_a_miss(tmp_path, content):
    cache = DiskCache(directory=tmp_path, max_mb=10, max_days=7)
    cache._path("k").write_text(content)
    assert cache.get("k", 3600) is None
    assert not cache._path("k").exists()


def test_cache_evicts_oldest_over_size_and_too_old(tmp_path):
    import os
    import time

    cache = DiskCache(directory=tmp_path, max_mb=1, max_days=7)
    blob = {"v": "x" * 400_000}  # ± 0,4 MB per entry
    for i, key in enumerate(("a", "b", "c")):
        cache.set(key, blob)
        t = time.time() - 100 + i  # a oudst, c nieuwst
        os.utime(cache._path(key), (t, t))
    cache.set("d", blob)  # nu ± 1,6 MB: de oudste gaan eruit tot het past
    assert not cache._path("a").exists()
    assert not cache._path("b").exists()
    assert cache._path("c").exists() and cache._path("d").exists()

    old = time.time() - 8 * 86400
    os.utime(cache._path("c"), (old, old))
    cache.set("e", {"v": 1})
    assert not cache._path("c").exists()


# --- Security review B-08 -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gaspedaal.nl:8443/zoeken",
        "https://www.gaspedaal.nl//api/proxy/redirect/vehicle/1",
        "https://www.gaspedaal.nl/zoeken/../api/proxy/redirect/vehicle/1",
        "https://www.gaspedaal.nl/API/Proxy/Redirect/vehicle/1",
        "https://www.gaspedaal.nl/api/proxy/%2572edirect/vehicle/1".replace("%25", "%"),
    ],
)
async def test_hook_blocks_port_and_path_tricks(url):
    """S-03."""
    c = _client(_ok_handler)
    with pytest.raises(ClientError):
        await c.get_html(url)


async def test_redirect_to_forbidden_path_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "//www.gaspedaal.nl//api/proxy/redirect/v/1"})
        return httpx.Response(200, text="should not be fetched")

    with pytest.raises(ClientError, match="forbidden path"):
        await _client(handler).get_html("https://www.gaspedaal.nl/start")


async def test_redirect_chain_is_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={"location": f"/loop?n={n + 1}"})

    with pytest.raises(ClientError, match="Request to Gaspedaal failed"):
        await _client(handler).get_html("https://www.gaspedaal.nl/loop")


async def test_total_deadline(monkeypatch):
    """S-06: een trage server houdt de lock niet onbeperkt vast."""
    import asyncio

    import gaspedaal_mcp.client as client_mod

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.05)
                yield b"x"

    monkeypatch.setattr(client_mod, "DEADLINE", 0.3)
    c = _client(lambda request: httpx.Response(200, stream=Slow()))
    with pytest.raises(ClientError, match="no complete answer within"):
        await c.get_html("https://www.gaspedaal.nl/zoeken")


def test_cache_refuses_shared_directory(tmp_path):
    """S-02: een door anderen beschrijfbare map wordt niet gebruikt."""
    import os

    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)  # noqa: S103 - bewust: een gedeelde map nabootsen
    cache = DiskCache(directory=shared, max_mb=10, max_days=7)
    cache.set("k", {"v": 1})
    assert list(shared.iterdir()) == []
    assert cache.get("k", 3600) is None


def test_cache_write_does_not_follow_symlink(tmp_path):
    """S-02: een neergezet symlink op de oude vaste tmp-naam wordt niet gevolgd."""
    directory = tmp_path / "cache"
    cache = DiskCache(directory=directory, max_mb=10, max_days=7)
    cache.set("warm", {"v": 0})  # map aanmaken
    victim = tmp_path / "victim.txt"
    victim.write_text("belangrijk")
    cache._path("k").with_suffix(".tmp").symlink_to(victim)
    cache.set("k", {"v": 1})
    assert victim.read_text() == "belangrijk"
    assert cache.get("k", 3600) == {"v": 1}
