"""Dimension 7: Multi-turn conversation context retention.

Sends a 4-turn conversation and checks whether the model retains
context from earlier turns in its final summary response.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchmarks.scoring import regex_match

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures() -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Load conversation turns and expected markers."""
    with open(_FIXTURES / "multi_turn_script.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["turns"], data["expected_markers"]


class MultiTurnBenchmark:
    """Measures context retention across multiple conversation turns."""

    name: str = "multi_turn"
    requires_vision: bool = False

    async def run(
        self, harness: BenchmarkHarness, model: str
    ) -> BenchmarkResult:
        """Run multi-turn benchmark for a single model.

        Sends turns 1-3 as conversation history, then turn 4 as the
        final user message. Checks the response for expected markers.

        Args:
            harness: The benchmark harness.
            model: Short model name.

        Returns:
            BenchmarkResult with per-marker match results in raw data.
        """
        turns, expected_markers = _load_fixtures()

        # Build conversation: turns 1-3 as history with assistant acks,
        # turn 4 as the final question.
        messages: list[dict[str, str]] = []
        for i, turn in enumerate(turns):
            messages.append({"role": turn["role"], "content": turn["content"]})
            # Add assistant acknowledgment for non-final turns
            if i < len(turns) - 1:
                ack_resp = await harness.throttled_chat(
                    model, list(messages), max_tokens=150
                )
                ack_text = ack_resp["choices"][0]["message"]["content"]
                messages.append({"role": "assistant", "content": ack_text})

        # Final turn — get the summary response
        t_start = time.perf_counter()
        resp = await harness.throttled_chat(model, messages, max_tokens=500)
        total_ms = (time.perf_counter() - t_start) * 1000

        summary = resp["choices"][0]["message"]["content"]

        found = 0
        details: list[dict[str, Any]] = []
        for marker in expected_markers:
            matched = any(regex_match(summary, p) for p in marker["patterns"])
            if matched:
                found += 1
            details.append(
                {
                    "name": marker["name"],
                    "matched": matched,
                }
            )

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=float(found),
            raw={
                "found": found,
                "total": len(expected_markers),
                "details": details,
                "summary_snippet": summary[:500],
            },
            duration_ms=round(total_ms, 1),
        )
