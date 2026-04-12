"""Telegram /bench command handler.

Runs benchmark dimensions against the live gateway in the background
and pushes results back to the Telegram chat.
"""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.handlers.messages import _send_reply
from echeneis.bot.middleware import is_authorized
from echeneis.bot.task_registry import TaskRegistry

logger = logging.getLogger(__name__)

_task_registry = TaskRegistry()

# Minimum interval (seconds) between Telegram message edits for progress.
_PROGRESS_THROTTLE_SECS = 8.0

# Simple lock to prevent concurrent benchmark runs.
_bench_lock = asyncio.Lock()

_HELP_TEXT = (
    "用法：/bench [dimension] [model]\n\n"
    "維度（可選）：\n"
    "  latency, context_recall, vision,\n"
    "  code_review, translation, multi_turn,\n"
    "  rate_limit\n\n"
    "範例：\n"
    "  /bench — 全部維度 × 全部模型\n"
    "  /bench latency — 只跑延遲測試\n"
    "  /bench latency groq-llama-70b — 特定維度+模型\n"
    "  /bench all groq-llama-70b — 全部維度，指定模型"
)

_VALID_DIMENSIONS = {
    "latency",
    "context_recall",
    "vision",
    "code_review",
    "translation",
    "multi_turn",
    "rate_limit",
}


def _format_progress(info, dimension_names: list[str]) -> str:
    """Format a live progress message for Telegram.

    Args:
        info: ProgressInfo from the harness.
        dimension_names: Ordered list of all dimension names being run.

    Returns:
        Formatted progress string.
    """
    from benchmarks.harness import ProgressInfo  # type guard only

    assert isinstance(info, ProgressInfo)

    header = (
        f"🔬 Benchmark 進行中… "
        f"({info.completed_dimensions}/{info.total_dimensions} 維度)"
    )
    lines: list[str] = [header, ""]

    # Track which dimensions are done based on partial results
    done_dims: set[str] = set()
    dim_model_counts: dict[str, int] = {}
    for r in info.partial_results:
        dim_model_counts[r.dimension] = dim_model_counts.get(r.dimension, 0) + 1

    # The current dimension in progress
    current = info.current_dimension

    for name in dimension_names:
        if name == current:
            if info.models_done_in_dimension >= info.models_in_dimension:
                # Just finished last model — show as done
                lines.append(f"✅ {name} — {info.models_in_dimension} 模型")
                done_dims.add(name)
            elif info.models_done_in_dimension > 0:
                lines.append(
                    f"🔄 {name} — {info.current_model} "
                    f"({info.models_done_in_dimension}/{info.models_in_dimension})"
                )
            else:
                lines.append(f"🔄 {name} — 啟動中…")
        elif name in dim_model_counts:
            count = dim_model_counts[name]
            lines.append(f"✅ {name} — {count} 模型")
        else:
            lines.append(f"⏳ {name}")

    return "\n".join(lines)


# Short display names for models — keeps <pre> columns compact.
_SHORT_NAMES: dict[str, str] = {
    "cerebras-llama-8b": "cb-8b",
    "groq-llama-70b": "groq-70b",
    "groq-llama-4-scout": "groq-scout",
    "mistral-large-3": "mistral-lg",
    "mistral-small-3.1": "mistral-sm",
    "or-gemini-flash-lite": "or-flash",
    "or-nemotron-120b": "or-nemo",
    "gemma-4-31b": "gemma-31b",
    "gemma-4-26b": "gemma-26b",
    "github-gpt-4o": "gh-gpt4o",
    "gemini-2.5-flash": "gem-flash",
    "gemini-2.0-flash": "gem-2.0",
}

# Per-dimension emoji for section headers.
_DIM_ICONS: dict[str, str] = {
    "latency": "⚡",
    "context_recall": "📖",
    "vision": "👁",
    "code_review": "🔍",
    "translation": "🌐",
    "multi_turn": "💬",
    "rate_limit": "🔥",
}


def _short(model: str) -> str:
    """Get short display name for a model."""
    return _SHORT_NAMES.get(model, model)


def _fmt_dur(ms: float) -> str:
    """Format duration: >=1000ms as seconds, otherwise ms."""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


def _format_bench_results(results: list) -> str:
    """Format benchmark results as HTML for Telegram display.

    Uses ``<pre>`` blocks for monospace alignment and sorts each
    dimension by performance (score desc, then duration asc).

    Args:
        results: List of BenchmarkResult objects.

    Returns:
        HTML-formatted string for Telegram.
    """
    if not results:
        return "沒有結果。"

    by_dim: dict[str, list] = {}
    for r in results:
        by_dim.setdefault(r.dimension, []).append(r)

    parts: list[str] = []
    parts.append(f"📊 <b>Benchmark 完成</b> ({results[0].git_sha})")

    for dim_name, dim_results in by_dim.items():
        icon = _DIM_ICONS.get(dim_name, "📋")
        parts.append(f"\n{icon} <b>{dim_name}</b>")

        if dim_name == "latency":
            rows = _format_latency(dim_results)
        elif dim_name == "rate_limit":
            rows = _format_rate_limit(dim_results)
        else:
            rows = _format_score_dim(dim_name, dim_results)

        parts.append("<pre>" + "\n".join(rows) + "</pre>")

    # Summary
    errors = sum(1 for r in results if r.error)
    ok = len(results) - errors
    parts.append(f"合計 {ok} ✓ / {errors} ✗")

    return "\n".join(parts)


def _format_latency(dim_results: list) -> list[str]:
    """Format latency dimension — sorted by p50 ascending."""
    valid = [r for r in dim_results if not r.error and r.score is not None]
    errs = [r for r in dim_results if r.error]

    # Sort by p50 (fastest first)
    valid.sort(key=lambda r: r.raw.get("p50_ms", 9e9))

    name_w = max((len(_short(r.model)) for r in valid), default=8)
    rows: list[str] = []
    for r in valid:
        p50 = r.raw.get("p50_ms", 0)
        p95 = r.raw.get("p95_ms", 0)
        name = _short(r.model).ljust(name_w)
        rows.append(f"{name} {p50:>6.0f} / {p95:>6.0f}ms")
    for r in errs:
        rows.append(f"{_short(r.model).ljust(name_w)} {'✗ 錯誤':>16}")
    return rows


def _format_score_dim(dim_name: str, dim_results: list) -> list[str]:
    """Format a score-based dimension — sorted by score desc, duration asc."""
    valid = [r for r in dim_results if not r.error and r.score is not None]
    errs = [r for r in dim_results if r.error]

    # Sort: highest score first, then fastest
    valid.sort(key=lambda r: (-r.score, r.duration_ms))

    name_w = max((len(_short(r.model)) for r in valid), default=8)
    rows: list[str] = []
    for r in valid:
        total = r.raw.get("total", "?")
        name = _short(r.model).ljust(name_w)
        score_str = f"{r.score:.0f}/{total}"
        dur = _fmt_dur(r.duration_ms)
        rows.append(f"{name} {score_str:>5}  {dur:>6}")
    for r in errs:
        rows.append(f"{_short(r.model).ljust(name_w)} {'✗ 錯誤':>13}")
    return rows


def _format_rate_limit(dim_results: list) -> list[str]:
    """Format rate_limit dimension — single-entry summary."""
    rows: list[str] = []
    for r in dim_results:
        if r.error:
            rows.append(f"✗ 錯誤: {r.error}")
        else:
            rate = r.raw.get("success_rate", 0)
            total_ms = r.raw.get("total_ms", 0)
            rows.append(f"成功率 {rate:.0%}  耗時 {_fmt_dur(total_ms)}")
    return rows


async def bench_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /bench — run benchmarks and report results.

    Supports:
        /bench                          — all dimensions, all models
        /bench <dimension>              — single dimension, all models
        /bench <dimension> <model>      — single dimension, single model
        /bench all <model>              — all dimensions, single model
    """
    if not is_authorized(update):
        return

    args = context.args or []

    # Parse arguments
    dimension_filter: list[str] | None = None
    model_filter: list[str] | None = None

    if len(args) >= 1:
        dim_arg = args[0].lower()
        if dim_arg == "help":
            await update.message.reply_text(_HELP_TEXT)
            return
        if dim_arg != "all":
            if dim_arg not in _VALID_DIMENSIONS:
                await update.message.reply_text(f"未知維度：{dim_arg}\n\n{_HELP_TEXT}")
                return
            dimension_filter = [dim_arg]

    if len(args) >= 2:
        model_filter = [args[1]]

    # Prevent concurrent runs
    if _bench_lock.locked():
        await update.message.reply_text("⏳ 已有 benchmark 正在執行中，請稍候。")
        return

    # Describe what we're about to run
    dim_desc = ", ".join(dimension_filter) if dimension_filter else "全部"
    model_desc = ", ".join(model_filter) if model_filter else "全部可用"
    sent = await update.message.reply_text(
        f"🔬 Benchmark 啟動中…\n"
        f"維度：{dim_desc}\n"
        f"模型：{model_desc}\n\n"
        f"這可能需要幾分鐘，完成後會推送結果。"
    )

    # Run in background so the bot stays responsive
    asyncio.create_task(_run_bench_background(sent, dimension_filter, model_filter))


async def _run_bench_background(
    status_msg,
    dimensions: list[str] | None,
    models: list[str] | None,
) -> None:
    """Execute benchmarks in background and push results to Telegram.

    Args:
        status_msg: The Telegram message to update with results.
        dimensions: Dimension filter (None = all).
        models: Model filter (None = all available).
    """
    try:
        # Lazy import to avoid circular deps and keep bot startup fast.
        # Must be inside try so ImportError is caught and reported.
        from benchmarks.dimensions import registry
        from benchmarks.harness import BenchmarkHarness
        from benchmarks.results import ResultStore

        dim_desc = ", ".join(dimensions) if dimensions else "all"
        model_desc = ", ".join(models) if models else "all"
        detail = f"dimensions={dim_desc} models={model_desc}"

        # Determine ordered dimension names for progress display
        if dimensions:
            dim_names = [
                d.name for d in registry.ALL_DIMENSIONS if d.name in dimensions
            ]
        else:
            dim_names = [d.name for d in registry.ALL_DIMENSIONS]

        # Throttled progress callback — edits Telegram message at most
        # once per _PROGRESS_THROTTLE_SECS to avoid rate limits.
        last_edit = 0.0

        async def _on_progress(info) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if now - last_edit < _PROGRESS_THROTTLE_SECS:
                return
            last_edit = now
            text = _format_progress(info, dim_names)
            try:
                await status_msg.edit_text(text)
            except Exception:
                logger.debug("Progress edit failed", exc_info=True)

        async with _bench_lock:
            async with _task_registry.track("benchmark", max_minutes=30, detail=detail):
                started = time.perf_counter()

                harness = BenchmarkHarness(
                    models=models,
                    dimensions=dimensions,
                    on_progress=_on_progress,
                )
                results = await harness.run()

                elapsed = time.perf_counter() - started

                # Save results
                store = ResultStore()
                store.save(results)

                # Format and send final report (HTML for monospace alignment)
                report = _format_bench_results(results)
                report += f"\n\n⏱ 總耗時：{elapsed:.1f}s"

                await _send_reply(status_msg, report, parse_mode="HTML")

    except Exception as e:
        logger.error("Benchmark failed: %s", e, exc_info=True)
        try:
            await status_msg.edit_text(f"❌ Benchmark 執行失敗：{e}")
        except Exception:
            pass
