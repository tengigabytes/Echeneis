"""Dimension 6: Rate limit stress test.

Sends 50 requests with 5 concurrent workers through auto-routing
(no model pinning) to test gateway failover and throughput under load.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from echeneis.bot.gateway_client import GatewayError

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

NUM_REQUESTS = 50
CONCURRENCY = 5
PROMPT = "Respond with exactly one word: hello."


class RateLimitBenchmark:
    """Measures gateway throughput and failover under concurrent load."""

    name: str = "rate_limit"
    requires_vision: bool = False
    run_once: bool = True  # Not per-model — tests auto-routing

    async def run(self, harness: BenchmarkHarness) -> BenchmarkResult:
        """Run stress test against the gateway.

        Args:
            harness: The benchmark harness.

        Returns:
            BenchmarkResult with success rate and throughput metrics.
        """
        # Use a fresh client without per-provider throttling
        assert harness._client is not None
        client = harness._client

        semaphore = asyncio.Semaphore(CONCURRENCY)
        successes = 0
        failures = 0
        latencies: list[float] = []
        errors: list[str] = []

        async def _send_one(idx: int) -> None:
            nonlocal successes, failures
            async with semaphore:
                t0 = time.perf_counter()
                try:
                    await client.chat(
                        [{"role": "user", "content": PROMPT}],
                        max_tokens=20,
                    )
                    latencies.append((time.perf_counter() - t0) * 1000)
                    successes += 1
                except (GatewayError, Exception) as e:
                    latencies.append((time.perf_counter() - t0) * 1000)
                    failures += 1
                    errors.append(f"req {idx}: {e}")

        t_start = time.perf_counter()
        tasks = [_send_one(i) for i in range(NUM_REQUESTS)]
        await asyncio.gather(*tasks)
        total_ms = (time.perf_counter() - t_start) * 1000

        success_rate = successes / NUM_REQUESTS if NUM_REQUESTS > 0 else 0.0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        return harness.make_result(
            dimension=self.name,
            model="auto",
            score=success_rate,
            raw={
                "total_requests": NUM_REQUESTS,
                "concurrency": CONCURRENCY,
                "successes": successes,
                "failures": failures,
                "success_rate": round(success_rate, 4),
                "total_ms": round(total_ms, 1),
                "avg_latency_ms": round(avg_latency, 1),
                "errors": errors[:10],  # cap error list
            },
            duration_ms=round(total_ms, 1),
        )
