"""Tests voor server.py met gemockte client."""

from __future__ import annotations

from collections import OrderedDict

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
    monkeypatch.setattr(server, "_memory", OrderedDict())


async def test_search(monkeypatch):
    payload = make_occasions_payload([make_occasion(i) for i in range(1, 6)])
    _patch_client(monkeypatch, make_page(payload))

    result = await server.search(keyword="x", max_results=3, filters=None)
    assert result["returned"] == 3
    assert result["truncated"] is True
    assert len(result["listings"]) == 3


async def test_get_listing_no_network(monkeypatch):
    payload = make_occasions_payload([make_occasion(42)])
    _patch_client(monkeypatch, make_page(payload))
    await server.search(keyword="x", filters=None)
    listing = await server.get_listing(42)
    assert listing["id"] == 42


async def test_get_listing_unknown(monkeypatch):
    monkeypatch.setattr(server, "_memory", OrderedDict())
    with pytest.raises(ValueError, match="not seen in this session"):
        await server.get_listing(12345)


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


async def test_search_and_market_summary_share_cache(monkeypatch):
    seen = []

    class _Recorder(_FakeClient):
        async def get_parsed(self, url, ttl_seconds, parser, namespace=""):
            seen.append((namespace, url, ttl_seconds))
            return parser(self.html)

    payload = make_occasions_payload([make_occasion(1)], total=1, pages=1)
    monkeypatch.setattr(server, "_client", _Recorder(make_page(payload)))
    monkeypatch.setattr(server, "_memory", OrderedDict())
    await server.search(brand="toyota", filters=None)
    await server.market_summary(brand="toyota", filters=None, max_pages=1)
    assert seen[0] == seen[1]


async def test_list_brands(monkeypatch):
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={"merken": [{"slug": "toyota", "name": "Toyota", "label": "Toyota (10)"}]},
    )
    _patch_client(monkeypatch, make_page(payload))
    brands = await server.list_brands()
    assert brands[0]["slug"] == "toyota"


async def test_market_summary_stops_at_last_page_and_single_price(monkeypatch):
    calls = []

    class _Counter(_FakeClient):
        async def get_parsed(self, url, ttl_seconds, parser, namespace=""):
            calls.append(url)
            return parser(self.html)

    payload = make_occasions_payload([make_occasion(1)], total=1, pages=1)
    monkeypatch.setattr(server, "_client", _Counter(make_page(payload)))
    result = await server.market_summary(keyword="x", filters=None, max_pages=5)
    assert len(calls) == 1
    assert result["price"]["p25"] == result["price"]["median"] == result["price"]["p75"]
    assert "note" not in result


async def test_list_models_and_list_filters_tools(monkeypatch):
    seen = []

    class _Recorder(_FakeClient):
        async def get_parsed(self, url, ttl_seconds, parser, namespace=""):
            seen.append((namespace, url, ttl_seconds))
            return parser(self.html)

    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={"modellen": [{"slug": "rav4", "name": "RAV4", "label": "RAV4 (3)"}]},
        filters={"bouwjaar": [2015, 2020]},
    )
    monkeypatch.setattr(server, "_client", _Recorder(make_page(payload)))
    models = await server.list_models("toyota")
    filters = await server.list_filters("toyota")
    assert models == [{"slug": "rav4", "name": "RAV4", "count": 3}]
    assert filters["year"] == {"param": "bmin/bmax", "steps": [2015, 2020]}
    assert filters["sort_options"][0] == "relevance"
    assert [s[0] for s in seen] == ["models", "filters"]
    assert seen[0][1] == seen[1][1]  # zelfde URL, gescheiden door de namespace
    assert seen[0][2] == server.TTL_LISTS


async def test_include_photos_and_get_listing(monkeypatch):
    _patch_client(monkeypatch, make_page(make_occasions_payload([make_occasion(7)])))
    without = await server.search(keyword="x")
    with_photos = await server.search(keyword="x", include_photos=True)
    assert "photo_url" not in without["listings"][0]
    assert with_photos["listings"][0]["photo_url"]
    assert (await server.get_listing(7))["photo_url"]


def test_listing_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(server, "_memory", OrderedDict())
    monkeypatch.setattr(server, "MAX_REMEMBERED", 3)
    server._remember([{"id": i} for i in range(5)])
    assert list(server._memory) == [2, 3, 4]


def test_summarize_is_pure():
    listings = [
        {"price_eur": 1000, "mileage_km": 10, "year": 2020, "fuel": "BENZINE", "transmission": "AUTOMATISCH"},
        {"price_eur": None, "mileage_km": 30, "year": 2020, "fuel": "DIESEL", "transmission": None},
        {"price_eur": 3000, "mileage_km": 20, "year": 2021, "fuel": "BENZINE", "transmission": "AUTOMATISCH"},
    ]
    s = server.summarize(listings, total=10)
    assert s["price"] == {"min": 1000, "p25": 1500.0, "median": 2000.0, "p75": 2500.0, "max": 3000}
    assert s["mileage_km"] == {"median": 20}
    assert s["by_year"][0] == {"year": 2020, "n": 2, "median_price": 1000, "median_mileage": 20.0}
    assert s["by_fuel"] == {"BENZINE": 2, "DIESEL": 1}
    assert s["note"].startswith("Sample of the first 3 listings")


def test_httpx_does_not_log_urls():
    """S-05: zoektermen en postcode niet in de logbestanden van de MCP-client."""
    import logging

    assert logging.getLogger("httpx").level == logging.WARNING


async def test_unknown_slugs_rejected_by_every_tool(monkeypatch):
    catalog = {"merkModel": [{"slug": "toyota", "models": [{"slug": "rav4"}]}]}
    _patch_client(monkeypatch, make_page(make_occasions_payload(filters=catalog)))
    for call in (
        server.search(brand="tesler"),
        server.market_summary(brand="tesler", max_pages=1),
        server.list_models("tesler"),
        server.list_filters("tesler"),
    ):
        with pytest.raises(ValueError, match="Unknown brand 'tesler'"):
            await call
    with pytest.raises(ValueError, match="Unknown model 'rav5'"):
        await server.search(brand="toyota", model="rav5")
    assert (await server.search(brand="toyota", model="rav4"))["returned"] == 1
