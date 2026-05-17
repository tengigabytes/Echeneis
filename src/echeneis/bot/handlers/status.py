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
from echeneis.bot.middleware import require_user, role_of
from echeneis.bot.monitor import get_quota_status, get_vm_resources
from echeneis.bot.user_store import Role, get_store
from echeneis.bot.user_usage import summary as usage_summary
from echeneis.bot.user_usage import user_row

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
        # Order: most-utilised first, fully uncapped (pct=None) last.
        quotas.sort(key=lambda q: (q["pct"] is None, -(q["pct"] or 0)))
        name_w = max(len(q["model"]) for q in quotas)
        q_lines: list[str] = []
        for q in quotas:
            pct = q["pct"]
            if pct is None:
                icon = "🔵"  # no cap declared on either dimension
            elif pct >= 90:
                icon = "🔴"
            elif pct >= 70:
                icon = "🟡"
            else:
                icon = "🟢"
            rpd_str = (
                f"RPD {q['used_rpd']}/{q['limit_rpd']}"
                if q["limit_rpd"]
                else f"RPD {q['used_rpd']}/—"
            )
            rpm_str = (
                f"RPM {q['used_rpm']}/{q['limit_rpm']}"
                if q["limit_rpm"]
                else f"RPM {q['used_rpm']}/—"
            )
            pct_str = f" ({pct:.0f}%)" if pct is not None else ""
            q_lines.append(
                f"{icon} {q['model']:<{name_w}}  {rpd_str}  {rpm_str}{pct_str}"
            )
        parts.append("<pre>" + "\n".join(q_lines) + "</pre>")

    # -- Usage dashboard (admin: all users; guest: just self) --
    is_admin = role_of(update) is Role.ADMIN
    if is_admin:
        parts.append(_format_admin_usage())
    else:
        parts.append(_format_guest_usage(update.effective_user.id))

    # -- Uptime --
    if _boot_time:
        uptime = time.time() - _boot_time
        parts.append(f"⏱ 運行 {_fmt_uptime(uptime)}")

    await _send_reply(sent, "\n".join(parts), parse_mode="HTML")


def _format_admin_usage() -> str:
    """Render per-user × {1d, 7d, all} table + top-7d models."""
    data = usage_summary()
    users = data["users"]
    totals = data["totals"]
    top = data["top_models_7d"][:5]

    if not users:
        return "📊 <b>用量</b>\n<pre>尚無紀錄</pre>"

    store = get_store()
    known_admins = set(store.list_admins())
    guest_names = {uid: rec.get("name", "") for uid, rec in store.list_guests()}

    def label(uid: int) -> str:
        if uid in known_admins:
            return f"{uid} (admin)"
        name = guest_names.get(uid)
        return f"{uid} ({name})" if name else str(uid)

    # Width for label column — capped so the line stays in Telegram's
    # mono width.
    rows = sorted(users.items(), key=lambda kv: -kv[1]["all"])
    lbl_w = min(28, max(len(label(uid)) for uid, _ in rows))

    lines = [f"{'User':<{lbl_w}}  {'1d':>5} {'7d':>5} {'all':>6}"]
    lines.append("-" * (lbl_w + 21))
    for uid, counts in rows:
        lines.append(
            f"{label(uid)[:lbl_w]:<{lbl_w}}  "
            f"{counts['1d']:>5} {counts['7d']:>5} {counts['all']:>6}"
        )
    lines.append("-" * (lbl_w + 21))
    lines.append(
        f"{'Total':<{lbl_w}}  {totals['1d']:>5} {totals['7d']:>5} {totals['all']:>6}"
    )

    parts = ["📊 <b>用量（1d / 7d / all）</b>", "<pre>" + "\n".join(lines) + "</pre>"]

    if top:
        top_lines = [f"  {m:<24} {n}" for m, n in top]
        parts.append("🔥 <b>Top models (7d)</b>")
        parts.append("<pre>" + "\n".join(top_lines) + "</pre>")

    return "\n".join(parts)


def _format_guest_usage(user_id: int) -> str:
    """Single-row usage view for a guest (their own numbers only)."""
    row = user_row(user_id)
    lines = [
        f"{'1d':>5} {'7d':>5} {'all':>6}",
        f"{row['1d']:>5} {row['7d']:>5} {row['all']:>6}",
    ]
    return "📊 <b>你的用量</b>\n<pre>" + "\n".join(lines) + "</pre>"
