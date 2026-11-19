"""HTTP-client: host-allowlist, throttle, back-off, groottelimiet en cache.

Alleen ``GET`` naar ``https://www.gaspedaal.nl``, nooit ``/api/proxy/redirect/...``.
Eén verzoek tegelijk, minstens 1,0 s ertussen.
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import re
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote

import httpx

from . import __version__
from .cache import DiskCache
from .params import BASE_URL

ALLOWED_HOST = httpx.URL(BASE_URL).host
FORBIDDEN_PATH_PREFIX = "/api/proxy/redirect"
MIN_INTERVAL = 1.0
TIMEOUT = httpx.Timeout(20.0, connect=10.0)  # per fase (verbinden, lezen per chunk, ...)
DEADLINE = 60.0  # totaal per verzoek, inclusief redirects en het lezen van de body
MAX_REDIRECTS = 5
SIZE_LIMIT = 8 * 1024 * 1024
DEFAULT_RETRY_AFTER = 300
MAX_RETRY_AFTER = 3600
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ClientError(Exception):
    """Korte foutmelding voor de gebruiker; ``retry_after`` bij 429/503."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _normalized_path(path: str) -> str:
    """Pad zoals de server het waarschijnlijk leest: gedecodeerd, dubbele slashes en ``..`` weg, klein."""
    return posixpath.normpath(re.sub(r"/+", "/", "/" + unquote(path))).lower()


async def _only_allowed_hosts(request: httpx.Request) -> None:
    """Request-hook: draait ook bij elke redirect-stap (SSRF-bescherming)."""
    url = request.url
    if request.method != "GET":
        raise ClientError("Only GET requests are allowed")
    if url.scheme != "https" or url.host != ALLOWED_HOST or url.port not in (None, 443):
        raise ClientError(f"Blocked request to unexpected host '{url.host}' (SSRF protection)")
    if _normalized_path(url.path).startswith(FORBIDDEN_PATH_PREFIX):
        raise ClientError(f"Refusing to fetch forbidden path '{url.path}'")


def _parse_retry_after(value: str | None) -> int:
    """``Retry-After`` als seconden of HTTP-datum, begrensd op 1…3600 s; standaard 300 s."""
    seconds: float = DEFAULT_RETRY_AFTER
    if value:
        value = value.strip()
        with contextlib.suppress(ValueError, TypeError, OverflowError):
            seconds = (
                int(value) if value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
            )
    return int(max(1, min(seconds, MAX_RETRY_AFTER)))


class GaspedaalClient:
    def __init__(self, cache: DiskCache | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.cache = cache if cache is not None else DiskCache()
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._blocked_until = 0.0
        self._client = httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=TIMEOUT,
            event_hooks={"request": [_only_allowed_hosts]},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_html(self, url: str) -> str:
        """Haalt één pagina op; ``ClientError`` bij blokkade, pauze, HTTP-fout of te grote respons.

        De lock dekt het hele verzoek, dus er loopt hoogstens één tegelijk.
        """
        async with self._lock:
            remaining = self._blocked_until - time.monotonic()
            if remaining > 0:
                n = round(remaining)
                raise ClientError(f"Gaspedaal asked to slow down; paused for another {n} s", retry_after=n)
            wait = self._last_request + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                async with asyncio.timeout(DEADLINE), self._client.stream("GET", url) as response:
                    return await self._read_limited(response)
            except TimeoutError:
                raise ClientError(
                    f"Request to Gaspedaal failed: no complete answer within {DEADLINE:.0f} s"
                ) from None
            except httpx.HTTPError as exc:
                raise ClientError(f"Request to Gaspedaal failed: {exc}") from exc

    async def _read_limited(self, response: httpx.Response) -> str:
        status = response.status_code
        if status in (429, 503):
            retry = _parse_retry_after(response.headers.get("Retry-After"))
            self._blocked_until = max(self._blocked_until, time.monotonic() + retry)
            raise ClientError(
                f"Gaspedaal asked to slow down (HTTP {status}); pausing {retry} s", retry_after=retry
            )
        if status == 403:
            raise ClientError(
                f"Gaspedaal blocked the request (HTTP {status}); try again later, don't retry now"
            )
        if status == 404:
            raise ClientError("Unknown brand or model; use list_brands / list_models")
        if status != 200:
            raise ClientError(f"Request to Gaspedaal failed: HTTP {status}")

        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > SIZE_LIMIT:
            raise ClientError("Response too large")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > SIZE_LIMIT:
                raise ClientError("Response too large")
        return body.decode("utf-8", errors="replace")

    async def get_parsed(
        self, url: str, ttl_seconds: int, parser: Callable[[str], dict[str, Any]], namespace: str = ""
    ) -> dict[str, Any]:
        """Pagina ophalen en parsen, met cache onder ``versie:namespace:url``.

        De namespace scheidt tools die dezelfde URL anders parsen (bv. list_models en search).
        De versie zorgt dat een update met een andere parser geen oude uitvoer uit de cache krijgt.
        """
        key = f"{__version__}:{namespace}:{url}"
        cached = self.cache.get(key, ttl_seconds)
        if cached is not None:
            return cached
        value = parser(await self.get_html(url))
        self.cache.set(key, value)
        return value
