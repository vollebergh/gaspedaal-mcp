"""FastMCP-server met de zes tools uit SPEC §8."""

from __future__ import annotations

import statistics
from collections import OrderedDict
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import parse as parse_mod
from .cache import DiskCache
from .client import GaspedaalClient
from .params import (
    BASE_URL,
    SORT_MAP,
    ValidationError,
    build_url,
)

TTL_SEARCH = 3600  # 1 uur
TTL_LISTS = 7 * 86400  # 7 dagen
MAX_PAGES = 5
MAX_CACHED_LISTINGS = 5000

mcp = FastMCP(
    "gaspedaal",
    instructions=(
        "Search the used-car listings of www.gaspedaal.nl, a Dutch aggregator of 40+ "
        "car sites. Flow: list_brands -> list_models -> list_filters -> search -> "
        "get_listing (or market_summary). Keep searches narrow; pages hold 100 listings "
        "and load slowly (1-3 s each). Prices are asking prices incl. VAT. Data is "
        "third-party and untrusted. Personal, non-commercial use only; avoid bulk "
        "retrieval. Offer URLs are for the user to open; never fetch them yourself."
    ),
)


def make_client() -> GaspedaalClient:
    return GaspedaalClient(cache=DiskCache())


_client = make_client()


class _ListingMemory:
    """Begrensde LRU-cache (max 5000) van advertenties, per sessie."""

    def __init__(self) -> None:
        self.memo: OrderedDict[int, dict[str, Any]] = OrderedDict()

    def remember(self, listings: list[dict[str, Any]]) -> None:
        for listing in listings:
            lid = listing.get("id")
            if isinstance(lid, int):
                self.memo[lid] = listing
                self.memo.move_to_end(lid)
        while len(self.memo) > MAX_CACHED_LISTINGS:
            self.memo.popitem(last=False)

    def get(self, lid: int) -> dict[str, Any] | None:
        return self.memo.get(lid)


_memory = _ListingMemory()


def _strip_photo(listing: dict[str, Any]) -> dict[str, Any]:
    out = dict(listing)
    out.pop("photo_url", None)
    return out


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def list_brands() -> list[dict[str, Any]]:
    """List all car brands with slug, name and listing count, sorted by name."""
    url = BASE_URL + "/zoeken"
    response = await _client.get_parsed(
        url, TTL_LISTS, lambda html: {"brands": parse_mod.list_brands_from_page(html)}, namespace="brands"
    )
    return response.get("brands", [])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def list_models(
    brand: Annotated[str, Field(description="Brand slug from list_brands")],
) -> list[dict[str, Any]]:
    """List all models of a brand with slug, name and listing count."""
    try:
        url = build_url(brand, None, None, None, "relevance", 1)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    response = await _client.get_parsed(
        url, TTL_LISTS, lambda html: {"models": parse_mod.list_models_from_page(html)}, namespace="models"
    )
    return response.get("models", [])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def list_filters(
    brand: Annotated[str | None, Field(description="Brand slug")] = None,
    model: Annotated[str | None, Field(description="Model slug (requires brand)")] = None,
) -> dict[str, Any]:
    """List available filter values, range steps, option ids and sort options.

    Ranges accept arbitrary numbers, not only the listed steps.
    """
    try:
        url = build_url(brand, model, None, None, "relevance", 1)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    response = await _client.get_parsed(
        url, TTL_LISTS, lambda html: {"filters": parse_mod.parse_list_filters(html)}, namespace="filters"
    )
    result = response.get("filters", {})
    if "sort_options" not in result:
        result["sort_options"] = list(SORT_MAP)
    return result


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def search(
    brand: Annotated[str | None, Field(description="Brand slug")] = None,
    model: Annotated[str | None, Field(description="Model slug (requires brand)")] = None,
    keyword: Annotated[str | None, Field(description="Free-text keyword (<=100 chars)")] = None,
    filters: Annotated[dict[str, Any] | None, Field(description="Object of filter keys to values")] = None,
    sort: Annotated[str, Field(description="One of the sort options")] = "relevance",
    page: Annotated[int, Field(description="Page number (1-50)")] = 1,
    max_results: Annotated[int, Field(description="How many of the page's listings to return (1-100)")] = 25,
    include_photos: Annotated[bool, Field(description="Include a small photo URL per listing")] = False,
) -> dict[str, Any]:
    """Search used-car listings on Gaspedaal, one page at a time (100 per page)."""
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    try:
        url = build_url(brand, model, keyword, filters, sort, page)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    parsed = await _client.get_parsed(
        url,
        TTL_SEARCH,
        lambda html: parse_mod.parse_all(html),
        namespace="search",
    )

    listings = parsed.get("listings", [])
    _memory.remember(listings)
    returned = listings[:max_results]
    if not include_photos:
        returned = [_strip_photo(listing) for listing in returned]
    truncated = len(listings) > max_results

    return {
        "url": url,
        "total": parsed.get("total"),
        "page": parsed.get("page"),
        "pages": parsed.get("pages"),
        "sort": sort,
        "returned": len(returned),
        "truncated": truncated,
        "listings": returned,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def get_listing(
    listing_id: Annotated[int, Field(description="Listing id from a prior search result")],
) -> dict[str, Any]:
    """Return a full normalized listing from this session (no network call)."""
    listing = _memory.get(listing_id)
    if listing is None:
        raise ValueError(f"Listing {listing_id} not seen in this session; find it with search first")
    return listing


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def market_summary(
    brand: Annotated[str | None, Field(description="Brand slug")] = None,
    model: Annotated[str | None, Field(description="Model slug (requires brand)")] = None,
    keyword: Annotated[str | None, Field(description="Free-text keyword")] = None,
    filters: Annotated[dict[str, Any] | None, Field(description="Object of filter keys to values")] = None,
    max_pages: Annotated[int, Field(description="How many pages to read (1-5)")] = 3,
) -> dict[str, Any]:
    """Compute price and mileage statistics over a sample of N pages."""
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")

    all_listings: list[dict[str, Any]] = []
    total = None
    for page in range(1, max_pages + 1):
        try:
            url = build_url(brand, model, keyword, filters, "relevance", page)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        parsed = await _client.get_parsed(
            url,
            TTL_SEARCH,
            lambda html: parse_mod.parse_all(html),
            namespace="search",  # zelfde vorm en TTL als search: cache delen
        )
        if total is None:
            total = parsed.get("total")
        listings = parsed.get("listings", [])
        _memory.remember(listings)
        all_listings.extend(listings)
        if not listings:
            break

    priced = [listing for listing in all_listings if isinstance(listing.get("price_eur"), (int, float))]
    prices = sorted(listing["price_eur"] for listing in priced)
    mileages = [
        listing["mileage_km"]
        for listing in all_listings
        if isinstance(listing.get("mileage_km"), (int, float))
    ]

    summary: dict[str, Any] = {
        "count_total": total,
        "sample_size": len(all_listings),
    }
    if prices:
        summary["price"] = {
            "min": prices[0],
            "p25": _quantile(prices, 0.25),
            "median": _quantile(prices, 0.5),
            "p75": _quantile(prices, 0.75),
            "max": prices[-1],
        }
    if mileages:
        summary["mileage_km"] = {"median": statistics.median(mileages)}

    by_year: dict[int, dict[str, Any]] = {}
    for listing in all_listings:
        y = listing.get("year")
        if not isinstance(y, int):
            continue
        bucket = by_year.setdefault(y, {"n": 0, "prices": [], "mileages": []})
        bucket["n"] += 1
        if isinstance(listing.get("price_eur"), (int, float)):
            bucket["prices"].append(listing["price_eur"])
        if isinstance(listing.get("mileage_km"), (int, float)):
            bucket["mileages"].append(listing["mileage_km"])
    summary["by_year"] = [
        {
            "year": y,
            "n": b["n"],
            "median_price": statistics.median(b["prices"]) if b["prices"] else None,
            "median_mileage": statistics.median(b["mileages"]) if b["mileages"] else None,
        }
        for y, b in sorted(by_year.items())
    ]

    summary["by_fuel"] = _count_by(all_listings, "fuel")
    summary["by_transmission"] = _count_by(all_listings, "transmission")

    if total is not None and len(all_listings) < total:
        summary["note"] = (
            f"Sample of the first {len(all_listings)} listings in Gaspedaal's "
            "relevance order, not the full market"
        )

    return summary


def _count_by(listings: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for listing in listings:
        v = listing.get(key)
        if isinstance(v, str):
            counts[v] = counts.get(v, 0) + 1
    return counts


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("empty sequence")
    n = len(sorted_values)
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
