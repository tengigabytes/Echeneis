"""Dimension 1: Latency measurement.

Sends simple prompts to each model and measures response time.
Reports p50 and p95 latency in milliseconds.
"""

from __future__ import annotations

import statistics
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

NUM_REQUESTS = 10
PROMPT = "What is 2+2? Answer with just the number."


class LatencyBenchmark:
    """Measures response latency per model."""

    name: str = "latency"
    requires_vision: bool = False

    async def run(self, harness: BenchmarkHarness, model: str) -> BenchmarkResult:
        """Run latency measurement for a single model.

        Args:
            harness: The benchmark harness (provides throttled_chat).
            model: Short model name to benchmark.

        Returns:
            BenchmarkResult with p50/p95 in raw data.
        """
        messages = [{"role": "user", "content": PROMPT}]
        latencies: list[float] = []

        t_start = time.perf_counter()

        for i in range(NUM_REQUESTS):
            req_start = time.perf_counter()
            await harness.throttled_chat(model, messages, max_tokens=50)
            req_end = time.perf_counter()
            latencies.append((req_end - req_start) * 1000)

        total_ms = (time.perf_counter() - t_start) * 1000

        p50 = statistics.median(latencies)
        p95 = (
            statistics.quantiles(latencies, n=20)[18]
            if len(latencies) >= 20
            else max(latencies)
        )

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=p50,
            raw={
                "latencies_ms": [round(v, 1) for v in latencies],
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "num_requests": NUM_REQUESTS,
            },
            duration_ms=round(total_ms, 1),
        )
