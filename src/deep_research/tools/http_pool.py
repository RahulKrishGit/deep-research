"""One run's HTTP connection pool, lent to every page and document read.

Latency audit O4: each ``web_scraper`` and ``document_reader`` call opened a
client of its own, so every read paid its own connection and TLS handshake --
robots.txt and page alike -- even to a host a sibling loop had just read. The
pool below is shared by the run's reads instead. Each call still builds its own
``httpx.AsyncClient`` over it, so what a request carries -- its headers, its
cookies (a call's own, never a sibling's), its redirects and timeouts -- is
exactly what it carried before; only the connections underneath are reused.
"""

from __future__ import annotations

from urllib.request import getproxies

import httpx


class SharedConnectionPool(httpx.AsyncBaseTransport):
    """The transport every read's own client is built over, for one run.

    The real transport is built on first use with httpx's defaults: the one a
    per-call client builds for itself. A per-call client closes its transport
    when its ``async with`` ends, and this one outlives it, so ``aclose`` does
    nothing; the run that owns the pool closes it once, with ``close``.
    """

    def __init__(self) -> None:
        self._transport: httpx.AsyncHTTPTransport | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._transport is None:
            self._transport = httpx.AsyncHTTPTransport()
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        """A per-call client's own exit: the run's pool stays open."""

    async def close(self) -> None:
        """Close the pool's connections; a later read opens new ones."""
        transport, self._transport = self._transport, None
        if transport is not None:
            await transport.aclose()


def shared_connection_pool() -> SharedConnectionPool | None:
    """A pool for one run, or ``None`` when the environment names a proxy.

    httpx reads proxy settings from the environment only for a client that
    builds its own transport, so a client built over this pool would bypass a
    configured ``HTTP_PROXY``, ``HTTPS_PROXY`` or ``ALL_PROXY`` (or the
    platform's own proxy settings, as ``urllib.request.getproxies`` reads
    them). With one configured, every read keeps building its own client,
    exactly as before.
    """
    proxies = getproxies()
    if any(proxies.get(scheme) for scheme in ("http", "https", "all")):
        return None
    return SharedConnectionPool()
