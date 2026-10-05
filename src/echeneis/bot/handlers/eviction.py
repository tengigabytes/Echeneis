"""Telegram /eviction command handler.

Shows host CPU/RAM alongside the long-running systemd idle service.
The service holds a token ~2% total CPU via low-priority stress-ng; it is
not sized to clear Oracle's 20% idle-reclamation threshold, which does not
apply to this tenancy.
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

_TARGET_PCT = 2.0  # Idle service target (token load, not a reclaim guard)


def _bar(pct: float, width: int = 10) -> str:
    """Render a text progress bar."""
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


@require_admin
async def eviction_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /eviction — show host load and idle service settings. Admin only."""
    sent = await update.message.reply_text("查詢中…")

    vm = get_vm_resources()
    cpu_pct = vm["cpu_pct"]

    cpu_line = (
        f"🟢 CPU  {_bar(cpu_pct)} {cpu_pct:>5.1f}%  "
        f"(load {vm['load_1m']}, {vm['cpu_count']} cores)"
    )
    mem_line = (
        f"🟢 RAM  {_bar(vm['mem_pct'])} "
        f"{vm['mem_used_gb']:>4.1f}/{vm['mem_total_gb']:.0f} GB"
    )

    parts: list[str] = [
        "🛡 <b>主機負載 / Idle Service</b>",
        f"<pre>{cpu_line}\n{mem_line}</pre>",
        "📋 <b>Idle Service</b>",
        f"<pre>"
        f"模式：常駐 stress-ng (Nice=19, SCHED_IDLE)\n"
        f"目標：~{_TARGET_PCT:.0f}% 總 CPU（1 核 × 8%）\n"
        f"說明：最低基礎負載，不作為閒置回收防護"
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
