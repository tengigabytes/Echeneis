"""Tiered routing engine with failover.

Routes classified requests to the appropriate model via litellm,
with automatic failover on rate limits, server errors, and timeouts.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

import litellm

from echeneis.gateway.classifier import Classification
from echeneis.gateway.config import RoutingConfig
from echeneis.gateway.health import HealthTracker

logger = logging.getLogger(__name__)


@dataclass
class ModelEntry:
    """A resolved model entry from litellm_config.yaml."""

    litellm_model: str
    api_key: str | None = None
    api_base: str | None = None


# Maps short model names to their resolved litellm params.
ModelRegistry = dict[str, ModelEntry]


def _resolve_env_ref(value: str) -> str | None:
    """Resolve 'os.environ/VAR_NAME' references to actual env var values.

    Args:
        value: Raw value from litellm_config.yaml.

    Returns:
        Resolved value, or None if the env var is unset/empty.
    """
    if isinstance(value, str) and value.startswith("os.environ/"):
        var_name = value[len("os.environ/"):]
        return os.environ.get(var_name) or None
    return value


def build_model_registry(litellm_config_path: str) -> ModelRegistry:
    """Build a mapping from short model names to resolved litellm params.

    Args:
        litellm_config_path: Path to litellm_config.yaml.

    Returns:
        Dict mapping e.g. "gemma-4-31b" → ModelEntry(litellm_model, api_key, ...).
    """
    import yaml

    with open(litellm_config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    registry: ModelRegistry = {}
    for entry in config.get("model_list", []):
        short_name = entry["model_name"]
        params = entry["litellm_params"]
        litellm_model = params["model"]

        api_key = _resolve_env_ref(params.get("api_key", ""))
        api_base_raw = params.get("api_base")
        api_base = os.path.expandvars(api_base_raw) if api_base_raw else None

        registry[short_name] = ModelEntry(
            litellm_model=litellm_model,
            api_key=api_key,
            api_base=api_base,
        )

    return registry


class Router:
    """Routes requests through the tier system with failover.

    Flow: Classification → pick primary model → call litellm →
    on failure → pick fallback model → call litellm → return or raise.
    """

    def __init__(
        self,
        routing_config: RoutingConfig,
        model_registry: ModelRegistry,
        health_tracker: HealthTracker,
    ) -> None:
        self._config = routing_config
        self._registry = model_registry
        self._health = health_tracker

    def _resolve_model(self, short_name: str) -> ModelEntry:
        """Resolve short model name to its ModelEntry."""
        entry = self._registry.get(short_name)
        if not entry:
            raise ValueError(
                f"Model '{short_name}' not found in litellm config. "
                f"Available: {list(self._registry.keys())}"
            )
        return entry

    def _pick_models(self, classification: Classification) -> list[str]:
        """Pick primary + fallback model names for a classification.

        Returns:
            List of short model names to try in order.
        """
        tier = self._config.tiers.get(classification.tier)
        if not tier:
            raise ValueError(f"Unknown tier: {classification.tier}")

        candidates: list[str] = []

        # Primary model for this task type
        primary = tier.get_model(classification.task_type)
        if primary:
            candidates.append(primary)

        # Fallback model for this task type
        fallback = tier.get_fallback(classification.task_type)
        if fallback and fallback not in candidates:
            candidates.append(fallback)

        # If no task-specific model found, try first available in tier
        if not candidates:
            for model_name in tier.models.values():
                if model_name not in candidates:
                    candidates.append(model_name)
                    break

        return candidates

    async def route(
        self,
        classification: Classification,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        """Route a request to the appropriate model with failover.

        Args:
            classification: The classified task type and tier.
            messages: OpenAI-format messages list.
            **kwargs: Additional params passed to litellm.acompletion.

        Returns:
            litellm ModelResponse.

        Raises:
            RuntimeError: If all candidate models fail.
        """
        candidates = self._pick_models(classification)
        timeout = self._config.failover.timeout_seconds
        last_error: Exception | None = None

        for short_name in candidates:
            if not self._health.is_available(short_name):
                logger.info("Skipping %s — unavailable", short_name)
                continue

            entry = self._resolve_model(short_name)
            logger.info(
                "Routing %s/%s → %s (%s)",
                classification.tier,
                classification.task_type,
                short_name,
                entry.litellm_model,
            )

            # Build call kwargs — only pass api_key/api_base if set
            call_kwargs: dict[str, Any] = {
                "model": entry.litellm_model,
                "messages": messages,
                "timeout": timeout,
                **kwargs,
            }
            if entry.api_key:
                call_kwargs["api_key"] = entry.api_key
            if entry.api_base:
                call_kwargs["api_base"] = entry.api_base

            try:
                response = await litellm.acompletion(**call_kwargs)
                self._health.record_success(short_name)
                return response

            except litellm.RateLimitError:
                logger.warning("Rate limited on %s, failing over", short_name)
                self._health.record_failure(short_name, 429)
                last_error = litellm.RateLimitError(
                    message=f"Rate limited: {short_name}",
                    llm_provider=short_name,
                    model=entry.litellm_model,
                )

            except litellm.APIStatusError as e:
                status = getattr(e, "status_code", 500)
                logger.warning("API error %d on %s, failing over", status, short_name)
                self._health.record_failure(short_name, status)
                last_error = e

            except litellm.Timeout:
                logger.warning("Timeout on %s, failing over", short_name)
                self._health.record_failure(short_name, 0)
                last_error = litellm.Timeout(
                    message=f"Timeout: {short_name}",
                    llm_provider=short_name,
                    model=entry.litellm_model,
                )

            except Exception as e:
                logger.warning(
                    "Unexpected error on %s: %s, failing over",
                    short_name,
                    e,
                )
                self._health.record_failure(short_name, 500)
                last_error = e

        raise RuntimeError(
            f"All models exhausted for {classification.tier}/"
            f"{classification.task_type}: {[c for c in candidates]}"
        ) from last_error
