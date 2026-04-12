"""Benchmark harness — orchestration, rate throttling, and result collection.

Runs benchmark dimensions against live gateway models, respecting
per-provider rate limits and collecting structured results.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from benchmarks.models import get_delay, get_provider, supports_vision
from echeneis.bot.gateway_client import GatewayClient

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark measurement."""

    timestamp: str
    git_sha: str
    dimension: str
    model: str
    score: float | None
    raw: dict[str, Any]
    duration_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON output."""
        return asdict(self)


def _git_sha() -> str:
    """Get current git commit SHA (short).

    Falls back to the GIT_SHA environment variable (set at Docker build
    time) when running inside a container without a .git directory.
    """
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha[:7]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@dataclass
class ProgressInfo:
    """Snapshot of benchmark progress, passed to the on_progress callback."""

    total_dimensions: int
    completed_dimensions: int
    current_dimension: str
    current_model: str | None
    models_in_dimension: int
    models_done_in_dimension: int
    partial_results: list[BenchmarkResult]


# Type alias for the progress callback.
ProgressCallback = Callable[[ProgressInfo], Any]


class ProviderThrottle:
    """Per-provider rate limiter using asyncio locks and timestamps."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def wait(self, provider: str) -> None:
        """Wait until it's safe to make a request to the given provider.

        Args:
            provider: Provider name, e.g. "groq".
        """
        if provider not in self._locks:
            self._locks[provider] = asyncio.Lock()

        async with self._locks[provider]:
            delay = get_delay(provider)
            last = self._last_request.get(provider, 0.0)
            elapsed = time.monotonic() - last
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request[provider] = time.monotonic()


class BenchmarkHarness:
    """Orchestrates benchmark execution with rate limiting.

    Iterates models x dimensions, skipping incompatible combinations,
    and collects structured results.
    """

    def __init__(
        self,
        gateway_url: str | None = None,
        models: list[str] | None = None,
        dimensions: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Initialize the harness.

        Args:
            gateway_url: Base URL of the running gateway.  None falls
                back to ECHENEIS_GATEWAY_URL env var, then localhost:4000.
            models: Model names to benchmark (None = all available).
            dimensions: Dimension names to run (None = all registered).
            on_progress: Optional async/sync callback invoked after each
                dimension starts and each model completes, receiving a
                :class:`ProgressInfo` snapshot.
        """
        self._gateway_url = gateway_url
        self._requested_models = models
        self._requested_dimensions = dimensions
        self._on_progress = on_progress
        self._throttle = ProviderThrottle()
        self._git_sha = _git_sha()
        self._client: GatewayClient | None = None
        self._available_models: list[dict[str, Any]] = []
        self._model_providers: dict[str, str] = {}

    async def _init_client(self) -> GatewayClient:
        """Create client and verify gateway connectivity."""
        client = GatewayClient(self._gateway_url)
        await client.health()
        models_resp = await client.models()
        self._available_models = [
            m
            for m in models_resp.get("models", [])
            if m.get("available") and m.get("has_key")
        ]
        for m in self._available_models:
            self._model_providers[m["name"]] = get_provider(m.get("litellm_model", ""))
        return client

    def _get_target_models(self) -> list[str]:
        """Resolve which models to benchmark."""
        available_names = [m["name"] for m in self._available_models]
        if self._requested_models:
            return [m for m in self._requested_models if m in available_names]
        return available_names

    def _get_dimensions(self) -> list:
        """Import and instantiate all requested dimension benchmarks."""
        from benchmarks.dimensions import registry

        if self._requested_dimensions:
            return [
                d
                for d in registry.ALL_DIMENSIONS
                if d.name in self._requested_dimensions
            ]
        return list(registry.ALL_DIMENSIONS)

    async def throttled_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat request with per-provider throttling.

        The throttle wait happens *before* the request, so the caller's
        timer around this method includes both the wait and the API call.
        Use :meth:`timed_chat` when you need to measure pure API latency.

        Args:
            model: Short model name.
            messages: OpenAI-format messages.
            **kwargs: Extra params for the gateway.

        Returns:
            Gateway response dict.
        """
        provider = self._model_providers.get(model, "unknown")
        await self._throttle.wait(provider)
        assert self._client is not None
        return await self._client.chat(messages, model=model, **kwargs)

    async def timed_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[dict[str, Any], float]:
        """Send a throttled request and return pure API latency.

        Waits for the provider throttle, then measures only the HTTP
        round-trip — the returned duration excludes throttle wait time.

        Args:
            model: Short model name.
            messages: OpenAI-format messages.
            **kwargs: Extra params for the gateway.

        Returns:
            Tuple of (response dict, latency in milliseconds).
        """
        provider = self._model_providers.get(model, "unknown")
        await self._throttle.wait(provider)
        assert self._client is not None
        t0 = time.perf_counter()
        resp = await self._client.chat(messages, model=model, **kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000
        return resp, latency_ms

    def make_result(
        self,
        dimension: str,
        model: str,
        score: float | None,
        raw: dict[str, Any],
        duration_ms: float,
        error: str | None = None,
    ) -> BenchmarkResult:
        """Create a BenchmarkResult with common fields populated.

        Args:
            dimension: Dimension name.
            model: Model short name.
            score: Normalized score (dimension-specific).
            raw: Raw measurement data.
            duration_ms: Wall-clock duration.
            error: Error message if failed.

        Returns:
            Populated BenchmarkResult.
        """
        return BenchmarkResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            git_sha=self._git_sha,
            dimension=dimension,
            model=model,
            score=score,
            raw=raw,
            duration_ms=duration_ms,
            error=error,
        )

    async def run(self) -> list[BenchmarkResult]:
        """Execute all requested benchmarks.

        Returns:
            List of BenchmarkResult for all model x dimension combinations.
        """
        self._client = await self._init_client()
        try:
            return await self._run_all()
        finally:
            await self._client.close()

    async def _emit_progress(self, info: ProgressInfo) -> None:
        """Fire the on_progress callback if set."""
        if self._on_progress is None:
            return
        try:
            ret = self._on_progress(info)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logger.debug("on_progress callback error", exc_info=True)

    async def _run_all(self) -> list[BenchmarkResult]:
        """Inner run loop after client is initialized."""
        models = self._get_target_models()
        dimensions = self._get_dimensions()
        results: list[BenchmarkResult] = []
        total_dims = len(dimensions)
        completed_dims = 0

        logger.info(
            "Benchmarking %d models x %d dimensions",
            len(models),
            len(dimensions),
        )

        for dim in dimensions:
            logger.info("=== Dimension: %s ===", dim.name)

            # Rate limit stress test runs once (not per-model)
            if getattr(dim, "run_once", False):
                await self._emit_progress(ProgressInfo(
                    total_dimensions=total_dims,
                    completed_dimensions=completed_dims,
                    current_dimension=dim.name,
                    current_model=None,
                    models_in_dimension=1,
                    models_done_in_dimension=0,
                    partial_results=list(results),
                ))
                try:
                    result = await dim.run(self)
                    results.append(result)
                except Exception as e:
                    logger.error("Dimension %s failed: %s", dim.name, e)
                    results.append(
                        self.make_result(dim.name, "auto", None, {}, 0, str(e))
                    )
                completed_dims += 1
                continue

            # Build list of eligible models for this dimension
            eligible = [
                m for m in models
                if not dim.requires_vision or supports_vision(m)
            ]
            models_done = 0

            # Emit progress at dimension start
            await self._emit_progress(ProgressInfo(
                total_dimensions=total_dims,
                completed_dimensions=completed_dims,
                current_dimension=dim.name,
                current_model=None,
                models_in_dimension=len(eligible),
                models_done_in_dimension=0,
                partial_results=list(results),
            ))

            for model in models:
                if dim.requires_vision and not supports_vision(model):
                    logger.info("Skipping %s/%s (no vision)", dim.name, model)
                    continue

                logger.info("Running %s / %s", dim.name, model)
                try:
                    result = await dim.run(self, model)
                    results.append(result)
                    logger.info(
                        "  score=%.2f  duration=%.0fms",
                        result.score if result.score is not None else -1,
                        result.duration_ms,
                    )
                except Exception as e:
                    logger.error("  FAILED: %s", e)
                    results.append(
                        self.make_result(dim.name, model, None, {}, 0, str(e))
                    )

                models_done += 1
                await self._emit_progress(ProgressInfo(
                    total_dimensions=total_dims,
                    completed_dimensions=completed_dims,
                    current_dimension=dim.name,
                    current_model=model,
                    models_in_dimension=len(eligible),
                    models_done_in_dimension=models_done,
                    partial_results=list(results),
                ))

            completed_dims += 1

        return results
