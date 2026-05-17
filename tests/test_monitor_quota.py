"""Tests for monitor.get_quota_status — RPD + RPM + uncapped models."""

from __future__ import annotations

import httpx
import pytest

from echeneis.bot.gateway_client import GatewayClient
from echeneis.bot.monitor import get_quota_status


def _make_client(models_payload: dict) -> GatewayClient:
    """Build a GatewayClient backed by a mocked /models response."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=models_payload)
    )
    gw = GatewayClient(base_url="http://test:4000")
    gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")
    return gw


@pytest.mark.asyncio
async def test_includes_models_with_no_rpd_cap():
    """Models with limit_rpd=0 must still appear (e.g. Mistral, NVIDIA)."""
    gw = _make_client(
        {
            "models": [
                {
                    "name": "mistral-large-3",
                    "usage": {
                        "used_rpd": 4,
                        "limit_rpd": 0,
                        "used_rpm": 0,
                        "limit_rpm": 60,
                    },
                },
            ]
        }
    )
    quotas = await get_quota_status(gw)
    await gw.close()

    assert len(quotas) == 1
    assert quotas[0]["model"] == "mistral-large-3"
    assert quotas[0]["limit_rpd"] == 0
    assert quotas[0]["rpd_pct"] is None
    assert quotas[0]["rpm_pct"] == 0.0
    assert quotas[0]["pct"] == 0.0  # falls back to RPM


@pytest.mark.asyncio
async def test_fully_uncapped_model_reports_none_pct():
    """A model with both caps at 0 has pct=None so it never alerts."""
    gw = _make_client(
        {
            "models": [
                {
                    "name": "nvidia-mistral-large-3",
                    "usage": {
                        "used_rpd": 2,
                        "limit_rpd": 0,
                        "used_rpm": 1,
                        "limit_rpm": 0,
                    },
                },
            ]
        }
    )
    quotas = await get_quota_status(gw)
    await gw.close()

    assert quotas[0]["pct"] is None
    assert quotas[0]["rpd_pct"] is None
    assert quotas[0]["rpm_pct"] is None


@pytest.mark.asyncio
async def test_pct_picks_higher_of_rpd_rpm():
    """pct must reflect the binding cap, i.e. whichever is more utilised."""
    gw = _make_client(
        {
            "models": [
                {
                    "name": "gemini-2.5-flash",
                    "usage": {
                        "used_rpd": 50,
                        "limit_rpd": 500,  # 10%
                        "used_rpm": 8,
                        "limit_rpm": 10,  # 80%
                    },
                },
            ]
        }
    )
    quotas = await get_quota_status(gw)
    await gw.close()

    assert quotas[0]["rpd_pct"] == 10.0
    assert quotas[0]["rpm_pct"] == 80.0
    assert quotas[0]["pct"] == 80.0


@pytest.mark.asyncio
async def test_rpm_only_model_reports_rpm_pct():
    """Model with only RPM cap (no RPD) still gets a pct from RPM."""
    gw = _make_client(
        {
            "models": [
                {
                    "name": "nvidia-nemotron-super-49b",
                    "usage": {
                        "used_rpd": 5,
                        "limit_rpd": 0,
                        "used_rpm": 20,
                        "limit_rpm": 40,
                    },
                },
            ]
        }
    )
    quotas = await get_quota_status(gw)
    await gw.close()

    assert quotas[0]["pct"] == 50.0


@pytest.mark.asyncio
async def test_missing_usage_fields_default_to_zero():
    """Tolerate gateway responses missing some fields."""
    gw = _make_client(
        {
            "models": [
                {"name": "weird-model", "usage": {}},
            ]
        }
    )
    quotas = await get_quota_status(gw)
    await gw.close()

    assert quotas[0]["used_rpd"] == 0
    assert quotas[0]["used_rpm"] == 0
    assert quotas[0]["pct"] is None
