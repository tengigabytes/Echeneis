"""Result persistence and display.

Stores benchmark results as append-only JSONL, and formats
summary tables and cross-run comparisons for CLI output.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.harness import BenchmarkResult

_DEFAULT_RESULTS_PATH = Path(__file__).parent / "results" / "results.jsonl"


class ResultStore:
    """JSONL-based benchmark result persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_RESULTS_PATH

    def save(self, results: list[BenchmarkResult]) -> None:
        """Append results to the JSONL file.

        Args:
            results: List of BenchmarkResult to persist.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    def load_all(self) -> list[BenchmarkResult]:
        """Load all results from the JSONL file.

        Returns:
            List of all stored BenchmarkResult.
        """
        if not self._path.exists():
            return []
        results: list[BenchmarkResult] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                results.append(BenchmarkResult(**data))
        return results

    def load_runs(self) -> list[list[BenchmarkResult]]:
        """Group results by run (same git_sha + close timestamps).

        Returns:
            List of runs, each a list of BenchmarkResult, newest first.
        """
        all_results = self.load_all()
        if not all_results:
            return []

        runs: list[list[BenchmarkResult]] = []
        current_run: list[BenchmarkResult] = []
        current_sha = ""

        for r in all_results:
            if r.git_sha != current_sha and current_run:
                runs.append(current_run)
                current_run = []
            current_sha = r.git_sha
            current_run.append(r)

        if current_run:
            runs.append(current_run)

        runs.reverse()
        return runs


def format_summary_table(results: list[BenchmarkResult]) -> str:
    """Format results as a CLI-friendly ASCII table.

    Args:
        results: Benchmark results from a single run.

    Returns:
        Formatted table string.
    """
    if not results:
        return "No results."

    # Group by dimension
    by_dim: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        by_dim.setdefault(r.dimension, []).append(r)

    lines: list[str] = []
    git_sha = results[0].git_sha
    timestamp = results[0].timestamp[:19]
    lines.append(f"Benchmark Run: {git_sha} @ {timestamp}")
    lines.append("=" * 72)

    for dim_name, dim_results in by_dim.items():
        lines.append(f"\n--- {dim_name} ---")
        lines.append(f"{'Model':<25} {'Score':>8} {'Duration':>10} {'Error':>8}")
        lines.append("-" * 55)
        for r in sorted(dim_results, key=lambda x: x.model):
            score_str = f"{r.score:.2f}" if r.score is not None else "N/A"
            dur_str = f"{r.duration_ms:.0f}ms"
            err_str = "ERR" if r.error else ""
            lines.append(f"{r.model:<25} {score_str:>8} {dur_str:>10} {err_str:>8}")

    return "\n".join(lines)


def format_comparison(
    current: list[BenchmarkResult],
    previous: list[BenchmarkResult],
) -> str:
    """Show score deltas between two runs, flagging regressions.

    Args:
        current: Results from the newer run.
        previous: Results from the older run.

    Returns:
        Formatted comparison table.
    """
    if not current or not previous:
        return "Need two runs to compare."

    # Build lookup: (dimension, model) → score
    prev_scores: dict[tuple[str, str], float | None] = {
        (r.dimension, r.model): r.score for r in previous
    }

    lines: list[str] = []
    lines.append(f"Comparison: {current[0].git_sha} vs {previous[0].git_sha}")
    lines.append("=" * 80)
    lines.append(
        f"{'Dimension':<18} {'Model':<25} {'Current':>8} {'Previous':>8} {'Delta':>8}"
    )
    lines.append("-" * 72)

    regressions = 0
    for r in sorted(current, key=lambda x: (x.dimension, x.model)):
        prev_score = prev_scores.get((r.dimension, r.model))
        if r.score is None or prev_score is None:
            continue

        delta = r.score - prev_score
        flag = ""
        if prev_score > 0 and delta / prev_score < -0.10:
            flag = " !!REGRESS"
            regressions += 1

        lines.append(
            f"{r.dimension:<18} {r.model:<25} {r.score:>8.2f} "
            f"{prev_score:>8.2f} {delta:>+8.2f}{flag}"
        )

    if regressions:
        lines.append(f"\n*** {regressions} regression(s) detected (>10% drop) ***")
    else:
        lines.append("\nNo regressions detected.")

    return "\n".join(lines)
