"""Gedeelde fixtures, waaronder de synthetische RSC-pagina-bouwer."""

from __future__ import annotations

import json

import pytest


def make_page(payload: dict, chunks: int = 3) -> str:
    """Serialiseert een payload tot een RSC-achtige flight-payload.

    De payload wordt als JSON gedumpt, in ``chunks`` stukken geknipt, elk stuk
    als JSON-string geëscapet en in ``<script>self.__next_f.push([1,"…"])</script>``
    gezet. Zo testen we extractie over chunkgrenzen heen.
    """
    raw = json.dumps(payload, ensure_ascii=False)
    n = len(raw)
    size = max(1, (n + chunks - 1) // chunks)
    pieces = [raw[i : i + size] for i in range(0, n, size)]
    scripts = []
    for piece in pieces:
        escaped = json.dumps(piece, ensure_ascii=False)
        scripts.append(f"<script>self.__next_f.push([1,{escaped}])</script>")
    return "<html>" + "".join(scripts) + "</html>"


def make_occasion(advertentie_id: int, **overrides) -> dict:
    """Bouwt een synthetische advertentie."""
    occ = {
        "advertentieId": advertentie_id,
        "datumGeplaatst": "2026-11-01",
        "autogegevens": {
            "algemeen": {
                "merknaam": "Toyota",
                "modelnaam": "RAV4",
                "uitvoering": "2.5 Hybrid Executive",
                "brandstofsoort": "HYBRIDE",
                "transmissietype": "AUTOMATISCH",
                "carrosserievorm": "SUV",
                "kleur": "ZWART",
                "aantalDeuren": 5,
            },
            "geschiedenis": {
                "bouwjaar": 2020,
                "kilometerstand": 80000,
            },
            "motor": {
                "motorinhoud": 2500,
                "vermogenKw": 160,
            },
        },
        "prijs": {"totaalInclusiefBtw": 35000},
        "aanbieder": {
            "aanbiedergegevens": {
                "plaatsnaam": "Woerden",
                "provincieHulpwaarde": "UT",
                "naamsvermelding": "Demo Autobedrijf",
            }
        },
        "gpFotos": {"fotoKlein": "https://example.invalid/foto.jpg"},
        "portalen": [
            {
                "portaalBeschrijving": "AutoTrack",
                "portaalType": "ANB",
                "klikUrl": "/api/proxy/redirect/vehicle/1",
            }
        ],
    }
    occ.update(overrides)
    return occ


def make_occasions_payload(occasions: list[dict] | None = None, **props) -> dict:
    occasions = occasions if occasions is not None else [make_occasion(1)]
    return {
        "commonPortalURLProps": {
            "sort": props.get("sort", "df-a"),
            "totalOccasions": props.get("total", len(occasions)),
            "pageSize": 100,
            "pageNumber": props.get("page", 1),
        },
        "numberOfPages": props.get("pages", 1),
        "occasions": occasions,
        "dynamicFieldValues": props.get("dynamicFieldValues", {}),
        "filters": props.get("filters", {}),
    }


@pytest.fixture
def page_builder():
    return make_page
