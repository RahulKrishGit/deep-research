"""Tests for the run's shared HTTP connection pool."""

from __future__ import annotations

import httpx
import pytest

import deep_research.tools.http_pool as http_pool_module
from deep_research.tools.http_pool import SharedConnectionPool, shared_connection_pool


class _CountingTransport(httpx.AsyncBaseTransport):
    """Stands in for httpx's own transport: answers 200, counts what it sees."""

    built = 0

    def __init__(self) -> None:
        type(self).built += 1
        self.requests: list[str] = []
        self.closed = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return httpx.Response(200, text="ok", request=request)

    async def aclose(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_every_client_over_the_pool_shares_one_transport_that_outlives_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[_CountingTransport] = []

    def build() -> _CountingTransport:
        transport = _CountingTransport()
        built.append(transport)
        return transport

    monkeypatch.setattr(http_pool_module.httpx, "AsyncHTTPTransport", build)
    pool = SharedConnectionPool()

    for page in ("a", "b"):
        async with httpx.AsyncClient(transport=pool) as client:
            await client.get(f"https://example.test/{page}")

    assert len(built) == 1
    assert built[0].requests == ["https://example.test/a", "https://example.test/b"]
    assert built[0].closed == 0
    await pool.close()
    assert built[0].closed == 1


def test_there_is_no_pool_when_the_environment_names_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_pool_module, "getproxies", lambda: {"https": "http://proxy.test:8080"}
    )
    assert shared_connection_pool() is None

    monkeypatch.setattr(http_pool_module, "getproxies", lambda: {})
    assert isinstance(shared_connection_pool(), SharedConnectionPool)
