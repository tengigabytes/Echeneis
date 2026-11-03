"""Tests for monitor.find_failing_models and the daily probe schedule."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from echeneis.bot.gateway_client import GatewayClient
from echeneis.bot.monitor import find_failing_models, next_probe_time

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


_TW = timezone(timedelta(hours=8))
_JST = timezone(timedelta(hours=9))


def _tw(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=_TW)


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        # Morning deploy: probe the same evening.
        (_tw(3, 10, 40), _tw(3, 22)),
        # Exactly at the probe time: the next one is tomorrow, so no double run.
        (_tw(3, 22), _tw(4, 22)),
        # Late-night deploy: wait for tomorrow evening, not 24 h from now.
        (_tw(3, 23, 50), _tw(4, 22)),
        # Host clock in JST: 22:30 JST is 21:30 in Taiwan, still before 22:00.
        (datetime(2026, 11, 3, 22, 30, tzinfo=_JST), _tw(3, 22)),
        # Month boundary.
        (_tw(30, 23), _tw(1, 22, month=12)),
    ],
)
def test_next_probe_time_is_22_taiwan(after, expected):
    nxt = next_probe_time(after)
    assert nxt == expected
    assert nxt.utcoffset() == timedelta(hours=8)


def test_next_probe_time_does_not_drift():
    """Chaining from the previous run stays on 22:00 day after day."""
    run = next_probe_time(_tw(3, 23, 59))
    for _ in range(30):
        run = next_probe_time(run)
        assert (run.hour, run.minute, run.second) == (22, 0, 0)
    assert run == _tw(4, 22, month=12)
