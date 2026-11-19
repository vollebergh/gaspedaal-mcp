"""Filtermapping, validatie en URL-opbouw (pure functies, geen netwerk)."""

from __future__ import annotations

import datetime
import re
from typing import Any

import httpx

BASE_URL = "https://www.gaspedaal.nl"

SLUG_RE = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

# filter-toolsleutel -> (param voor min, param voor max, of None voor lijst/enum)
_RANGE_FILTERS: dict[str, tuple[str, str]] = {
    "year": ("bmin", "bmax"),
    "price": ("pmin", "pmax"),
    "mileage": ("kmin", "kmax"),
    "power": ("vmin", "vmax"),
    "seats": ("zmin", "zmax"),
    "towing_weight": ("tmin", "tmax"),
    "empty_weight": ("gmin", "gmax"),
    "engine_cc": ("cmin", "cmax"),
}

# filter-toolsleutel -> (param, toegestane waarden of None)
_LIST_FILTERS: dict[str, tuple[str, list[str] | None]] = {
    "fuel": ("brnst", ["BENZINE", "DIESEL", "HYBRIDE", "ELEKTRICITEIT", "LPG", "AARDGAS", "WATERSTOF"]),
    "body": (
        "crs",
        [
            "HATCHBACK",
            "STATIONWAGEN",
            "MPV",
            "SEDAN",
            "SUV",
            "PICK_UP",
            "COUPE",
            "CABRIOLET",
            "BEDRIJFSWAGEN",
            "CAMPER",
        ],
    ),
    "color": (
        "klr",
        [
            "BEIGE",
            "BLAUW",
            "BRUIN",
            "GEEL",
            "GOUD",
            "GRIJS",
            "GROEN",
            "ORANJE",
            "PAARS",
            "ROOD",
            "WIT",
            "ZILVER",
            "ZWART",
        ],
    ),
    "warranty": ("grn", ["BOVAG", "MERKGARANTIE"]),
}

# filter-toolsleutel -> (param, toegestane waarden of None) voor één-waarde-filters.
_SINGLE_FILTERS: dict[str, tuple[str, list[str] | None]] = {
    "transmission": ("trns", ["AUTOMATISCH", "HANDGESCHAKELD"]),
    "seller": ("vrk", ["UNIVERSEEL_AUTOBEDRIJF", "PARTICULIER"]),
}

_OPTION_FILTERS: dict[str, str] = {
    "doors": "drn",
}

_RADIUS_ALLOWED = {5, 10, 20, 30, 40, 50, 75, 100, 150, 200}
_DAYS_ALLOWED = {1, 3, 5, 7, 14, 21, 28}

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

# Volgorde van de parameters in de query (zie SPEC §4, "Volgorde en codering").
_PARAM_ORDER = [
    "trefw",
    "bmin",
    "bmax",
    "pmin",
    "pmax",
    "kmin",
    "kmax",
    "vmin",
    "vmax",
    "zmin",
    "zmax",
    "tmin",
    "tmax",
    "gmin",
    "gmax",
    "cmin",
    "cmax",
    "brnst",
    "trns",
    "crs",
    "klr",
    "drn",
    "opt",
    "vrk",
    "grn",
    "nap",
    "dagen",
    "pc",
    "strl",
    "srt",
    "page",
]


class ValidationError(ValueError):
    """Korte foutmelding voor de gebruiker."""


def _current_year() -> int:
    return datetime.date.today().year


def _validate_slug(slug: str, what: str) -> None:
    if not re.fullmatch(SLUG_RE, slug):
        raise ValidationError(f"invalid {what} slug '{slug}'; use list_brands / list_models")


def _normalize_value(value: str, allowed: list[str] | None, key: str) -> str:
    norm = value.strip().upper()
    if allowed is not None and norm not in allowed:
        raise ValidationError(f"Unknown value '{value}' for {key}; allowed: {', '.join(allowed)}")
    return norm


def _as_list(value: Any, key: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, int) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    raise ValidationError(f"{key}: expected a string, an integer, or a list of them")


def _as_range(value: Any, key: str) -> dict[str, int | None]:
    if isinstance(value, dict):
        if not (set(value.keys()) <= {"min", "max"}):
            raise ValidationError(f"{key}: range accepts only 'min' and 'max'")
        result: dict[str, int | None] = {}
        for part in ("min", "max"):
            if part in value:
                if value[part] is None:
                    result[part] = None
                else:
                    try:
                        result[part] = int(value[part])
                    except (TypeError, ValueError):
                        raise ValidationError(f"{key}: {part} must be an integer") from None
        return result
    raise ValidationError(f"{key}: expected {{min, max}} object")


def _as_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{key}: expected an integer")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{key}: expected an integer") from None


def validate_postcode(pc: str) -> str:
    pc = pc.strip().upper().replace(" ", "")
    if not re.fullmatch(r"^\d{4}([A-Z]{2})?$", pc):
        raise ValidationError("postcode: expected 4 digits, optionally followed by 2 letters")
    return pc


def build_url(
    brand: str | None,
    model: str | None,
    keyword: str | None,
    filters: dict[str, Any] | None,
    sort: str,
    page: int,
) -> str:
    """Bouw een gaspedaal.nl-zoek-URL op volgens SPEC §4.

    Geeft een ``ValidationError`` bij ongeldige invoer.
    """
    filters = filters or {}
    if model is not None and brand is None:
        raise ValidationError("model requires brand")

    for key in (brand, model):
        if key is not None:
            _validate_slug(key, "brand" if key == brand else "model")

    if sort not in SORT_MAP:
        allowed = ", ".join(SORT_MAP)
        raise ValidationError(f"Unknown sort '{sort}'; allowed: {allowed}")

    if not 1 <= page <= 50:
        raise ValidationError("page must be between 1 and 50")

    params: dict[str, str] = {}
    if keyword is not None:
        keyword = keyword.strip()
        if not keyword:
            raise ValidationError("keyword must not be empty")
        if len(keyword) > 100:
            raise ValidationError("keyword must be at most 100 characters")
        params["trefw"] = keyword

    unknown = set(filters) - _ALL_FILTER_KEYS
    if unknown:
        name = sorted(unknown)[0]
        raise ValidationError(f"Unknown filter '{name}'; call list_filters")

    # bereikfilters
    for key, (pmin, pmax) in _RANGE_FILTERS.items():
        if key not in filters:
            continue
        rng = _as_range(filters[key], key)
        low = rng.get("min")
        high = rng.get("max")
        if low is not None and high is not None and low > high:
            raise ValidationError(f"{key}: min > max")
        _validate_range_bounds(key, low, high)
        if low is not None:
            params[pmin] = str(low)
        if high is not None:
            params[pmax] = str(high)

    # lijstfilters (brandstof, carrosserie, kleur, garantie, opties, deuren)
    for key, (param, allowed) in _LIST_FILTERS.items():
        if key not in filters:
            continue
        values = [_normalize_value(v, allowed, key) for v in _as_list(filters[key], key)]
        params[param] = ",".join(values)

    for key, (param, allowed) in _SINGLE_FILTERS.items():
        if key not in filters:
            continue
        values = _as_list(filters[key], key)
        if len(values) != 1:
            raise ValidationError(f"{key}: takes a single value")
        params[param] = _normalize_value(values[0], allowed, key)

    # deuren (lijst van ints)
    if "doors" in filters:
        param = _OPTION_FILTERS["doors"]
        values = _as_list(filters["doors"], "doors")
        ints: list[int] = []
        for v in values:
            n = _as_int(v, "doors")
            if not 2 <= n <= 6:
                raise ValidationError("doors: allowed values 2-6")
            ints.append(n)
        params[param] = ",".join(str(n) for n in ints)

    # opties (numerieke id's)
    if "options" in filters:
        values = _as_list(filters["options"], "options")
        ids: list[str] = []
        for v in values:
            n = _as_int(v, "options")
            if n < 0:
                raise ValidationError("options: expected non-negative integer ids")
            ids.append(str(n))
        params["opt"] = ",".join(ids)

    # nap (bool)
    if "nap" in filters:
        val = filters["nap"]
        if not isinstance(val, bool):
            raise ValidationError("nap: expected a boolean")
        if val:
            params["nap"] = "true"

    # dagen (één gewaardeerde int)
    if "days_listed" in filters:
        n = _as_int(filters["days_listed"], "days_listed")
        if n not in _DAYS_ALLOWED:
            raise ValidationError("days_listed: allowed values: 1, 3, 5, 7, 14, 21, 28")
        params["dagen"] = str(n)

    # postcode + straal
    pc = filters.get("postcode")
    strl = filters.get("radius_km")
    if strl is not None and pc is None:
        raise ValidationError("radius_km requires postcode")
    if pc is not None:
        params["pc"] = validate_postcode(str(pc))
    if strl is not None:
        n = _as_int(strl, "radius_km")
        if n not in _RADIUS_ALLOWED:
            raise ValidationError(
                f"radius_km: allowed values: {', '.join(str(x) for x in sorted(_RADIUS_ALLOWED))}"
            )
        params["strl"] = str(n)

    params["srt"] = SORT_MAP[sort]
    if sort == "distance" and "pc" not in params:
        raise ValidationError("sort 'distance' requires postcode")
    if page > 1:
        params["page"] = str(page)

    path = "/zoeken"
    if brand is not None:
        path = f"/{brand}"
        if model is not None:
            path += f"/{model}"

    ordered = [(k, params[k]) for k in _PARAM_ORDER if k in params]
    url = httpx.URL(BASE_URL + path, params=ordered)
    return str(url)


def _validate_range_bounds(key: str, low: int | None, high: int | None) -> None:
    year = _current_year() + 1
    if key == "year":
        for v in (low, high):
            if v is not None and not 1900 <= v <= year:
                raise ValidationError(f"year: must be between 1900 and {year}")
    elif key == "seats":
        for v in (low, high):
            if v is not None and not 1 <= v <= 9:
                raise ValidationError("seats: must be between 1 and 9")
    else:
        # price, mileage, power, towing_weight, empty_weight, engine_cc: >= 0
        for v in (low, high):
            if v is not None and v < 0:
                raise ValidationError(f"{key}: must be >= 0")


_ALL_FILTER_KEYS = (
    set(_RANGE_FILTERS)
    | set(_LIST_FILTERS)
    | set(_SINGLE_FILTERS)
    | set(_OPTION_FILTERS)
    | {"options", "nap", "days_listed", "postcode", "radius_km"}
)
