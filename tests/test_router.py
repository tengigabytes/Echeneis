"""Tests for tiered router."""

from unittest.mock import AsyncMock, patch

import pytest

from echeneis.gateway.classifier import Classification
from echeneis.gateway.config import FailoverConfig, RoutingConfig
from echeneis.gateway.health import HealthTracker
from echeneis.gateway.router import ModelEntry, Router
from echeneis.gateway.usage import UsageTracker

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


def _router(usage: UsageTracker | None = None) -> Router:
    health = HealthTracker(FailoverConfig())
    return Router(_TEST_CONFIG, _REGISTRY, health, usage_tracker=usage)


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
    async def test_route_direct_records_usage(self) -> None:
        """/use and /health probes hit route_direct — must still bump RPM/RPD."""
        usage = UsageTracker()
        usage.set_limits("mistral-large-3", rpm=60, rpd=0)
        router = _router(usage)
        mock_response = AsyncMock(
            return_value={"choices": [{"message": {"content": "hi"}}]}
        )

        with patch("echeneis.gateway.router.litellm.acompletion", mock_response):
            await router.route_direct(
                "mistral-large-3",
                [{"role": "user", "content": "hi"}],
            )

        row = usage.get_all_usage().get("mistral-large-3", {})
        assert row.get("used_rpd", 0) == 1

    @pytest.mark.asyncio
    async def test_route_records_usage(self) -> None:
        """Sanity: classified routes still record (regression guard)."""
        usage = UsageTracker()
        usage.set_limits("mistral-large-3", rpm=60, rpd=0)
        router = _router(usage)
        mock_response = AsyncMock(
            return_value={"choices": [{"message": {"content": "hi"}}]}
        )

        with patch("echeneis.gateway.router.litellm.acompletion", mock_response):
            await router.route(
                Classification(task_type="translation", tier="A"),
                [{"role": "user", "content": "hi"}],
            )

        row = usage.get_all_usage().get("mistral-large-3", {})
        assert row.get("used_rpd", 0) == 1

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


class TestModelEntryExtraBody:
    """Per-model extra_body merges into litellm call kwargs."""

    @pytest.mark.asyncio
    async def test_extra_body_forwarded_on_route(self) -> None:
        registry = {
            "mistral-large-3": ModelEntry(
                "mistral/mistral-large-latest",
                api_key="sk-test",
                extra_body={
                    "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}}
                },
            ),
            "gemma-4-26b": ModelEntry("gemini/gemma-4-26b-a4b-it", api_key="sk-test"),
            "cerebras-llama-70b": ModelEntry(
                "cerebras/llama3.3-70b", api_key="sk-test"
            ),
            "groq-llama-70b": ModelEntry(
                "groq/llama-3.3-70b-versatile", api_key="sk-test"
            ),
        }
        health = HealthTracker(FailoverConfig())
        router = Router(_TEST_CONFIG, registry, health)

        captured: dict = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch("echeneis.gateway.router.litellm.acompletion", side_effect=capture):
            await router.route(
                Classification(task_type="translation", tier="A"),
                [{"role": "user", "content": "hi"}],
            )

        assert captured.get("extra_body") == {
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}}
        }

    def test_caller_kwargs_win_on_top_level_conflict(self) -> None:
        entry = ModelEntry("x/y", extra_body={"generationConfig": {"a": 1}, "other": 2})
        kwargs = {"extra_body": {"generationConfig": {"b": 2}}}
        Router._apply_entry_extras(kwargs, entry)
        # Caller-supplied generationConfig wins (no deep merge).
        assert kwargs["extra_body"] == {
            "generationConfig": {"b": 2},
            "other": 2,
        }

    def test_no_extra_body_is_noop(self) -> None:
        entry = ModelEntry("x/y")
        kwargs = {"model": "x/y"}
        Router._apply_entry_extras(kwargs, entry)
        assert "extra_body" not in kwargs


class TestBuildModelRegistry:
    """litellm_config.yaml parsing surfaces extra_body into ModelEntry."""

    def test_extra_body_parsed(self, tmp_path) -> None:
        from echeneis.gateway.router import build_model_registry

        cfg = tmp_path / "litellm.yaml"
        cfg.write_text(
            "model_list:\n"
            "  - model_name: gemma-4-31b\n"
            "    litellm_params:\n"
            "      model: gemini/gemma-4-31b-it\n"
            "      extra_body:\n"
            "        generationConfig:\n"
            "          thinkingConfig:\n"
            "            thinkingBudget: 0\n",
            encoding="utf-8",
        )
        reg = build_model_registry(str(cfg))
        assert reg["gemma-4-31b"].extra_body == {
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}}
        }

    def test_invalid_extra_body_rejected(self, tmp_path) -> None:
        from echeneis.gateway.router import build_model_registry

        cfg = tmp_path / "litellm.yaml"
        cfg.write_text(
            "model_list:\n"
            "  - model_name: bad\n"
            "    litellm_params:\n"
            "      model: x/y\n"
            "      extra_body: not-a-dict\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            build_model_registry(str(cfg))
