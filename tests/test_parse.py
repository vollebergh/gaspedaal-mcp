"""Tests voor parse.py met synthetische flight-payloads (geen echte site-HTML)."""

from __future__ import annotations

import pytest
from conftest import make_occasion, make_occasions_payload, make_page

from gaspedaal_mcp import parse


def test_extract_across_chunk_boundaries():
    payload = make_occasions_payload([make_occasion(1)])
    html = make_page(payload, chunks=7)
    data = parse.parse_all(html)
    assert data["total"] == 1
    assert data["listings"][0]["id"] == 1


def test_undefined_and_references():
    occ = make_occasion(1)
    occ["autogegevens"]["algemeen"]["uitvoering"] = "$undefined"
    occ["autogegevens"]["algemeen"]["modelnaam"] = "$6:6:props:foo"
    payload = make_occasions_payload([occ])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["version"] is None
    assert listing["model"] is None


def test_double_dollar_is_literal():
    occ = make_occasion(1)
    occ["autogegevens"]["algemeen"]["uitvoering"] = "$$literal"
    payload = make_occasions_payload([occ])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["version"] == "$literal"


def test_hits_block_ignored():
    payload = make_occasions_payload([make_occasion(1)])
    payload["hits"] = [{"advertentieId": 999}]
    html = make_page(payload)
    data = parse.parse_all(html)
    assert len(data["listings"]) == 1
    assert data["listings"][0]["id"] == 1


def test_missing_fields():
    occ = {
        "advertentieId": 2,
        "datumGeplaatst": "2026-11-02",
    }
    payload = make_occasions_payload([occ])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["id"] == 2
    assert listing["brand"] is None
    assert listing["price_eur"] is None
    assert listing["offers"] == []


def test_count_from_label_with_thousand_separator():
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={
            "merken": [
                {"slug": "toyota", "name": "Toyota", "label": "Toyota (16.724)"},
            ]
        },
    )
    html = make_page(payload)
    brands = parse.list_brands_from_page(html)
    assert brands[0]["count"] == 16724


def test_list_brands_skips_group_headers_and_sorts():
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={
            "merken": [
                {"isGroupHeader": True, "value": "Populair"},
                {"slug": "bimmer", "name": "BMW", "label": "BMW (100)"},
                {"slug": "audi", "name": "Audi", "label": "Audi (200)"},
            ]
        },
    )
    html = make_page(payload)
    brands = parse.list_brands_from_page(html)
    assert [b["name"] for b in brands] == ["Audi", "BMW"]


def test_format_error():
    html = make_page({"niet": "van toepassing"})
    with pytest.raises(ValueError, match="Unexpected response format"):
        parse.parse_all(html)


def test_power_hp_derived_from_kw():
    occ = make_occasion(1)
    occ["autogegevens"]["motor"]["vermogenKw"] = 160
    payload = make_occasions_payload([occ])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["power_kw"] == 160
    assert listing["power_hp"] == round(160 * 1.35962)


def test_location_combines_place_and_province():
    payload = make_occasions_payload([make_occasion(1)])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["location"] == "Woerden (UT)"


def test_photo_url_always_present():
    payload = make_occasions_payload([make_occasion(1)])
    html = make_page(payload)
    listing = parse.parse_all(html)["listings"][0]
    assert listing["photo_url"] == "https://example.invalid/foto.jpg"


def test_list_filters_options_use_id_field():
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={
            "opties": [{"value": "421", "label": "Trekhaak (123)"}],
        },
        filters={"zoekopties": [{"id": "421", "name": "Trekhaak", "attribute": "Veiligheid"}]},
    )
    html = make_page(payload)
    result = parse.parse_list_filters(html)
    opts = result["options"]
    assert opts[0]["id"] == "421"
    assert opts[0]["name"] == "Trekhaak"
    assert opts[0]["count"] == 123


def test_list_filters_has_param_field():
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={
            "brandstofsoort": [{"value": "BENZINE", "label": "Benzine (10)"}],
        },
        filters={"bouwjaar": [2015, 2020], "locatie": [5, 10], "aanbodSinds": [1, 3]},
    )
    html = make_page(payload)
    result = parse.parse_list_filters(html)
    assert result["fuel"]["param"] == "brnst"
    assert result["fuel"]["values"][0]["value"] == "BENZINE"
    assert result["year"]["param"] == "bmin/bmax"
    assert result["year"]["steps"] == [2015, 2020]
    assert result["radius_km"] == {"param": "strl", "steps": [5, 10]}
    assert result["days_listed"] == {"param": "dagen", "steps": [1, 3]}


def test_find_value_skips_key_as_value():
    # "occasions" als *waarde* moet niet als sleutel worden aangemerkt
    text = '{"foo": "occasions", "occasions": [{"advertentieId": 7}]}'
    value, _ = parse._find_value(text, "occasions")
    assert value == [{"advertentieId": 7}]


def test_find_value_decode_error_returns_none():
    text = '{"occasions": [unclosed'
    assert parse._find_value(text, "occasions") is None
