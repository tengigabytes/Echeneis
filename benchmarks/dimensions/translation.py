"""Dimension 5: Translation consistency.

Translates an English technical paragraph to Traditional Chinese and
checks that 6 specified technical terms are preserved in English.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from benchmarks.scoring import count_preserved_terms

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures() -> tuple[str, str, list[str]]:
    """Load source text, instruction, and terms to preserve."""
    with open(_FIXTURES / "translation_source.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["source_text"], data["instruction"], data["preserve_terms"]


class TranslationBenchmark:
    """Measures translation quality and term preservation."""

    name: str = "translation"
    requires_vision: bool = False

    async def run(self, harness: BenchmarkHarness, model: str) -> BenchmarkResult:
        """Run translation benchmark for a single model.

        Args:
            harness: The benchmark harness.
            model: Short model name.

        Returns:
            BenchmarkResult with preserved/missing terms in raw data.
        """
        source, instruction, terms = _load_fixtures()

        messages = [
            {
                "role": "user",
                "content": f"{instruction}\n\n{source}",
            },
        ]

        t_start = time.perf_counter()
        resp = await harness.throttled_chat(model, messages, max_tokens=500)
        total_ms = (time.perf_counter() - t_start) * 1000

        translated = resp["choices"][0]["message"]["content"]
        preserved_count, preserved, missing = count_preserved_terms(translated, terms)

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=float(preserved_count),
            raw={
                "preserved": preserved_count,
                "total": len(terms),
                "preserved_terms": preserved,
                "missing_terms": missing,
                "translation_snippet": translated[:500],
            },
            duration_ms=round(total_ms, 1),
        )
