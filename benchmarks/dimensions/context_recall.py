"""Dimension 2: Long context recall.

Sends ~5K chars of SX1276 datasheet as system context and asks
5 factual questions. Scores by expected answer fragment matching.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchmarks.scoring import substring_match

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures() -> tuple[str, list[dict[str, Any]]]:
    """Load datasheet context and Q&A pairs."""
    context = (_FIXTURES / "sx1276_datasheet.txt").read_text(encoding="utf-8")
    with open(_FIXTURES / "context_recall_qa.yaml", encoding="utf-8") as f:
        qa = yaml.safe_load(f)
    return context, qa["questions"]


class ContextRecallBenchmark:
    """Measures factual recall over a long context window."""

    name: str = "context_recall"
    requires_vision: bool = False

    async def run(self, harness: BenchmarkHarness, model: str) -> BenchmarkResult:
        """Run context recall for a single model.

        Args:
            harness: The benchmark harness.
            model: Short model name.

        Returns:
            BenchmarkResult with per-question pass/fail in raw data.
        """
        context, questions = _load_fixtures()
        correct = 0
        details: list[dict[str, Any]] = []

        t_start = time.perf_counter()

        for qa in questions:
            question = qa["question"]
            expected = qa["expected"]

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a technical assistant. Answer questions "
                        "based on the following datasheet excerpt.\n\n" + context
                    ),
                },
                {"role": "user", "content": question},
            ]

            resp = await harness.throttled_chat(model, messages, max_tokens=200)
            answer = resp["choices"][0]["message"]["content"]

            passed = all(substring_match(answer, frag) for frag in expected)
            if passed:
                correct += 1

            details.append(
                {
                    "question": question,
                    "expected": expected,
                    "passed": passed,
                    "answer_snippet": answer[:200],
                }
            )

        total_ms = (time.perf_counter() - t_start) * 1000

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=float(correct),
            raw={
                "correct": correct,
                "total": len(questions),
                "details": details,
            },
            duration_ms=round(total_ms, 1),
        )
