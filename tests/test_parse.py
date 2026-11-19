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
    assert parse._find_value(text, "occasions") == [{"advertentieId": 7}]


def test_find_value_decode_error_returns_none():
    text = '{"occasions": [unclosed'
    assert parse._find_value(text, "occasions") is None


def test_text_fields_clipped_and_long_urls_dropped():
    """CR-02: alle tekst ≤ 200 tekens; een te lange URL wordt None in plaats van afgekapt."""
    occ = make_occasion(1)
    occ["aanbieder"]["aanbiedergegevens"]["naamsvermelding"] = "x" * 5000
    occ["portalen"][0]["portaalBeschrijving"] = "y" * 500
    occ["gpFotos"]["fotoKlein"] = "https://cdn.example.invalid/" + "z" * 3000
    listing = parse.parse_all(make_page(make_occasions_payload([occ])))["listings"][0]
    assert len(listing["seller_name"]) == parse.MAX_TEXT
    assert len(listing["offers"][0]["site"]) == parse.MAX_TEXT
    assert listing["offers"][0]["url"] == "https://www.gaspedaal.nl/api/proxy/redirect/vehicle/1"
    assert listing["photo_url"] is None


def test_bad_escape_in_fragment_is_format_error():
    """CR-03."""
    html = '<script>self.__next_f.push([1,"bad \\x escape"])</script>'
    with pytest.raises(ValueError, match="Unexpected response format"):
        parse.parse_all(html)


# --- Security review B-08 -------------------------------------------------------------


def test_push_regex_is_linear_on_unterminated_fragments():
    """S-01: fragmenten zonder afsluiter mogen niet kwadratisch worden (ReDoS)."""
    import time

    html = ('self.__next_f.push([1,"' + "a" * 20) * 40_000  # ± 1,7 MB
    t = time.perf_counter()
    with pytest.raises(ValueError, match="Unexpected response format"):
        parse.parse_all(html)
    assert time.perf_counter() - t < 2.0  # oude regex: uren


def test_push_regex_keeps_escaped_quotes():
    html = make_page(make_occasions_payload([make_occasion(1, datumGeplaatst='quote " en \\ slash')]))
    assert parse.parse_all(html)["listings"][0]["listed_at"] == 'quote " en \\ slash'


def test_deep_nesting_is_format_error():
    """S-06: absurd diep geneste JSON geeft de formaatfout, geen RecursionError."""
    import json

    depth = 100_000
    inner = '{"commonPortalURLProps":{},"occasions":' + "[" * depth + "]" * depth + "}"
    html = f"<script>self.__next_f.push([1,{json.dumps(inner)}])</script>"
    with pytest.raises(ValueError, match="Unexpected response format"):
        parse.parse_all(html)


@pytest.mark.parametrize(
    ("klik", "expected"),
    [
        ("/api/proxy/redirect/vehicle/9", "https://www.gaspedaal.nl/api/proxy/redirect/vehicle/9"),
        (
            "https://www.gaspedaal.nl/api/proxy/redirect/vehicle/9",
            "https://www.gaspedaal.nl/api/proxy/redirect/vehicle/9",
        ),
        ("https://evil.example/api/proxy/redirect/vehicle/9", None),
        ("javascript:alert(1)", None),
        ("http://www.gaspedaal.nl/api/proxy/redirect/vehicle/9", None),
        ("https://www.gaspedaal.nl/elders", None),
        ("//evil.example/api/proxy/redirect/vehicle/9", None),
    ],
)
def test_offer_url_only_gaspedaal_redirect(klik, expected):
    """S-04: de gebruiker opent offer-URL's zelf; alleen de eigen doorverwijzer van Gaspedaal."""
    occ = make_occasion(1)
    occ["portalen"][0]["klikUrl"] = klik
    occ["gpFotos"]["fotoKlein"] = "javascript:alert(1)"
    listing = parse.parse_all(make_page(make_occasions_payload([occ])))["listings"][0]
    assert listing["offers"][0]["url"] == expected
    assert listing["photo_url"] is None


def test_list_texts_are_clipped():
    """S-08: ook labels en namen in list_brands en list_filters zijn begrensd."""
    payload = make_occasions_payload(
        [make_occasion(1)],
        dynamicFieldValues={
            "merken": [{"slug": "x", "name": "N" * 1000, "label": "L (1)"}],
            "brandstofsoort": [{"value": "BENZINE", "label": "B" * 1000}],
        },
    )
    html = make_page(payload)
    assert len(parse.list_brands_from_page(html)[0]["name"]) == parse.MAX_TEXT
    assert len(parse.parse_list_filters(html)["fuel"]["values"][0]["label"]) == parse.MAX_TEXT
