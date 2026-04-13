"""Telegram /eviction command handler.

Shows anti-eviction status: current CPU load, 7-day duty accumulation,
and next scheduled run. Can also trigger a manual stress run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.handlers.messages import _send_reply
from echeneis.bot.middleware import is_authorized
from echeneis.bot.monitor import get_vm_resources

logger = logging.getLogger(__name__)

# Must match anti-eviction.sh configuration.
_WEEKLY_TARGET = 604  # 504 + 100 safety margin
_STATE_DIR = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
_STATE_FILE = os.path.join(_STATE_DIR, "anti-eviction.log")
_ANTI_EVICTION_SCRIPT = os.path.join(
    os.environ.get("ECHENEIS_DEPLOY_DIR", "/app/deploy"),
    "anti-eviction.sh",
)

# Runtime state for the manual stress run.
_stress_running = False
_stress_started: float = 0.0


def _read_duty_log() -> list[dict[str, Any]]:
    """Parse anti-eviction.log and return entries from the last 7 days."""
    cutoff = time.time() - 7 * 86400
    entries: list[dict[str, Any]] = []
    try:
        with open(_STATE_FILE) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                ts = int(parts[0])
                duration = int(parts[1])
                if ts >= cutoff:
                    entries.append({"ts": ts, "duration": duration})
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Failed to read duty log", exc_info=True)
    return entries


def _duty_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute duty statistics from log entries."""
    total_minutes = sum(e["duration"] for e in entries) / 60
    run_count = len(entries)
    last_run = max((e["ts"] for e in entries), default=0)
    return {
        "total_minutes": round(total_minutes, 1),
        "run_count": run_count,
        "last_run_ts": last_run,
        "target_minutes": _WEEKLY_TARGET,
        "pct": (
            round(total_minutes / _WEEKLY_TARGET * 100, 1) if _WEEKLY_TARGET else 0
        ),
    }


def _bar(pct: float, width: int = 10) -> str:
    """Render a text progress bar."""
    filled = round(min(pct, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


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


def _fmt_elapsed(start: float) -> str:
    """Format elapsed time since start."""
    elapsed = int(time.time() - start)
    m, s = divmod(elapsed, 60)
    return f"{m}m{s:02d}s"


async def eviction_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /eviction — show anti-eviction status or trigger manual run."""
    if not is_authorized(update):
        return

    args = context.args or []
    if args and args[0] == "run":
        if _stress_running:
            elapsed = _fmt_elapsed(_stress_started)
            await update.message.reply_text(
                f"⚠️ Stress test 已在執行中（已跑 {elapsed}）"
            )
            return
        sent = await update.message.reply_text("⚡ 手動觸發 anti-eviction…")
        asyncio.create_task(_run_stress_background(sent))
        return

    # -- Dashboard mode --
    sent = await update.message.reply_text("查詢中…")

    vm = get_vm_resources()
    entries = _read_duty_log()
    summary = _duty_summary(entries)

    # CPU status
    cpu_icon = "🔴" if vm["cpu_pct"] > 80 else "🟡" if vm["cpu_pct"] > 50 else "🟢"

    # Duty status
    if summary["pct"] >= 100:
        duty_icon = "🟢"
    elif summary["pct"] >= 70:
        duty_icon = "🟡"
    else:
        duty_icon = "🔴"

    # Deficit
    deficit = summary["target_minutes"] - summary["total_minutes"]
    if deficit <= 0:
        deficit_str = f"+{abs(deficit):.0f} 超標"
    else:
        deficit_str = f"-{deficit:.0f} 不足"

    parts: list[str] = []
    parts.append("🛡 <b>Anti-Eviction 狀態</b>")

    # Running indicator
    if _stress_running:
        elapsed = _fmt_elapsed(_stress_started)
        parts.append(f"🔥 <b>Stress test 執行中</b>（已跑 {elapsed}）")

    cpu_line = (
        f"{cpu_icon} CPU  {_bar(vm['cpu_pct'])} {vm['cpu_pct']:>5.1f}%  "
        f"(load {vm['load_1m']}, {vm['cpu_count']} cores)"
    )
    mem_line = (
        f"{'🟢'} RAM  {_bar(vm['mem_pct'])} "
        f"{vm['mem_used_gb']:>4.1f}/{vm['mem_total_gb']:.0f} GB"
    )
    parts.append(f"<pre>{cpu_line}\n{mem_line}</pre>")

    parts.append("📊 <b>7 日 Duty 累計</b>")
    duty_lines = [
        f"{duty_icon} {_bar(summary['pct'])} "
        f"{summary['total_minutes']:.0f}/{summary['target_minutes']} min "
        f"({summary['pct']:.0f}%)",
        f"   {deficit_str}",
        f"   執行次數：{summary['run_count']}",
        f"   上次執行：{_fmt_ago(summary['last_run_ts'])}",
    ]
    parts.append("<pre>" + "\n".join(duty_lines) + "</pre>")

    # Recent runs (last 5)
    if entries:
        parts.append("🕐 <b>近期執行</b>")
        recent = sorted(entries, key=lambda e: -e["ts"])[:5]
        recent_lines = []
        for e in recent:
            t = time.strftime("%m/%d %H:%M", time.localtime(e["ts"]))
            recent_lines.append(f"   {t}  {e['duration']:>4}s")
        parts.append("<pre>" + "\n".join(recent_lines) + "</pre>")

    parts.append("\n手動觸發：<code>/eviction run</code>")

    await _send_reply(sent, "\n".join(parts), parse_mode="HTML")


def _progress_bar(pct: float, width: int = 20) -> str:
    """Render a progress bar for the stress run."""
    clamped = min(max(pct, 0), 100)
    filled = round(clamped / 100 * width)
    return "▓" * filled + "░" * (width - filled)


async def _read_plan(proc) -> dict[str, int]:
    """Read the PLAN line from anti-eviction.sh stdout."""
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        text = line.decode().strip()
        if text.startswith("PLAN "):
            parts = text.split()
            return {
                "stress_load": int(parts[1]),
                "duration": int(parts[2]),
                "weekly_duty": int(parts[3]),
                "weekly_target": int(parts[4]),
            }
    except Exception:
        pass
    return {}


async def _run_stress_background(sent) -> None:
    """Run anti-eviction.sh in background, updating every 5s with progress."""
    global _stress_running, _stress_started
    _stress_running = True
    _stress_started = time.time()

    vm_before = get_vm_resources()

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            _ANTI_EVICTION_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Read planned duration from script stdout
        plan = await _read_plan(proc)
        total_duration = plan.get("duration", 300)
        stress_load = plan.get("stress_load", 0)

        last_text = ""
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=2)
                break  # Process finished
            except asyncio.TimeoutError:
                pass  # Still running

            elapsed_s = time.time() - _stress_started
            pct = min(elapsed_s / total_duration * 100, 99)
            vm_now = get_vm_resources()
            elapsed = _fmt_elapsed(_stress_started)
            remaining = max(0, int(total_duration - elapsed_s))
            rm, rs = divmod(remaining, 60)

            text = (
                f"🔥 <b>Stress Test 執行中</b>\n\n"
                f"<code>{_progress_bar(pct)}</code> {pct:.0f}%\n"
                f"⏱ {elapsed} / "
                f"{total_duration // 60}m"
                f"{total_duration % 60:02d}s"
                f"（剩餘 {rm}m{rs:02d}s）\n\n"
                f"<pre>"
                f"目標負載:  {stress_load}%\n"
                f"目前 CPU:  {vm_now['cpu_pct']:>5.1f}%"
                f"  (load {vm_now['load_1m']})\n"
                f"觸發前:    {vm_before['cpu_pct']:>5.1f}%"
                f"</pre>"
            )
            if text != last_text:
                try:
                    await sent.edit_text(text, parse_mode="HTML")
                    last_text = text
                except Exception:
                    pass

        rc = proc.returncode
        vm_after = get_vm_resources()
        entries = _read_duty_log()
        summary = _duty_summary(entries)

        if rc == 0:
            total_elapsed = _fmt_elapsed(_stress_started)
            result = (
                f"✅ <b>Anti-eviction 完成</b>（{total_elapsed}）\n\n"
                f"<code>{_progress_bar(100)}</code> 100%\n\n"
                f"<pre>"
                f"觸發前 CPU: {vm_before['cpu_pct']:>5.1f}%\n"
                f"結束後 CPU: {vm_after['cpu_pct']:>5.1f}%\n"
                f"\n"
                f"7日 Duty:  {summary['total_minutes']:.0f}/"
                f"{summary['target_minutes']} min "
                f"({summary['pct']:.0f}%)"
                f"</pre>"
            )
        else:
            result = f"❌ Anti-eviction 執行失敗 (exit {rc})"

        await _send_reply(sent, result, parse_mode="HTML")

    except Exception as e:
        logger.error("Manual anti-eviction failed: %s", e)
        try:
            await sent.edit_text(f"❌ 執行錯誤: {e}")
        except Exception:
            pass
    finally:
        _stress_running = False
