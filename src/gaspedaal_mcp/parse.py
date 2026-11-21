"""Flight-payload -> dicts. Pure functies, geen netwerk.

De site (Next.js App Router) levert data in inline scripts
``self.__next_f.push([1,"<JSON-string-fragment>"])``. Deze module decodeert die
fragmenten en haalt er de relevante sleutels uit.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import httpx

from .params import BASE_URL, PARAM_NAMES

# Een JSON-string zonder onge-escapete `"`; possessief (`*+`), zodat een fragment zonder
# afsluiter lineair faalt in plaats van kwadratisch (ReDoS, CWE-1333).
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*+)"\]\)</script>', re.DOTALL)
_COUNT_RE = re.compile(r"\(([\d.]+)\)")
_DECODER = json.JSONDecoder()

FORMAT_ERROR = "Unexpected response format from Gaspedaal"

# Data van de site is onbetrouwbaar (SPEC §2.8): tekst inkorten, te lange URL's weglaten
# (een afgekapte URL werkt toch niet).
MAX_TEXT = 200
MAX_URL = 2000
_URL_KEYS = {"url", "photo_url"}

# Bereik- en keuzefilters in `filters` -> toolsleutel.
_STEP_KEYS = {
    "bouwjaar": "year",
    "prijs": "price",
    "kilometerstand": "mileage",
    "vermogen": "power",
    "aantalZitplaatsenRange": "seats",
    "trekgewicht": "towing_weight",
    "ledigGewicht": "empty_weight",
    "motorinhoud": "engine_cc",
    "locatie": "radius_km",
    "aanbodSinds": "days_listed",
}

# Enum-filters in `dynamicFieldValues` -> toolsleutel.
_ENUM_KEYS = {
    "brandstofsoort": "fuel",
    "transmissietype": "transmission",
    "carrosserievorm": "body",
    "kleur": "color",
    "aantalDeuren": "doors",
    "soortAanbieder": "seller",
    "garantietype": "warranty",
    "kilometerstandCorrect": "nap",
}


def _sanitize(value: Any) -> Any:
    """RSC-waarden normaliseren (SPEC §5): ``$undefined`` en ``$``-refs → None, ``$$`` → ``$``."""
    if isinstance(value, str):
        if value.startswith("$$"):
            return value[1:]
        return None if value.startswith("$") else value
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


def extract_flight_text(html: str) -> str:
    """Decodeert alle flight-fragmenten en plakt ze aan elkaar."""
    return "".join(json.loads(f'"{frag}"') for frag in _PUSH_RE.findall(html))


def _find_value(text: str, key: str) -> Any:
    """Decodeert de waarde bij het eerste ``"key":``; ``None`` als die ontbreekt of kapot is."""
    needle = f'"{key}":'
    idx = text.find(needle)
    if idx == -1:
        return None
    start = idx + len(needle)
    while text[start : start + 1].isspace():
        start += 1
    try:
        return _sanitize(_DECODER.raw_decode(text, start)[0])
    except (ValueError, RecursionError):  # kapot of absurd diep genest
        return None


class PageData:
    """Geparste zoekpagina: de ruwe (gesanitiseerde) blokken uit de flight-payload."""

    def __init__(self, html: str) -> None:
        try:
            text = extract_flight_text(html)
        except ValueError:
            raise ValueError(FORMAT_ERROR) from None
        props = _find_value(text, "commonPortalURLProps")
        occasions = _find_value(text, "occasions")
        if not isinstance(props, dict) or not isinstance(occasions, list):
            raise ValueError(FORMAT_ERROR)
        self.props: dict[str, Any] = props
        self.occasions: list[Any] = occasions
        self.pages = _find_value(text, "numberOfPages")
        self.facets: dict[str, Any] = _dict(_find_value(text, "dynamicFieldValues"))
        self.filters: dict[str, Any] = _dict(_find_value(text, "filters"))

    def facet(self, key: str) -> list[dict[str, Any]]:
        """Items van een facet, zonder groepskoppen."""
        items = self.facets.get(key)
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict) and not i.get("isGroupHeader")]


def check_slugs(page: PageData, brand: str | None, model: str | None) -> None:
    """``ValueError`` als ``brand``/``model`` niet in de merk/model-catalogus van de pagina staan.

    Met een querystring geeft de site bij een onbekende slug geen 404, maar valt ze stil terug
    op een bredere zoekopdracht (alle merken, of alle modellen van het merk). Elke zoekpagina
    bevat de volledige catalogus in ``filters.merkModel``; ontbreekt die, dan controleren we niet.
    """
    catalog = page.filters.get("merkModel")
    if brand is None or not isinstance(catalog, list):
        return
    entry = next((b for b in catalog if isinstance(b, dict) and b.get("slug") == brand), None)
    if entry is None:
        raise ValueError(f"Unknown brand '{brand}'; use list_brands")
    models = entry.get("models")
    if model is not None and not any(
        isinstance(m, dict) and m.get("slug") == model for m in (models if isinstance(models, list) else [])
    ):
        raise ValueError(f"Unknown model '{model}' for brand '{brand}'; use list_models")


def checked(
    parser: Callable[[str], dict[str, Any]], brand: str | None, model: str | None
) -> Callable[[str], dict[str, Any]]:
    """``parser`` met vooraf :func:`check_slugs`; een fout komt zo ook niet in de cache."""

    def run(html: str) -> dict[str, Any]:
        check_slugs(PageData(html), brand, model)
        return parser(html)

    return run


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(item: dict[str, Any] | None) -> int | None:
    """``count`` van een facet-item, of het getal uit ``label`` (``"Toyota (16.724)"`` → 16724)."""
    if not item:
        return None
    if isinstance(count := item.get("count"), int):
        return count
    m = _COUNT_RE.search(item.get("label") or "")
    return int(m.group(1).replace(".", "")) if m else None


def _slug_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"slug": i.get("slug"), "name": i.get("name") or i.get("value"), "count": _count(i)} for i in items
    ]


def list_brands_from_page(html: str) -> list[dict[str, Any]]:
    brands: list[dict[str, Any]] = _clip(_slug_list(PageData(html).facet("merken")))
    return sorted(brands, key=lambda b: (b["name"] or "").lower())


def list_models_from_page(html: str) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = _clip(_slug_list(PageData(html).facet("modellen")))
    return [m for m in models if m["count"] != 0]


def _get(d: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def _clip(value: Any, key: str = "") -> Any:
    """Strings inkorten tot ``MAX_TEXT``; URL's langer dan ``MAX_URL`` worden ``None``. Recursief."""
    if isinstance(value, str):
        if key in _URL_KEYS:
            return value if len(value) <= MAX_URL else None
        return value[:MAX_TEXT]
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v, k) for k, v in value.items()}
    return value


_SITE_HOST = httpx.URL(BASE_URL).host
_OFFER_PATH = "/api/proxy/redirect/"


def _offer_url(value: Any) -> str | None:
    """Alleen een https-link naar de eigen doorverwijzer van Gaspedaal; al het andere wordt ``None``.

    De gebruiker opent deze link zelf: een gemanipuleerde respons mag hem niet naar een
    willekeurige site (of een ``javascript:``-URL) sturen.
    """
    if not isinstance(value, str):
        return None
    url = httpx.URL(urljoin(BASE_URL, value))
    ok = url.scheme == "https" and url.host == _SITE_HOST and url.path.startswith(_OFFER_PATH)
    return str(url) if ok else None


def _photo_url(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _normalize_listing(occ: Any) -> dict[str, Any]:
    if not isinstance(occ, dict):
        return {}
    alg = _dict(_get(occ, "autogegevens.algemeen"))
    hist = _dict(_get(occ, "autogegevens.geschiedenis"))
    motor = _dict(_get(occ, "autogegevens.motor"))
    seller = _dict(_get(occ, "aanbieder.aanbiedergegevens"))

    power_kw = motor.get("vermogenKw")
    place, province = seller.get("plaatsnaam"), seller.get("provincieHulpwaarde")
    listing: dict[str, Any] = {
        "id": occ.get("advertentieId"),
        "listed_at": occ.get("datumGeplaatst"),
        "brand": alg.get("merknaam"),
        "model": alg.get("modelnaam"),
        "version": alg.get("uitvoering"),
        "fuel": alg.get("brandstofsoort"),
        "transmission": alg.get("transmissietype"),
        "body": alg.get("carrosserievorm"),
        "color": alg.get("kleur"),
        "doors": alg.get("aantalDeuren"),
        "year": hist.get("bouwjaar"),
        "mileage_km": hist.get("kilometerstand"),
        "engine_cc": motor.get("motorinhoud"),
        "power_kw": power_kw,
        "power_hp": round(power_kw * 1.35962) if isinstance(power_kw, (int, float)) else None,
        "price_eur": _get(occ, "prijs.totaalInclusiefBtw"),
        "location": (f"{place} ({province})" if province else place) if place else None,
        "seller_name": seller.get("naamsvermelding") or None,
        "offers": [
            {
                "site": p.get("portaalBeschrijving"),
                "type": p.get("portaalType"),
                "url": _offer_url(p.get("klikUrl")),
            }
            for p in occ.get("portalen") or []
            if isinstance(p, dict)
        ],
        "photo_url": _photo_url(_get(occ, "gpFotos.fotoKlein")),
    }
    return {k: _clip(v, k) for k, v in listing.items()}


def parse_all(html: str) -> dict[str, Any]:
    """Zoekpagina → ``{total, page, pages, sort, listings}``."""
    page = PageData(html)
    return {
        "total": page.props.get("totalOccasions"),
        "page": page.props.get("pageNumber"),
        "pages": page.pages,
        "sort": page.props.get("sort"),
        "listings": [_normalize_listing(o) for o in page.occasions],
    }


def parse_list_filters(html: str) -> dict[str, Any]:
    """Uitvoer van ``list_filters`` (SPEC §8.3), zonder ``sort_options`` (voegt de server toe).

    Enums: ``{param, values:[{value, label, count}]}``; stappen: ``{param, steps}``;
    ``options``: ``[{id, name, group, count}]``.
    """
    page = PageData(html)
    result: dict[str, Any] = {}

    for site_key, key in _ENUM_KEYS.items():
        values = [
            {"value": i.get("value"), "label": i.get("label") or i.get("name"), "count": _count(i)}
            for i in page.facet(site_key)
        ]
        if values:
            result[key] = {"param": PARAM_NAMES[key], "values": values}

    counts = {o.get("value"): o for o in page.facet("opties")}
    options = [
        {
            "id": (oid := i.get("id", i.get("value"))),
            "name": i.get("name"),
            "group": i.get("group") or i.get("attribute"),
            "count": _count(counts.get(oid)),
        }
        for i in page.filters.get("zoekopties") or []
        if isinstance(i, dict)
    ]
    if options:
        result["options"] = options

    for site_key, key in _STEP_KEYS.items():
        if page.filters.get(site_key) is not None:
            result[key] = {"param": PARAM_NAMES[key], "steps": page.filters[site_key]}

    clipped: dict[str, Any] = _clip(result)  # site-tekst (labels, namen) is onbetrouwbaar
    return clipped
