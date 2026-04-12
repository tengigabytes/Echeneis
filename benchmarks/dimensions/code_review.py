"""Dimension 4: Embedded C code review.

Sends an SX1276 SPI driver with 6 planted bugs and asks the model
to review it. Scores by counting how many bugs are detected via
keyword matching.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchmarks.scoring import count_matches

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

_FIXTURES = Path(__file__).parent.parent / "fixtures"

_REVIEW_PROMPT = """\
Review the following SX1276 SPI driver for bugs, correctness issues,
and potential problems. List each issue you find with a brief explanation.

```c
{code}
```
"""


def _load_fixtures() -> tuple[str, list[dict[str, Any]]]:
    """Load C source code and bug definitions."""
    code = (_FIXTURES / "sx1276_spi_driver.c").read_text(encoding="utf-8")
    with open(_FIXTURES / "code_review_bugs.yaml", encoding="utf-8") as f:
        bugs = yaml.safe_load(f)
    return code, bugs["bugs"]


class CodeReviewBenchmark:
    """Measures ability to find planted bugs in embedded C code."""

    name: str = "code_review"
    requires_vision: bool = False

    async def run(
        self, harness: BenchmarkHarness, model: str
    ) -> BenchmarkResult:
        """Run code review benchmark for a single model.

        Args:
            harness: The benchmark harness.
            model: Short model name.

        Returns:
            BenchmarkResult with found/missed bugs in raw data.
        """
        code, bugs = _load_fixtures()

        messages = [
            {"role": "user", "content": _REVIEW_PROMPT.format(code=code)},
        ]

        t_start = time.perf_counter()
        resp = await harness.throttled_chat(model, messages, max_tokens=2000)
        total_ms = (time.perf_counter() - t_start) * 1000

        review_text = resp["choices"][0]["message"]["content"]
        found_count, found, missed = count_matches(review_text, bugs)

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=float(found_count),
            raw={
                "found": found_count,
                "total": len(bugs),
                "found_bugs": found,
                "missed_bugs": missed,
                "review_snippet": review_text[:500],
            },
            duration_ms=round(total_ms, 1),
        )
