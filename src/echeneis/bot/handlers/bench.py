"""Telegram /bench command handler.

Runs benchmark dimensions against the live gateway in the background
and pushes results back to the Telegram chat.
"""

import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.handlers.messages import _send_reply
from echeneis.bot.middleware import is_authorized

logger = logging.getLogger(__name__)

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


def _format_bench_results(results: list) -> str:
    """Format benchmark results for Telegram display.

    Args:
        results: List of BenchmarkResult objects.

    Returns:
        Formatted string for Telegram.
    """
    if not results:
        return "沒有結果。"

    by_dim: dict[str, list] = {}
    for r in results:
        by_dim.setdefault(r.dimension, []).append(r)

    lines: list[str] = []
    lines.append(f"📊 Benchmark 完成 ({results[0].git_sha})")
    lines.append("")

    for dim_name, dim_results in by_dim.items():
        lines.append(f"── {dim_name} ──")
        for r in sorted(dim_results, key=lambda x: x.model):
            if r.error:
                lines.append(f"  ✗ {r.model}: 錯誤")
            elif r.score is not None:
                # Dimension-specific formatting
                if dim_name == "latency":
                    p50 = r.raw.get("p50_ms", 0)
                    p95 = r.raw.get("p95_ms", 0)
                    lines.append(f"  {r.model}: p50={p50:.0f}ms p95={p95:.0f}ms")
                elif dim_name == "rate_limit":
                    rate = r.raw.get("success_rate", 0)
                    total_ms = r.raw.get("total_ms", 0)
                    lines.append(f"  成功率={rate:.0%} 耗時={total_ms:.0f}ms")
                else:
                    total = r.raw.get("total", "?")
                    dur = r.duration_ms
                    lines.append(f"  {r.model}: {r.score:.0f}/{total} ({dur:.0f}ms)")
        lines.append("")

    # Summary counts
    errors = sum(1 for r in results if r.error)
    ok = len(results) - errors
    lines.append(f"合計：{ok} 成功 / {errors} 錯誤")

    return "\n".join(lines)


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
    # Lazy import to avoid circular deps and keep bot startup fast
    from benchmarks.harness import BenchmarkHarness
    from benchmarks.results import ResultStore

    async with _bench_lock:
        try:
            started = time.perf_counter()

            harness = BenchmarkHarness(
                models=models,
                dimensions=dimensions,
            )
            results = await harness.run()

            elapsed = time.perf_counter() - started

            # Save results
            store = ResultStore()
            store.save(results)

            # Format and send
            report = _format_bench_results(results)
            report += f"\n\n⏱ 總耗時：{elapsed:.1f}s"

            await _send_reply(status_msg, report)

        except Exception as e:
            logger.error("Benchmark failed: %s", e)
            try:
                await status_msg.edit_text(f"❌ Benchmark 執行失敗：{e}")
            except Exception:
                pass
