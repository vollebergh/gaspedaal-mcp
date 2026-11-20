"""FastMCP-server met de zes tools uit SPEC §8."""

from __future__ import annotations

import logging
import statistics
from collections import Counter, OrderedDict
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, parse, serve
from .client import GaspedaalClient
from .params import BASE_URL, SORT_MAP, build_url

TTL_SEARCH = 3600
TTL_LISTS = 7 * 86400
MAX_PAGES = 5
MAX_REMEMBERED = 5000

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

# httpx logt elke URL op INFO, inclusief zoektermen en postcode; die horen niet in de
# logbestanden van de MCP-client (S-05).
logging.getLogger("httpx").setLevel(logging.WARNING)

_client = GaspedaalClient()
# Advertenties die deze sessie zijn teruggegeven, voor get_listing (LRU, max 5.000).
_memory: OrderedDict[int, dict[str, Any]] = OrderedDict()

_TOOL = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
Brand = Annotated[str | None, Field(description="Brand slug from list_brands")]
Model = Annotated[str | None, Field(description="Model slug from list_models (requires brand)")]
Keyword = Annotated[str | None, Field(description="Free-text keyword (<=100 chars)")]
Filters = Annotated[
    dict[str, Any] | None,
    Field(description="Object of filter keys (see list_filters) to values; ranges as {min, max}"),
]


def _remember(listings: list[dict[str, Any]]) -> None:
    for listing in listings:
        if isinstance(lid := listing.get("id"), int):
            _memory[lid] = listing
            _memory.move_to_end(lid)
    while len(_memory) > MAX_REMEMBERED:
        _memory.popitem(last=False)


async def _search_page(
    brand: str | None, model: str | None, keyword: str | None, filters: Any, sort: str, page: int
) -> tuple[str, dict[str, Any]]:
    url = build_url(brand, model, keyword, filters, sort, page)
    return url, await _client.get_parsed(url, TTL_SEARCH, parse.parse_all, namespace="search")


@mcp.tool(annotations=_TOOL)
async def list_brands() -> list[dict[str, Any]]:
    """List all car brands with slug, name and listing count, sorted by name."""
    url = BASE_URL + "/zoeken"
    data = await _client.get_parsed(
        url, TTL_LISTS, lambda html: {"brands": parse.list_brands_from_page(html)}, namespace="brands"
    )
    brands: list[dict[str, Any]] = data["brands"]
    return brands


@mcp.tool(annotations=_TOOL)
async def list_models(
    brand: Annotated[str, Field(description="Brand slug from list_brands")],
) -> list[dict[str, Any]]:
    """List all models of a brand with slug, name and listing count."""
    url = build_url(brand, None, None, None, "relevance", 1)
    data = await _client.get_parsed(
        url, TTL_LISTS, lambda html: {"models": parse.list_models_from_page(html)}, namespace="models"
    )
    models: list[dict[str, Any]] = data["models"]
    return models


@mcp.tool(annotations=_TOOL)
async def list_filters(brand: Brand = None, model: Model = None) -> dict[str, Any]:
    """List available filter values (with counts), range steps, option ids and sort options.

    Ranges accept arbitrary numbers, not only the listed steps.
    """
    url = build_url(brand, model, None, None, "relevance", 1)
    data = await _client.get_parsed(url, TTL_LISTS, parse.parse_list_filters, namespace="filters")
    return {**data, "sort_options": list(SORT_MAP)}


@mcp.tool(annotations=_TOOL)
async def search(
    brand: Brand = None,
    model: Model = None,
    keyword: Keyword = None,
    filters: Filters = None,
    sort: Annotated[str, Field(description=f"One of: {', '.join(SORT_MAP)}")] = "relevance",
    page: Annotated[int, Field(description="Page number (1-50)")] = 1,
    max_results: Annotated[int, Field(description="How many of the page's listings to return (1-100)")] = 25,
    include_photos: Annotated[bool, Field(description="Include a small photo URL per listing")] = False,
) -> dict[str, Any]:
    """Search used-car listings on Gaspedaal, one page at a time (100 per page)."""
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")
    url, data = await _search_page(brand, model, keyword, filters, sort, page)
    listings = data["listings"][:max_results]
    _remember(listings)
    if not include_photos:
        listings = [{k: v for k, v in item.items() if k != "photo_url"} for item in listings]
    return {
        "url": url,
        "total": data["total"],
        "page": data["page"],
        "pages": data["pages"],
        "sort": sort,
        "returned": len(listings),
        "truncated": len(data["listings"]) > max_results,
        "listings": listings,
    }


@mcp.tool(annotations=_TOOL)
async def get_listing(
    listing_id: Annotated[int, Field(description="Listing id from a prior search result")],
) -> dict[str, Any]:
    """Return a full normalized listing seen earlier in this session (no network call)."""
    if listing_id not in _memory:
        raise ValueError(f"Listing {listing_id} not seen in this session; find it with search first")
    return _memory[listing_id]


def _numbers(items: list[dict[str, Any]], key: str) -> list[float]:
    return [x[key] for x in items if isinstance(x.get(key), (int, float))]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(listings: list[dict[str, Any]], total: int | None) -> dict[str, Any]:
    """Statistiek over een steekproef advertenties (pure functie, zie market_summary)."""
    prices = sorted(_numbers(listings, "price_eur"))
    summary: dict[str, Any] = {"count_total": total, "sample_size": len(listings)}
    if prices:
        p25, median, p75 = (
            statistics.quantiles(prices, n=4, method="inclusive") if len(prices) > 1 else prices * 3
        )
        summary["price"] = {"min": prices[0], "p25": p25, "median": median, "p75": p75, "max": prices[-1]}
    if mileages := _numbers(listings, "mileage_km"):
        summary["mileage_km"] = {"median": statistics.median(mileages)}

    years = sorted({x["year"] for x in listings if isinstance(x.get("year"), int)})
    summary["by_year"] = [
        {
            "year": y,
            "n": len(group := [x for x in listings if x.get("year") == y]),
            "median_price": _median(_numbers(group, "price_eur")),
            "median_mileage": _median(_numbers(group, "mileage_km")),
        }
        for y in years
    ]
    for key in ("fuel", "transmission"):
        summary[f"by_{key}"] = dict(Counter(x[key] for x in listings if isinstance(x.get(key), str)))
    if total is not None and len(listings) < total:
        summary["note"] = (
            f"Sample of the first {len(listings)} listings in Gaspedaal's relevance order, "
            "not the full market"
        )
    return summary


@mcp.tool(annotations=_TOOL)
async def market_summary(
    brand: Brand = None,
    model: Model = None,
    keyword: Keyword = None,
    filters: Filters = None,
    max_pages: Annotated[int, Field(description=f"How many pages of 100 to read (1-{MAX_PAGES})")] = 3,
) -> dict[str, Any]:
    """Price and mileage statistics over the first pages in relevance order.

    This is a sample, not the full market; `note` says so when the sample is smaller than the total.
    """
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")

    listings: list[dict[str, Any]] = []
    total = None
    for page in range(1, max_pages + 1):
        _, data = await _search_page(brand, model, keyword, filters, "relevance", page)
        total = data["total"] if total is None else total
        listings += data["listings"]
        if not data["listings"] or (data["pages"] or 0) <= page:
            break
    return summarize(listings, total)


def main(argv: list[str] | None = None) -> None:
    """stdio (standaard) of, met ``--http``, streamable HTTP op 127.0.0.1 (zie serve.py)."""
    args = serve.parse_args(argv)
    # FastMCP heeft geen versie-argument; zonder dit meldt de server de versie van de SDK.
    mcp._mcp_server.version = __version__
    if args.http:
        serve.run_http(mcp, args)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
