"""Live-tests tegen www.gaspedaal.nl (marker ``live``, standaard uit).

Elk maakt hoogstens 2 verzoeken. Deze tests documenteren de onbevestigde
parameters; de uitkomst staat in de README (hoofdstuk *Voor taalmodellen*).
"""

from __future__ import annotations

import pytest

from gaspedaal_mcp.client import GaspedaalClient

pytestmark = pytest.mark.live


async def test_search_price_asc():
    from gaspedaal_mcp import parse

    client = GaspedaalClient()
    try:
        html = await client.get_html("https://www.gaspedaal.nl/toyota/rav4?srt=pr-a")
    finally:
        await client.close()
    data = parse.parse_all(html)
    assert data["total"] is not None and data["total"] > 0
    prices = [
        listing["price_eur"] for listing in data["listings"] if isinstance(listing["price_eur"], (int, float))
    ]
    assert prices == sorted(prices)


async def test_list_models_contains_rav4():
    from gaspedaal_mcp import parse

    client = GaspedaalClient()
    try:
        html = await client.get_html("https://www.gaspedaal.nl/toyota")
    finally:
        await client.close()
    models = parse.list_models_from_page(html)
    slugs = [m["slug"] for m in models if m.get("slug")]
    assert "rav4" in slugs


async def test_unconfirmed_param_recognized():
    """``tmin`` (trekgewicht): als de site de param herkent, daalt ``total`` strikt."""
    from gaspedaal_mcp import parse

    client = GaspedaalClient()
    try:
        base_html = await client.get_html("https://www.gaspedaal.nl/zoeken")
        base_total = parse.parse_all(base_html).get("total")
        filtered_html = await client.get_html("https://www.gaspedaal.nl/zoeken?tmin=2000")
        filtered_total = parse.parse_all(filtered_html).get("total")
    finally:
        await client.close()
    assert base_total is not None
    assert filtered_total is not None
    # Een strikte daling bewijst dat de site ``tmin`` daadwerkelijk herkent.
    assert filtered_total < base_total
