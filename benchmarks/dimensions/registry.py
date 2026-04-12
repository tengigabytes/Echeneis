"""Central registry of all benchmark dimensions.

Import and instantiate each dimension here so the harness
can discover them without hardcoded imports everywhere.
"""

from __future__ import annotations

from benchmarks.dimensions.code_review import CodeReviewBenchmark
from benchmarks.dimensions.context_recall import ContextRecallBenchmark
from benchmarks.dimensions.latency import LatencyBenchmark
from benchmarks.dimensions.multi_turn import MultiTurnBenchmark
from benchmarks.dimensions.rate_limit import RateLimitBenchmark
from benchmarks.dimensions.translation import TranslationBenchmark
from benchmarks.dimensions.vision import VisionBenchmark

# All dimension instances, in recommended execution order.
ALL_DIMENSIONS: list = [
    LatencyBenchmark(),
    ContextRecallBenchmark(),
    VisionBenchmark(),
    CodeReviewBenchmark(),
    TranslationBenchmark(),
    MultiTurnBenchmark(),
    RateLimitBenchmark(),
]

# Quick lookup by name.
BY_NAME: dict[str, object] = {d.name: d for d in ALL_DIMENSIONS}
