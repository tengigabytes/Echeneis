"""Dimension 3: Vision — register map image extraction.

Sends a register map image to vision-capable models and checks
whether they can extract specific register default values.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from benchmarks.scoring import regex_match

if TYPE_CHECKING:
    from benchmarks.harness import BenchmarkHarness, BenchmarkResult

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures() -> tuple[str, str, list[dict[str, Any]]]:
    """Load image as base64, prompt, and expected values."""
    img_path = _FIXTURES / "register_map.png"
    img_b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")

    with open(_FIXTURES / "vision_expected.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return img_b64, data["prompt"], data["expected_values"]


class VisionBenchmark:
    """Measures ability to extract register values from a datasheet image."""

    name: str = "vision"
    requires_vision: bool = True

    async def run(self, harness: BenchmarkHarness, model: str) -> BenchmarkResult:
        """Run vision benchmark for a single model.

        Args:
            harness: The benchmark harness.
            model: Short model name (must support vision).

        Returns:
            BenchmarkResult with per-value match results in raw data.
        """
        img_b64, prompt, expected_values = _load_fixtures()

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            },
        ]

        t_start = time.perf_counter()
        resp = await harness.throttled_chat(model, messages, max_tokens=500)
        total_ms = (time.perf_counter() - t_start) * 1000

        answer = resp["choices"][0]["message"]["content"]

        found = 0
        details: list[dict[str, Any]] = []
        for val in expected_values:
            matched = any(regex_match(answer, p) for p in val["patterns"])
            if matched:
                found += 1
            details.append(
                {
                    "name": val["name"],
                    "matched": matched,
                }
            )

        return harness.make_result(
            dimension=self.name,
            model=model,
            score=float(found),
            raw={
                "found": found,
                "total": len(expected_values),
                "details": details,
                "answer_snippet": answer[:500],
            },
            duration_ms=round(total_ms, 1),
        )
