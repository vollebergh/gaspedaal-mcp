"""Filtermapping, validatie en URL-opbouw (pure functies, geen netwerk)."""

from __future__ import annotations

import datetime
import re
from typing import Any

import httpx

BASE_URL = "https://www.gaspedaal.nl"

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_POSTCODE_RE = re.compile(r"\d{4}(?:[A-Z]{2})?")

# Bereikfilters: toolsleutel -> (min-param, max-param, ondergrens, bovengrens of None).
# De bovengrens van `year` is dynamisch (huidig jaar + 1).
_RANGES: dict[str, tuple[str, str, int, int | None]] = {
    "year": ("bmin", "bmax", 1900, None),
    "price": ("pmin", "pmax", 0, None),
    "mileage": ("kmin", "kmax", 0, None),
    "power": ("vmin", "vmax", 0, None),
    "seats": ("zmin", "zmax", 1, 9),
    "towing_weight": ("tmin", "tmax", 0, None),
    "empty_weight": ("gmin", "gmax", 0, None),
    "engine_cc": ("cmin", "cmax", 0, None),
}

# Enum-filters: toolsleutel -> (param, toegestane waarden, meerdere waarden toegestaan).
_ENUMS: dict[str, tuple[str, list[str], bool]] = {
    "fuel": ("brnst", "BENZINE DIESEL HYBRIDE ELEKTRICITEIT LPG AARDGAS WATERSTOF".split(), True),
    "transmission": ("trns", ["AUTOMATISCH", "HANDGESCHAKELD"], False),
    "body": (
        "crs",
        "HATCHBACK STATIONWAGEN MPV SEDAN SUV PICK_UP COUPE CABRIOLET BEDRIJFSWAGEN CAMPER".split(),
        True,
    ),
    "color": (
        "klr",
        "BEIGE BLAUW BRUIN GEEL GOUD GRIJS GROEN ORANJE PAARS ROOD WIT ZILVER ZWART".split(),
        True,
    ),
    "seller": ("vrk", ["UNIVERSEEL_AUTOBEDRIJF", "PARTICULIER"], False),
    "warranty": ("grn", ["BOVAG", "MERKGARANTIE"], True),
}

# Filters met een vaste set getallen: toolsleutel -> (param, toegestane waarden).
_CHOICES: dict[str, tuple[str, list[int]]] = {
    "days_listed": ("dagen", [1, 3, 5, 7, 14, 21, 28]),
    "radius_km": ("strl", [5, 10, 20, 30, 40, 50, 75, 100, 150, 200]),
}

_OTHER_KEYS = {"doors", "options", "nap", "postcode"}
_ALL_FILTER_KEYS = set(_RANGES) | set(_ENUMS) | set(_CHOICES) | _OTHER_KEYS

# Toolsleutel -> site-param, voor de `param`-velden van list_filters.
PARAM_NAMES: dict[str, str] = {
    **{k: f"{lo}/{hi}" for k, (lo, hi, _, _) in _RANGES.items()},
    **{k: v[0] for k, v in _ENUMS.items()},
    **{k: v[0] for k, v in _CHOICES.items()},
    "doors": "drn",
    "nap": "nap",
}

SORT_MAP = {
    "relevance": "df-a",
    "distance": "af-a",
    "price_asc": "pr-a",
    "price_desc": "pr-d",
    "mileage_asc": "km-a",
    "mileage_desc": "km-d",
    "year_asc": "bj-a",
    "year_desc": "bj-d",
    "newest": "dt-d",
}

# Volgorde van de parameters in de query (SPEC §4, "Volgorde en codering").
_PARAM_ORDER = (
    "trefw bmin bmax pmin pmax kmin kmax vmin vmax zmin zmax tmin tmax gmin gmax cmin cmax "
    "brnst trns crs klr drn opt vrk grn nap dagen pc strl srt page"
).split()


class ValidationError(ValueError):
    """Korte foutmelding voor de gebruiker."""


def _as_list(value: Any, key: str) -> list[str]:
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return [str(value)]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise ValidationError(f"{key}: expected a string, an integer, or a list of them")


def _as_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{key}: expected an integer")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{key}: expected an integer") from None


def _int_list(value: Any, key: str, low: int, high: int | None) -> str:
    """Lijst van gehele getallen binnen [low, high], als kommagescheiden string."""
    ints = [_as_int(v, key) for v in _as_list(value, key)]
    if any(n < low or (high is not None and n > high) for n in ints):
        raise ValidationError(f"{key}: values must be {low}-{high}" if high else f"{key}: must be >= {low}")
    return ",".join(map(str, ints))


def _range(value: Any, key: str) -> tuple[int | None, int | None]:
    if not isinstance(value, dict) or not set(value) <= {"min", "max"}:
        raise ValidationError(f"{key}: expected an object with 'min' and/or 'max'")
    low, high = (None if value.get(p) is None else _as_int(value[p], f"{key}.{p}") for p in ("min", "max"))
    if low is not None and high is not None and low > high:
        raise ValidationError(f"{key}: min > max")
    _, _, floor, ceil = _RANGES[key]
    if key == "year":
        ceil = datetime.date.today().year + 1
    for v in (low, high):
        if v is not None and (v < floor or (ceil is not None and v > ceil)):
            raise ValidationError(
                f"{key}: must be between {floor} and {ceil}" if ceil else f"{key}: must be >= {floor}"
            )
    return low, high


def build_url(
    brand: str | None,
    model: str | None,
    keyword: str | None,
    filters: dict[str, Any] | None,
    sort: str,
    page: int,
) -> str:
    """Bouw een gaspedaal.nl-zoek-URL op volgens SPEC §4; ``ValidationError`` bij ongeldige invoer."""
    filters = filters or {}
    if model is not None and brand is None:
        raise ValidationError("model requires brand")
    for what, slug in (("brand", brand), ("model", model)):
        if slug is not None and not _SLUG_RE.fullmatch(slug):
            raise ValidationError(f"invalid {what} slug '{slug}'; use list_brands / list_models")
    if sort not in SORT_MAP:
        raise ValidationError(f"Unknown sort '{sort}'; allowed: {', '.join(SORT_MAP)}")
    if not 1 <= page <= 50:
        raise ValidationError("page must be between 1 and 50")
    unknown = sorted(set(filters) - _ALL_FILTER_KEYS)
    if unknown:
        raise ValidationError(f"Unknown filter '{unknown[0]}'; call list_filters")

    params: dict[str, str] = {}
    if keyword is not None:
        keyword = keyword.strip()
        if not keyword:
            raise ValidationError("keyword must not be empty")
        if len(keyword) > 100:
            raise ValidationError("keyword must be at most 100 characters")
        params["trefw"] = keyword

    for key, (pmin, pmax, _, _) in _RANGES.items():
        if key in filters:
            low, high = _range(filters[key], key)
            if low is not None:
                params[pmin] = str(low)
            if high is not None:
                params[pmax] = str(high)

    for key, (param, allowed, multi) in _ENUMS.items():
        if key not in filters:
            continue
        values = [v.strip().upper() for v in _as_list(filters[key], key)]
        if not multi and len(values) != 1:
            raise ValidationError(f"{key}: takes a single value")
        for v in values:
            if v not in allowed:
                raise ValidationError(f"Unknown value '{v}' for {key}; allowed: {', '.join(allowed)}")
        params[param] = ",".join(values)

    for key, (param, allowed_ints) in _CHOICES.items():
        if key in filters:
            n = _as_int(filters[key], key)
            if n not in allowed_ints:
                raise ValidationError(f"{key}: allowed values: {', '.join(map(str, allowed_ints))}")
            params[param] = str(n)

    if "doors" in filters:
        params["drn"] = _int_list(filters["doors"], "doors", 2, 6)
    if "options" in filters:
        params["opt"] = _int_list(filters["options"], "options", 0, None)
    if "nap" in filters:
        if not isinstance(filters["nap"], bool):
            raise ValidationError("nap: expected a boolean")
        if filters["nap"]:
            params["nap"] = "true"

    if filters.get("postcode") is not None:
        pc = str(filters["postcode"]).strip().upper().replace(" ", "")
        if not _POSTCODE_RE.fullmatch(pc):
            raise ValidationError("postcode: expected 4 digits, optionally followed by 2 letters")
        params["pc"] = pc
    elif "strl" in params:
        raise ValidationError("radius_km requires postcode")

    if sort == "distance" and "pc" not in params:
        raise ValidationError("sort 'distance' requires postcode")
    params["srt"] = SORT_MAP[sort]
    if page > 1:
        params["page"] = str(page)

    path = "/" + "/".join(s for s in (brand, model) if s) if brand else "/zoeken"
    return str(httpx.URL(BASE_URL + path, params=[(k, params[k]) for k in _PARAM_ORDER if k in params]))
