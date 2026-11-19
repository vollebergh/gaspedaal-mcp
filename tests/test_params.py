"""Tests voor params.py: URL-opbouw en validatie."""

from __future__ import annotations

import pytest

from gaspedaal_mcp.params import ValidationError, build_url


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
