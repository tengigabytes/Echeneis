"""Pytest fixtures for benchmark suite.

Provides the gateway client, model list, and skip-if-unreachable logic.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from echeneis.bot.gateway_client import GatewayClient, GatewayError


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for benchmark dimensions."""
    config.addinivalue_line("markers", "benchmark: all benchmark tests")
    config.addinivalue_line("markers", "latency: latency measurement")
    config.addinivalue_line("markers", "context_recall: long context recall")
    config.addinivalue_line("markers", "vision: vision capability")
    config.addinivalue_line("markers", "code_review: embedded C code review")
    config.addinivalue_line("markers", "translation: translation consistency")
    config.addinivalue_line("markers", "rate_limit: rate limit stress test")
    config.addinivalue_line("markers", "multi_turn: multi-turn conversation")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --model CLI option for filtering benchmark models."""
    parser.addoption(
        "--model",
        action="append",
        default=None,
        help="Model(s) to benchmark (can be specified multiple times)",
    )


@pytest.fixture(scope="session")
def gateway_url() -> str:
    """Gateway URL from environment or default."""
    return os.environ.get("ECHENEIS_GATEWAY_URL", "http://localhost:4000")


@pytest_asyncio.fixture(scope="session")
async def gateway_client(gateway_url: str) -> GatewayClient:
    """Session-scoped gateway client.

    Skips the entire benchmark suite if the gateway is unreachable.
    """
    client = GatewayClient(gateway_url)
    try:
        await client.health()
    except (GatewayError, Exception):
        pytest.skip("Gateway unreachable — start the gateway before running benchmarks")
    yield client
    await client.close()


@pytest_asyncio.fixture(scope="session")
async def available_models(gateway_client: GatewayClient) -> list[str]:
    """List of model names that are available and have valid API keys."""
    resp = await gateway_client.models()
    return [
        m["name"]
        for m in resp.get("models", [])
        if m.get("available") and m.get("has_key")
    ]


@pytest.fixture(scope="session")
def requested_models(request: pytest.FixtureRequest) -> list[str] | None:
    """Models requested via --model CLI option."""
    return request.config.getoption("--model")


@pytest.fixture(scope="session")
def target_models(
    available_models: list[str],
    requested_models: list[str] | None,
) -> list[str]:
    """Final list of models to benchmark."""
    if requested_models:
        targets = [m for m in requested_models if m in available_models]
        if not targets:
            pytest.skip(
                f"Requested models {requested_models} not available. "
                f"Available: {available_models}"
            )
        return targets
    return available_models
