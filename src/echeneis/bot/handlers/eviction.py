"""Telegram /eviction command handler.

Shows anti-eviction status for the long-running systemd idle service.
The service constantly holds ~21% total CPU via low-priority stress-ng,
keeping the 7-day p95 above Oracle's 20% reclaim threshold at all times.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.handlers.messages import _send_reply
from echeneis.bot.middleware import require_admin
from echeneis.bot.monitor import get_vm_resources

logger = logging.getLogger(__name__)

_TARGET_PCT = 21.0  # Idle service target (must exceed Oracle's 20% p95 threshold)


def _bar(pct: float, width: int = 10) -> str:
    """Render a text progress bar."""
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


@require_admin
async def eviction_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /eviction — show anti-eviction idle service status. Admin only."""
    sent = await update.message.reply_text("查詢中…")

    vm = get_vm_resources()
    cpu_pct = vm["cpu_pct"]

    # Healthy: above Oracle's 20% threshold.
    if cpu_pct >= 20.0:
        health_icon = "🟢"
        health_text = "健康（> 20% 閾值）"
    elif cpu_pct >= 15.0:
        health_icon = "🟡"
        health_text = "偏低（接近閾值）"
    else:
        health_icon = "🔴"
        health_text = "過低（idle service 可能未運行）"

    cpu_line = (
        f"{health_icon} CPU  {_bar(cpu_pct)} {cpu_pct:>5.1f}%  "
        f"(load {vm['load_1m']}, {vm['cpu_count']} cores)"
    )
    mem_line = (
        f"🟢 RAM  {_bar(vm['mem_pct'])} "
        f"{vm['mem_used_gb']:>4.1f}/{vm['mem_total_gb']:.0f} GB"
    )

    parts: list[str] = [
        "🛡 <b>Anti-Eviction 狀態</b>",
        f"<pre>{cpu_line}\n{mem_line}</pre>",
        "📋 <b>Idle Service</b>",
        f"<pre>"
        f"模式：常駐 stress-ng (Nice=19, SCHED_IDLE)\n"
        f"目標：~{_TARGET_PCT:.0f}% 總 CPU（1 核 × 84%）\n"
        f"狀態：{health_text}\n"
        f"策略：100% 時間 > 20% 閾值，永不觸發回收"
        f"</pre>",
        "\n<i>VM 端查狀態：</i><code>systemctl status echeneis-idle</code>",
    ]

    await _send_reply(sent, "\n".join(parts), parse_mode="HTML")


# Legacy helper retained for backward compatibility (unused after idle switch).
def _fmt_ago(ts: float) -> str:
    """Format a timestamp as 'X ago'."""
    if ts == 0:
        return "無紀錄"
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}s 前"
    if delta < 3600:
        return f"{delta // 60}m 前"
    if delta < 86400:
        return f"{delta // 3600}h {(delta % 3600) // 60}m 前"
    return f"{delta // 86400}d {(delta % 86400) // 3600}h 前"
