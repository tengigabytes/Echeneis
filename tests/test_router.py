"""Tests for tiered router."""

from unittest.mock import AsyncMock, patch

import pytest

from echeneis.gateway.classifier import Classification
from echeneis.gateway.config import FailoverConfig, RoutingConfig
from echeneis.gateway.health import HealthTracker
from echeneis.gateway.router import ModelEntry, Router

_TEST_CONFIG = RoutingConfig.from_dict(
    {
        "tiers": {
            "A": {
                "description": "General",
                "models": {
                    "translation": "mistral-large-3",
                    "general_qa": "cerebras-llama-70b",
                },
                "fallback": {
                    "translation": "gemma-4-26b",
                    "general_qa": "groq-llama-70b",
                },
            },
        },
        "task_types": [],
        "failover": {
            "on_timeout": {"timeout_seconds": 5},
            "circuit_breaker": {"consecutive_failures": 3},
        },
        "cross_check": {},
    }
)

_REGISTRY = {
    "mistral-large-3": ModelEntry("mistral/mistral-large-latest", api_key="sk-test"),
    "gemma-4-26b": ModelEntry("gemini/gemma-4-26b-a4b-it", api_key="sk-test"),
    "cerebras-llama-70b": ModelEntry("cerebras/llama3.3-70b", api_key="sk-test"),
    "groq-llama-70b": ModelEntry("groq/llama-3.3-70b-versatile", api_key="sk-test"),
}


def _router() -> Router:
    health = HealthTracker(FailoverConfig())
    return Router(_TEST_CONFIG, _REGISTRY, health)


class TestModelSelection:
    def test_pick_primary_and_fallback(self) -> None:
        router = _router()
        classification = Classification(task_type="translation", tier="A")
        candidates = router._pick_models(classification)
        assert candidates == ["mistral-large-3", "gemma-4-26b"]

    def test_unknown_tier_raises(self) -> None:
        router = _router()
        classification = Classification(task_type="translation", tier="Z")
        with pytest.raises(ValueError, match="Unknown tier"):
            router._pick_models(classification)


class TestRouting:
    @pytest.mark.asyncio
    async def test_successful_route(self) -> None:
        router = _router()
        mock_response = AsyncMock()
        mock_response.return_value = {"choices": [{"message": {"content": "hi"}}]}

        with patch("echeneis.gateway.router.litellm.acompletion", mock_response):
            await router.route(
                Classification(task_type="translation", tier="A"),
                [{"role": "user", "content": "translate hello"}],
            )
        mock_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_failover_on_rate_limit(self) -> None:
        import litellm

        router = _router()
        call_count = 0

        async def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise litellm.RateLimitError(
                    message="rate limited",
                    llm_provider="mistral",
                    model="mistral/mistral-large-latest",
                )
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch(
            "echeneis.gateway.router.litellm.acompletion",
            side_effect=mock_completion,
        ):
            await router.route(
                Classification(task_type="translation", tier="A"),
                [{"role": "user", "content": "translate hello"}],
            )
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_models_fail_raises(self) -> None:
        import litellm

        router = _router()

        async def always_fail(**kwargs):
            raise litellm.RateLimitError(
                message="rate limited",
                llm_provider="test",
                model="test",
            )

        with patch(
            "echeneis.gateway.router.litellm.acompletion",
            side_effect=always_fail,
        ):
            with pytest.raises(RuntimeError, match="All models exhausted"):
                await router.route(
                    Classification(task_type="translation", tier="A"),
                    [{"role": "user", "content": "translate hello"}],
                )
