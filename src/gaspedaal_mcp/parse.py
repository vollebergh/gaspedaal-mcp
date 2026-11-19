"""Flight-payload -> dicts. Pure functies, geen netwerk.

De site (Next.js App Router) levert data in inline scripts
``self.__next_f.push([1,"<JSON-string-fragment>"])``. Dit moduul extraheert en
decodert die fragmenten en haalt er de relevante sleutels uit.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
_DECODER = json.JSONDecoder()

_FORMAT_ERROR = "Unexpected response format from Gaspedaal"

_KEYS = {
    "props": "commonPortalURLProps",
    "pages": "numberOfPages",
    "occasions": "occasions",
    "facets": "dynamicFieldValues",
    "filters": "filters",
}

# Bereikfilter-sleutel in de site -> (toolsleutel, min-label, max-label).
_RANGE_SITE_KEYS = [
    ("bouwjaar", "year"),
    ("prijs", "price"),
    ("kilometerstand", "mileage"),
    ("vermogen", "power"),
    ("aantalZitplaatsenRange", "seats"),
    ("trekgewicht", "towing_weight"),
    ("ledigGewicht", "empty_weight"),
    ("motorinhoud", "engine_cc"),
]

# enum-filter-sleutel in dynamicFieldValues -> toolsleutel.
_ENUM_FACET_KEYS = {
    "brandstofsoort": "fuel",
    "transmissietype": "transmission",
    "carrosserievorm": "body",
    "kleur": "color",
    "aantalDeuren": "doors",
    "soortAanbieder": "seller",
    "garantietype": "warranty",
    "kilometerstandCorrect": "nap",
}

# toolsleutel -> site-queryparam (voor de "param"-veldjes in list_filters).
_PARAM_NAMES = {
    "fuel": "brnst",
    "transmission": "trns",
    "body": "crs",
    "color": "klr",
    "doors": "drn",
    "seller": "vrk",
    "warranty": "grn",
    "nap": "nap",
    "year": "bmin/bmax",
    "price": "pmin/pmax",
    "mileage": "kmin/kmax",
    "power": "vmin/vmax",
    "seats": "zmin/zmax",
    "towing_weight": "tmin/tmax",
    "empty_weight": "gmin/gmax",
    "engine_cc": "cmin/cmax",
    "radius_km": "strl",
    "days_listed": "dagen",
}


def _unescape_fragment(frag: str) -> str:
    return json.loads('"' + frag + '"')


def _sanitize(value: Any) -> Any:
    """RSC-waarden normaliseren (zie SPEC §5): ``$undefined`` en ``$``-refs → None,
    ``$$`` → ``$``. Recursief op dict/list.
    """
    if isinstance(value, str):
        if value == "$undefined":
            return None
        if value.startswith("$"):
            if value.startswith("$$"):
                return value[1:]
            return None
        return value
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


def extract_flight_text(html: str) -> str:
    """Extraheert en decodeert alle flight-fragmenten tot één tekstblok."""
    parts: list[str] = []
    for frag in _PUSH_RE.findall(html):
        parts.append(_unescape_fragment(frag))
    return "".join(parts)


def _find_value(text: str, key: str) -> tuple[Any, int] | None:
    """Zoekt het eerste voorval van ``"key":`` als *sleutel* en decodeert de waarde.

    Een voorkomen waarop geen `:` volgt (dus een waarde in plaats van een
    sleutel) wordt overgeslagen. Decodeert niet (of geeft ``None``) in plaats
    van een rauwe ``JSONDecodeError``.
    """
    needle = '"' + key + '"'
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            return None
        colon = idx + len(needle)
        if colon < len(text) and text[colon] == ":":
            valstart = colon + 1
            while valstart < len(text) and text[valstart] in " \t\r\n":
                valstart += 1
            try:
                value, end = _DECODER.raw_decode(text, valstart)
            except (json.JSONDecodeError, ValueError):
                return None
            return value, end
        start = idx + 1


def _get_facet_counts(facets: Any) -> dict[str, Any]:
    return facets if isinstance(facets, dict) else {}


class PageData:
    """Geparste zoekpagina."""

    def __init__(self, html: str) -> None:
        text = extract_flight_text(html)
        props = _find_value(text, _KEYS["props"])
        occasions = _find_value(text, _KEYS["occasions"])
        if props is None or occasions is None:
            raise ValueError(_FORMAT_ERROR)
        self.props_raw = _sanitize(props[0])
        self.occasions_raw = _sanitize(occasions[0])

        pages = _find_value(text, _KEYS["pages"])
        self.pages = _sanitize(pages[0]) if pages is not None else None

        facets = _find_value(text, _KEYS["facets"])
        self.facets_raw = _sanitize(facets[0]) if facets is not None else None

        filters = _find_value(text, _KEYS["filters"])
        self.filters_raw = _sanitize(filters[0]) if filters is not None else None

    @property
    def total(self) -> int | None:
        return self.props_raw.get("totalOccasions") if isinstance(self.props_raw, dict) else None

    @property
    def sort(self) -> str | None:
        return self.props_raw.get("sort") if isinstance(self.props_raw, dict) else None

    @property
    def page_number(self) -> int | None:
        return self.props_raw.get("pageNumber") if isinstance(self.props_raw, dict) else None


def _int_count_from_label(label: Any) -> int | None:
    """``"Toyota (16.724)"`` → 16724 (punt is duizendtalscheiding)."""
    if not isinstance(label, str):
        return None
    m = re.search(r"\(([\d.]+)\)", label)
    if not m:
        return None
    digits = m.group(1).replace(".", "")
    try:
        return int(digits)
    except ValueError:
        return None


def list_brands_from_page(html: str) -> list[dict[str, Any]]:
    page = PageData(html)
    facets = page.facets_raw or {}
    merken = facets.get("merken", [])
    result: list[dict[str, Any]] = []
    for item in merken:
        if not isinstance(item, dict) or item.get("isGroupHeader"):
            continue
        result.append(
            {
                "slug": item.get("slug"),
                "name": item.get("name") or item.get("value"),
                "count": _int_count_from_label(item.get("label")),
            }
        )
    result.sort(key=lambda x: (x.get("name") or "").lower())
    return result


def list_models_from_page(html: str) -> list[dict[str, Any]]:
    page = PageData(html)
    facets = page.facets_raw or {}
    modellen = facets.get("modellen", [])
    result: list[dict[str, Any]] = []
    for item in modellen:
        if not isinstance(item, dict) or item.get("isGroupHeader"):
            continue
        count = _int_count_from_label(item.get("label"))
        if count is not None and count <= 0:
            continue
        result.append(
            {
                "slug": item.get("slug"),
                "name": item.get("name") or item.get("value"),
                "count": count,
            }
        )
    return result


def _facet_entries(facets: dict[str, Any], site_key: str) -> list[dict[str, Any]]:
    items = facets.get(site_key, [])
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and not i.get("isGroupHeader")]


def _get_path(d: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _normalize_listing(occ: Any) -> dict[str, Any]:
    if not isinstance(occ, dict):
        return {}
    alg = _get_path(occ, "autogegevens.algemeen", {}) or {}
    hist = _get_path(occ, "autogegevens.geschiedenis", {}) or {}
    motor = _get_path(occ, "autogegevens.motor", {}) or {}
    aanbieder = _get_path(occ, "aanbieder.aanbiedergegevens", {}) or {}

    version = alg.get("uitvoering")
    if isinstance(version, str):
        version = version[:200]

    power_kw = motor.get("vermogenKw")
    power_hp = round(power_kw * 1.35962) if isinstance(power_kw, (int, float)) else None

    plaats = aanbieder.get("plaatsnaam")
    provincie = aanbieder.get("provincieHulpwaarde")
    location = None
    if plaats:
        location = plaats
        if provincie:
            location = f"{plaats} ({provincie})"

    offers = []
    for portal in occ.get("portalen") or []:
        if not isinstance(portal, dict):
            continue
        offers.append(
            {
                "site": portal.get("portaalBeschrijving"),
                "type": portal.get("portaalType"),
                "url": portal.get("klikUrl"),
            }
        )

    listing: dict[str, Any] = {
        "id": occ.get("advertentieId"),
        "listed_at": occ.get("datumGeplaatst"),
        "brand": alg.get("merknaam"),
        "model": alg.get("modelnaam"),
        "version": version,
        "fuel": alg.get("brandstofsoort"),
        "transmission": alg.get("transmissietype"),
        "body": alg.get("carrosserievorm"),
        "color": alg.get("kleur"),
        "doors": alg.get("aantalDeuren"),
        "year": hist.get("bouwjaar"),
        "mileage_km": hist.get("kilometerstand"),
        "engine_cc": motor.get("motorinhoud"),
        "power_kw": power_kw,
        "power_hp": power_hp,
        "price_eur": _get_path(occ, "prijs.totaalInclusiefBtw"),
        "location": location,
        "seller_name": aanbieder.get("naamsvermelding") or None,
        "offers": offers,
        "photo_url": _get_path(occ, "gpFotos.fotoKlein"),
    }
    return listing


def parse_listings(html: str) -> list[dict[str, Any]]:
    page = PageData(html)
    occasions = page.occasions_raw
    if not isinstance(occasions, list):
        return []
    return [_normalize_listing(occ) for occ in occasions]


def parse_all(html: str) -> dict[str, Any]:
    """Parseert de hele zoekpagina naar ``{total, page, pages, sort, listings, facets}``."""
    page = PageData(html)
    listings = (
        [_normalize_listing(o) for o in page.occasions_raw or []]
        if isinstance(page.occasions_raw, list)
        else []
    )
    return {
        "total": page.total,
        "page": page.page_number,
        "pages": page.pages,
        "sort": page.sort,
        "listings": listings,
        "facets": {"dynamicFieldValues": page.facets_raw, "filters": page.filters_raw},
    }


def parse_list_filters(html: str) -> dict[str, Any]:
    """Bouwt de ``list_filters``-uitvoer op uit de pagina (zie SPEC §8.3).

    Enum-filters: ``{tool_key: {"param", "values":[{value, label, count}]}}``.
    Bereikfilters: ``{tool_key: {"param", "steps":[…]}}``.
    Plus ``options`` (id/name/group/count), ``radius_km``, ``days_listed`` en
    ``sort_options`` (wordt door de server toegevoegd).
    """
    page = PageData(html)
    facets = page.facets_raw or {}
    filters = page.filters_raw or {}
    result: dict[str, Any] = {}

    for site_key, tool_key in _ENUM_FACET_KEYS.items():
        entries = _facet_entries(facets, site_key)
        if not entries:
            continue
        values = []
        for item in entries:
            values.append(
                {
                    "value": item.get("value"),
                    "label": item.get("label") or item.get("name"),
                    "count": item.get("count") or _int_count_from_label(item.get("label")),
                }
            )
        result[tool_key] = {"param": _PARAM_NAMES[tool_key], "values": values}

    # opties: id uit `filters.zoekopties` (`id`), count uit `dynamicFieldValues.opties`.
    opt_counts = {o.get("value"): o for o in _facet_entries(facets, "opties")}
    options = []
    for item in filters.get("zoekopties") or []:
        if not isinstance(item, dict):
            continue
        opt_id = item.get("id")
        if opt_id is None:
            opt_id = item.get("value")
        opt = opt_counts.get(opt_id) if opt_id is not None else None
        count = (opt.get("count") if opt else None) or _int_count_from_label((opt or {}).get("label"))
        options.append(
            {
                "id": opt_id,
                "name": item.get("name"),
                "group": item.get("group") or item.get("attribute"),
                "count": count,
            }
        )
    if options:
        result["options"] = options

    # bereikfilters
    for site_key, tool_key in _RANGE_SITE_KEYS:
        steps = filters.get(site_key)
        if steps is not None:
            result[tool_key] = {"param": _PARAM_NAMES[tool_key], "steps": steps}

    # radius_km en days_listed: vaste waardenlijsten
    for site_key, tool_key in (("locatie", "radius_km"), ("aanbodSinds", "days_listed")):
        steps = filters.get(site_key)
        if steps is not None:
            result[tool_key] = {"param": _PARAM_NAMES[tool_key], "steps": steps}

    return result
