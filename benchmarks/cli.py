"""CLI entry point for the benchmark suite.

Usage:
    python -m benchmarks run [--dimension NAME] [--model NAME] [--gateway-url URL]
    python -m benchmarks results [--compare-last N] [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from benchmarks.harness import BenchmarkHarness
from benchmarks.results import (
    ResultStore,
    format_comparison,
    format_summary_table,
)


def _run(args: argparse.Namespace) -> None:
    """Execute benchmarks."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dimensions = args.dimension if args.dimension else None
    models = args.model if args.model else None

    harness = BenchmarkHarness(
        gateway_url=args.gateway_url,
        models=models,
        dimensions=dimensions,
    )

    results = asyncio.run(harness.run())

    store = ResultStore()
    store.save(results)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
    else:
        print(format_summary_table(results))

    print(f"\n{len(results)} results saved to {store._path}")


def _results(args: argparse.Namespace) -> None:
    """Display stored results."""
    store = ResultStore()
    runs = store.load_runs()

    if not runs:
        print("No benchmark results found.")
        return

    if args.json:
        print(
            json.dumps(
                [[r.to_dict() for r in run] for run in runs[: args.compare_last]],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # Show latest run
    print(format_summary_table(runs[0]))

    # Compare if requested and enough runs exist
    if args.compare_last >= 2 and len(runs) >= 2:
        print("\n")
        print(format_comparison(runs[0], runs[1]))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="benchmarks",
        description="Echeneis benchmark suite",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Execute benchmarks")
    run_parser.add_argument(
        "--dimension",
        action="append",
        help="Dimension(s) to run (default: all)",
    )
    run_parser.add_argument(
        "--model",
        action="append",
        help="Model(s) to benchmark (default: all available)",
    )
    run_parser.add_argument(
        "--gateway-url",
        default="http://localhost:4000",
        help="Gateway base URL",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    run_parser.set_defaults(func=_run)

    # results subcommand
    res_parser = subparsers.add_parser("results", help="Display stored results")
    res_parser.add_argument(
        "--compare-last",
        type=int,
        default=1,
        help="Compare last N runs (default: 1, show latest only)",
    )
    res_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    res_parser.set_defaults(func=_results)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
