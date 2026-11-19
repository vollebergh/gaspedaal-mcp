"""Tests voor server.py met gemockte client."""

from __future__ import annotations

import pytest
from conftest import make_occasion, make_occasions_payload, make_page

from gaspedaal_mcp import server


class _FakeClient:
    def __init__(self, html):
        self.html = html

    async def get_parsed(self, url, ttl_seconds, parser, namespace=""):
        return parser(self.html)

    def close(self):
        pass


def _patch_client(monkeypatch, html):
    monkeypatch.setattr(server, "_client", _FakeClient(html))
    monkeypatch.setattr(server, "_memory", server._ListingMemory())


@pytest.mark.asyncio
async def test_search(monkeypatch):
    payload = make_occasions_payload([make_occasion(i) for i in range(1, 6)])
    _patch_client(monkeypatch, make_page(payload))

    result = await server.search(keyword="x", max_results=3, filters=None)
    assert result["returned"] == 3
    assert result["truncated"] is True
    assert len(result["listings"]) == 3


@pytest.mark.asyncio
async def test_get_listing_no_network(monkeypatch):
    payload = make_occasions_payload([make_occasion(42)])
    _patch_client(monkeypatch, make_page(payload))
    await server.search(keyword="x", filters=None)
    listing = await server.get_listing(42)
    assert listing["id"] == 42


@pytest.mark.asyncio
async def test_get_listing_unknown(monkeypatch):
    monkeypatch.setattr(server, "_memory", server._ListingMemory())
    with pytest.raises(ValueError, match="not seen in this session"):
        await server.get_listing(12345)


@pytest.mark.asyncio
async def test_market_summary_quantiles(monkeypatch):
    occasions = []
    for i in range(1, 5):
        occ = make_occasion(i)
        occ["prijs"]["totaalInclusiefBtw"] = i * 1000
        occasions.append(occ)
    payload = make_occasions_payload(occasions, total=4, pages=1)
    _patch_client(monkeypatch, make_page(payload))

    result = await server.market_summary(keyword="x", filters=None, max_pages=1)
    p = result["price"]
    assert p["min"] == 1000
    assert p["max"] == 4000
    assert p["median"] == 2500


@pytest.mark.asyncio
async def test_search_and_market_summary_share_cache(monkeypatch):
    seen = []

    class _Recorder(_FakeClient):
        async def get_parsed(self, url, ttl_seconds, parser, namespace=""):
            seen.append((namespace, url, ttl_seconds))
            return parser(self.html)

    payload = make_occasions_payload([make_occasion(1)], total=1, pages=1)
    monkeypatch.setattr(server, "_client", _Recorder(make_page(payload)))
    monkeypatch.setattr(server, "_memory", server._ListingMemory())
    await server.search(brand="toyota", filters=None)
    await server.market_summary(brand="toyota", filters=None, max_pages=1)
    assert seen[0] == seen[1]


@pytest.mark.asyncio
async def test_list_brands(monkeypatch):
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={"merken": [{"slug": "toyota", "name": "Toyota", "label": "Toyota (10)"}]},
    )
    _patch_client(monkeypatch, make_page(payload))
    brands = await server.list_brands()
    assert brands[0]["slug"] == "toyota"
