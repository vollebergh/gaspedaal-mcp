"""HTTP-client met host-allowlist, throttle, back-off en groottelimiet.

Alleen ``https://www.gaspedaal.nl``, alleen ``GET``, nooit
``/api/proxy/redirect/...``. Eén verzoek tegelijk, minstens 1,0 s ertussen.
"""

from __future__ import annotations

import asyncio
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .cache import DiskCache

ALLOWED_HOST = "www.gaspedaal.nl"
BASE_URL = f"https://{ALLOWED_HOST}"

FORBIDDEN_PATH_PREFIX = "/api/proxy/redirect/"

MIN_INTERVAL = 1.0
SIZE_LIMIT = 8 * 1024 * 1024

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


class ClientError(Exception):
    """Fout met een korte, gegradeerde melding voor de gebruiker."""

    def __init__(self, message: str, code: int | None = None, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


async def _only_allowed_hosts(request: httpx.Request) -> None:
    if request.method != "GET":
        raise ClientError("Only GET requests are allowed")
    url = httpx.URL(request.url)
    if url.scheme != "https" or url.host != ALLOWED_HOST:
        raise ClientError(f"Blocked request to unexpected host '{url.host}' (SSRF protection)")
    if url.path.startswith(FORBIDDEN_PATH_PREFIX):
        raise ClientError(f"Refusing to fetch forbidden path '{url.path}'")


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        n = int(value)
        return max(1, min(n, 3600))
    try:
        dt = parsedate_to_datetime(value)
        now = time.time()
        if dt is not None:
            return max(1, min(int(dt.timestamp() - now), 3600))
    except (ValueError, TypeError, OverflowError):
        pass
    return None


class GaspedaalClient:
    """Dunne, async client bovenop httpx met alle beschermingen."""

    def __init__(
        self,
        cache: DiskCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache = cache if cache is not None else DiskCache()
        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_request = 0.0
        self._blocked_until = 0.0
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                event_hooks={"request": [_only_allowed_hosts]},
            )
        return self._client

    def cached(self, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        return self.cache.get_with_ttl(key, ttl_seconds)

    def set_cached(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self.cache.set(key, value, ttl_seconds)

    def _check_blocked(self) -> None:
        remaining = self._blocked_until - time.monotonic()
        if remaining > 0:
            n = round(remaining)
            raise ClientError(
                f"Gaspedaal asked to slow down; paused for another {n} s", code=429, retry_after=n
            )

    async def _throttle(self) -> None:
        async with self._lock:
            self._check_blocked()
            wait = self._last_request + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def get_html(self, url: str) -> str:
        """Haalt één pagina op en geeft de HTML terug.

        Raised ``ClientError`` bij netwerk-, formaat-, blokkade- of
        groottelimietproblemen. Behandelt 429/503 met Retry-After en onthoudt
        de pauze zodat volgende verzoeken direct een fout geven.

        De ``_request_lock`` serialiseert het *hele* verzoek (niet alleen de
        wachttijd), zodat er altijd hoogstens één verzoek tegelijk loopt.
        """
        client = self._get_client()
        async with self._request_lock:
            await self._throttle()
            try:
                # stream=True: de body wordt pas gelezen in _read_limited, zodat de
                # groottelimiet geldt vóórdat alles in het geheugen staat.
                async with client.stream("GET", url) as response:
                    return await self._read_limited(response)
            except httpx.HTTPError as exc:
                raise ClientError(f"Request to Gaspedaal failed: {exc}") from exc

    async def _read_limited(self, response: httpx.Response) -> str:
        """Controleert de status en leest de body met een harde groottelimiet."""
        status = response.status_code

        if status in (429, 503):
            retry = _parse_retry_after(response.headers.get("Retry-After"))
            retry = retry if retry is not None else 300
            self._blocked_until = max(self._blocked_until, time.monotonic() + retry)
            raise ClientError(
                f"Gaspedaal asked to slow down (HTTP {status}); pausing {retry} s",
                code=status,
                retry_after=retry,
            )

        if status == 403:
            raise ClientError(
                f"Gaspedaal blocked the request (HTTP {status}); try again later, don't retry now",
                code=status,
            )

        if status == 404:
            raise ClientError("Unknown brand or model; use list_brands / list_models", code=status)

        if status != 200:
            raise ClientError(f"Request to Gaspedaal failed: HTTP {status}", code=status)

        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > SIZE_LIMIT:
            raise ClientError("Response too large")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > SIZE_LIMIT:
                raise ClientError("Response too large")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def get_parsed(
        self,
        url: str,
        ttl_seconds: int,
        parser: Any,
        namespace: str = "",
    ) -> dict[str, Any]:
        """Haalt een pagina op en parseert hem, met cache.

        ``parser`` is een functie ``html -> dict``. ``ttl_seconds`` bepaalt de TTL.
        ``namespace`` onderscheidt de cache-entry per tool/soort zodat twee tools
        die dezelfde URL gebruiken niet elkaars geparste vorm overschrijven.
        """
        key = f"{namespace}:{url}" if namespace else url
        cached = self.cached(key, ttl_seconds)
        if cached is not None:
            return cached
        html = await self.get_html(url)
        value = parser(html)
        self.set_cached(key, value, ttl_seconds)
        return value
