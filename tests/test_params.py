"""Tests voor params.py: URL-opbouw en validatie."""

from __future__ import annotations

import pytest

from gaspedaal_mcp.params import ValidationError, build_url, normalize_slug


def test_example_1_toyota_rav4():
    url = build_url(
        "toyota",
        "rav4",
        None,
        {
            "fuel": ["hybride", "benzine"],
            "postcode": "3511",
            "radius_km": 50,
            "days_listed": 14,
            "seller": "UNIVERSEEL_AUTOBEDRIJF",
            "power": {"min": 150},
            "seats": {"min": 5},
            "options": ["420"],
        },
        "distance",
        2,
    )
    assert url == (
        "https://www.gaspedaal.nl/toyota/rav4?vmin=150&zmin=5&brnst=HYBRIDE%2CBENZINE"
        "&opt=420&vrk=UNIVERSEEL_AUTOBEDRIJF&dagen=14&pc=3511&strl=50&srt=af-a&page=2"
    )


def test_example_2_volkswagen_golf():
    url = build_url(
        "volkswagen",
        "golf",
        None,
        {"fuel": "diesel", "body": "stationwagen", "price": {"max": 15000}},
        "price_desc",
        1,
    )
    assert url == (
        "https://www.gaspedaal.nl/volkswagen/golf?pmax=15000&brnst=DIESEL&crs=STATIONWAGEN&srt=pr-d"
    )


def test_example_3_keyword():
    url = build_url(
        None,
        None,
        "trekhaak",
        {
            "year": {"min": 2020, "max": 2023},
            "mileage": {"min": 10000},
            "price": {"min": 5000},
            "transmission": "handgeschakeld",
        },
        "newest",
        1,
    )
    assert url == (
        "https://www.gaspedaal.nl/zoeken?trefw=trekhaak&bmin=2020&bmax=2023&pmin=5000"
        "&kmin=10000&trns=HANDGESCHAKELD&srt=dt-d"
    )


def test_page_1_omitted():
    url = build_url("toyota", "rav4", None, None, "relevance", 1)
    assert "page=" not in url


def test_model_requires_brand():
    with pytest.raises(ValidationError, match="model requires brand"):
        build_url(None, "rav4", None, None, "relevance", 1)


def test_unknown_filter():
    with pytest.raises(ValidationError, match="Unknown filter"):
        build_url(None, None, None, {"onzin": 1}, "relevance", 1)


def test_unknown_fuel_value():
    with pytest.raises(ValidationError, match="Unknown value"):
        build_url(None, None, None, {"fuel": "KEROSINE"}, "relevance", 1)


def test_price_min_gt_max():
    with pytest.raises(ValidationError, match="min > max"):
        build_url(None, None, None, {"price": {"min": 20000, "max": 10000}}, "relevance", 1)


def test_distance_requires_postcode():
    with pytest.raises(ValidationError, match="postcode"):
        build_url(None, None, None, None, "distance", 1)


def test_radius_requires_postcode():
    with pytest.raises(ValidationError, match="radius_km requires postcode"):
        build_url(None, None, None, {"radius_km": 50}, "relevance", 1)


def test_invalid_slug():
    with pytest.raises(ValidationError, match="slug"):
        build_url("TOYOTA!", None, None, None, "relevance", 1)


def test_page_bounds():
    with pytest.raises(ValidationError, match="page"):
        build_url(None, None, None, None, "relevance", 0)
    with pytest.raises(ValidationError, match="page"):
        build_url(None, None, None, None, "relevance", 51)


def test_seats_bounds():
    with pytest.raises(ValidationError, match="seats"):
        build_url(None, None, None, {"seats": {"min": 0}}, "relevance", 1)


def test_nap_boolean():
    url = build_url(None, None, None, {"nap": True}, "relevance", 1)
    assert "nap=true" in url


def test_doors_int_accepted():
    url = build_url(None, None, None, {"doors": 5}, "relevance", 1)
    assert "drn=5" in url


def test_options_int_accepted():
    url = build_url(None, None, None, {"options": 420}, "relevance", 1)
    assert "opt=420" in url


def test_postcode_normalized():
    url = build_url(None, None, None, {"postcode": " 3511 ab ", "radius_km": 10}, "distance", 1)
    assert url.endswith("pc=3511AB&strl=10&srt=af-a")


# --- Security review B-08 (S-06) ------------------------------------------------------


def test_huge_numbers_rejected():
    with pytest.raises(ValidationError, match="price: must be between 0 and 10000000"):
        build_url(None, None, None, {"price": {"min": 10**5000}}, "relevance", 1)
    with pytest.raises(ValidationError, match="expected an integer"):
        build_url(None, None, None, {"mileage": {"max": "9" * 5000}}, "relevance", 1)
    with pytest.raises(ValidationError, match="options: values must be"):
        build_url(None, None, None, {"options": [10**12]}, "relevance", 1)


def test_long_lists_rejected_and_duplicates_removed():
    with pytest.raises(ValidationError, match="at most 20 values"):
        build_url(None, None, None, {"options": list(range(21))}, "relevance", 1)
    url = build_url(None, None, None, {"fuel": ["benzine"] * 5000}, "relevance", 1)
    assert "brnst=BENZINE&" in url


@pytest.mark.parametrize("value", ["handmatig", "HANDGESCHAKELD", ["Handmatig", "handgeschakeld"]])
def test_transmission_handmatig_alias(value):
    url = build_url(None, None, None, {"transmission": value}, "relevance", 1)
    assert "trns=HANDGESCHAKELD" in url


def test_normalize_slug():
    assert normalize_slug(" Land-Rover ") == "land-rover"
    assert normalize_slug(None) is None
