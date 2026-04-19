"""Telegram /status command handler.

Shows a real-time dashboard of VM resources, gateway health,
and provider quota in a single HTML-formatted message.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.bot.handlers.messages import _send_reply
from echeneis.bot.middleware import require_user
from echeneis.bot.monitor import get_quota_status, get_vm_resources

logger = logging.getLogger(__name__)

# Populated at bot startup time (epoch seconds).
_boot_time: float = 0.0


def set_boot_time(t: float) -> None:
    """Record the bot startup timestamp."""
    global _boot_time
    _boot_time = t


def _fmt_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _bar(pct: float, width: int = 10) -> str:
    """Render a text progress bar."""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


@require_user
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show system dashboard.

    Admin gets full system view; guest gets a trimmed version
    (their own usage + basic system health). Admin-only content will
    be expanded in Phase 6.4 with per-user usage breakdown.
    """
    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("查詢中…")

    parts: list[str] = []

    # -- VM Resources --
    vm = get_vm_resources()
    cpu_icon = "🔴" if vm["cpu_pct"] > 80 else "🟡" if vm["cpu_pct"] > 50 else "🟢"
    mem_icon = "🔴" if vm["mem_pct"] > 90 else "🟡" if vm["mem_pct"] > 70 else "🟢"
    disk_icon = "🔴" if vm["disk_pct"] > 85 else "🟡" if vm["disk_pct"] > 70 else "🟢"

    parts.append("🖥 <b>VM 狀態</b>")
    vm_lines = [
        f"{cpu_icon} CPU  {_bar(vm['cpu_pct'])} {vm['cpu_pct']:>5.1f}%  "
        f"(load {vm['load_1m']}, {vm['cpu_count']} cores)",
        f"{mem_icon} RAM  {_bar(vm['mem_pct'])} "
        f"{vm['mem_used_gb']:>4.1f}/{vm['mem_total_gb']:.0f} GB",
        f"{disk_icon} Disk {_bar(vm['disk_pct'])} "
        f"{vm['disk_used_gb']:>4.0f}/{vm['disk_total_gb']:.0f} GB",
    ]
    parts.append("<pre>" + "\n".join(vm_lines) + "</pre>")

    # -- Gateway Health --
    try:
        health = await gateway.health()
        providers = health.get("providers", {})

        parts.append("🌐 <b>Gateway</b>")
        if providers:
            gw_lines: list[str] = []
            name_w = max(len(n) for n in providers) if providers else 8
            for name, st in sorted(providers.items()):
                circuit = st.get("circuit", "?")
                if circuit == "closed":
                    icon = "✅"
                elif circuit == "half_open":
                    icon = "🟡"
                else:
                    icon = "🔴"
                fails = st.get("consecutive_failures", 0)
                fail_str = f"  ({fails} fails)" if fails > 0 else ""
                gw_lines.append(f"{icon} {name:<{name_w}}{fail_str}")
            parts.append("<pre>" + "\n".join(gw_lines) + "</pre>")
        else:
            parts.append("<pre>全部正常 ✅</pre>")
    except GatewayError:
        parts.append("🌐 <b>Gateway</b>\n<pre>⚠️ 無法連線</pre>")

    # -- Quota Usage --
    quotas = await get_quota_status(gateway)
    if quotas:
        parts.append("📊 <b>配額</b>")
        quotas.sort(key=lambda q: -q["pct"])
        name_w = max(len(q["model"]) for q in quotas)
        q_lines: list[str] = []
        for q in quotas:
            icon = "🔴" if q["pct"] >= 90 else "🟡" if q["pct"] >= 70 else "🟢"
            q_lines.append(
                f"{icon} {q['model']:<{name_w}} "
                f"{q['used_rpd']:>4}/{q['limit_rpd']:<5} "
                f"({q['pct']:.0f}%)"
            )
        parts.append("<pre>" + "\n".join(q_lines) + "</pre>")

    # -- Uptime --
    if _boot_time:
        uptime = time.time() - _boot_time
        parts.append(f"⏱ 運行 {_fmt_uptime(uptime)}")

    await _send_reply(sent, "\n".join(parts), parse_mode="HTML")
