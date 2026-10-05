"""Tests for monitor.find_failing_models — daily dead-model detection."""

from __future__ import annotations

import json

import httpx
import pytest

from echeneis.bot.gateway_client import GatewayClient
from echeneis.bot.monitor import find_failing_models

_MODELS = {
    "models": [
        {"name": "alive", "has_key": True, "available": True},
        {"name": "retired", "has_key": True, "available": False},
        {"name": "flaky", "has_key": True, "available": True},
        {"name": "keyless", "has_key": False, "available": False},
    ]
}


def _make_client(handler) -> GatewayClient:
    """Build a GatewayClient backed by a mocked transport."""
    gw = GatewayClient(base_url="http://test:4000")
    gw._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test:4000"
    )
    return gw


@pytest.mark.asyncio
async def test_reports_only_models_failing_both_rounds():
    calls: dict[str, int] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/models":
            return httpx.Response(200, json=_MODELS)
        model = json.loads(req.content)["model"]
        calls[model] = calls.get(model, 0) + 1
        if model == "retired" or (model == "flaky" and calls[model] == 1):
            return httpx.Response(502, json={"detail": "upstream failed"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    gw = _make_client(handler)
    failing = await find_failing_models(gw, retry_delay=0)
    await gw.close()

    assert failing == ["retired"]
    # Open-circuit models are still probed; keyless ones never are.
    assert calls == {"alive": 1, "retired": 2, "flaky": 2}


@pytest.mark.asyncio
async def test_all_healthy_returns_empty():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/models":
            return httpx.Response(200, json=_MODELS)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    gw = _make_client(handler)
    failing = await find_failing_models(gw, retry_delay=0)
    await gw.close()

    assert failing == []
