"""Admin operations handlers.

Commands:
  /broadcast <msg>  — push a message to every admin and guest
  /logs [N]         — last N WARNING+ log lines (bot + gateway)
  /health           — force a round of provider health checks
  /reload           — hot-reload users.json, pending_requests.json, prompt cache
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from echeneis.bot.audit import log_admin_action
from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.bot.middleware import require_admin
from echeneis.bot.request_store import get_request_store
from echeneis.bot.user_store import get_store

logger = logging.getLogger(__name__)

_LOG_DEFAULT_N = 50
_LOG_MAX_N = 200
_BROADCAST_DELAY = 0.05  # 20 msgs/sec — well under Telegram's 30/s to different chats


def _admin_username(update: Update) -> str:
    user = update.effective_user
    return (user.username if user else "") or ""


# ── /broadcast ────────────────────────────────────────────────────────


@require_admin
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /broadcast <message> — DM every admin + guest."""
    message = " ".join(context.args) if context.args else ""
    if not message.strip():
        await update.message.reply_text(
            "用法：/broadcast <訊息>\n"
            "訊息會傳送給所有 admin 與 guest（純文字，避免特殊符號）。"
        )
        return

    admin_id = update.effective_user.id if update.effective_user else 0
    store = get_store()
    recipients = set(store.list_admins())
    recipients.update(uid for uid, rec in store.list_guests() if not rec.get("banned"))
    recipients.discard(admin_id)  # don't spam the sender

    if not recipients:
        await update.message.reply_text("沒有其他收件人。")
        return

    prefix = f"📢 <b>管理員公告</b>\n\n{message}"
    sent = 0
    failed = 0
    for uid in sorted(recipients):
        try:
            await context.bot.send_message(chat_id=uid, text=prefix, parse_mode="HTML")
            sent += 1
        except (Forbidden, BadRequest) as exc:
            failed += 1
            logger.warning("Broadcast to %s failed: %s", uid, exc)
        await asyncio.sleep(_BROADCAST_DELAY)

    log_admin_action(
        admin_id,
        "broadcast",
        detail=f"sent={sent} failed={failed} msg={message[:80]!r}",
        admin_username=_admin_username(update),
    )
    await update.message.reply_text(f"✅ 已送出：{sent} 人\n❌ 失敗：{failed} 人")


# ── /logs ─────────────────────────────────────────────────────────────


def _tail_file(path: Path, n: int) -> list[str]:
    """Return the last ``n`` non-empty lines of a file (missing = empty)."""
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    tail = [line.rstrip("\n") for line in lines[-n:] if line.strip()]
    return tail


@require_admin
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /logs [N] — show last N WARNING+ log entries from bot + gateway."""
    args = context.args or []
    n = _LOG_DEFAULT_N
    if args:
        try:
            n = max(1, min(_LOG_MAX_N, int(args[0])))
        except ValueError:
            await update.message.reply_text(f"用法：/logs [N]（1-{_LOG_MAX_N}）")
            return

    state_dir = Path(os.environ.get("ECHENEIS_STATE_DIR", "/app/state"))
    bot_lines = _tail_file(state_dir / "bot.log", n)
    gw_lines = _tail_file(state_dir / "gateway.log", n)

    if not bot_lines and not gw_lines:
        await update.message.reply_text(
            "目前沒有 WARNING+ log。\n（檔案：state/bot.log, state/gateway.log）"
        )
        return

    parts: list[str] = []
    if bot_lines:
        parts.append("<b>bot.log</b>")
        parts.append("<pre>" + _escape_html("\n".join(bot_lines)) + "</pre>")
    if gw_lines:
        parts.append("<b>gateway.log</b>")
        parts.append("<pre>" + _escape_html("\n".join(gw_lines)) + "</pre>")

    text = "\n".join(parts)
    # Telegram message cap ~4096; truncate from the front if needed so the
    # newest lines (most relevant) survive.
    if len(text) > 3800:
        text = text[-3800:]
        text = "…\n" + text

    await update.message.reply_text(text, parse_mode="HTML")


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── /health ───────────────────────────────────────────────────────────


@require_admin
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health — pull live provider health from the gateway."""
    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("檢查中…")
    try:
        data = await gateway.health()
    except GatewayError as exc:
        logger.error("Gateway /health call failed: %s", exc)
        await sent.edit_text(f"❌ 無法取得健康狀態：{exc}")
        return

    providers = data.get("providers", {})
    if not providers:
        await sent.edit_text("Gateway 回應但無 provider 資訊。")
        return

    lines = ["<b>Provider 健康狀態</b>\n"]
    up = 0
    down = 0
    for name in sorted(providers):
        status = providers[name]
        circuit = status.get("circuit", "unknown")
        available = status.get("available", False)
        icon = "🟢" if available else "🔴"
        if available:
            up += 1
        else:
            down += 1
        lines.append(f"{icon} <code>{name}</code>  ({circuit})")

    lines.append(f"\n合計：{up} up / {down} down")
    await sent.edit_text("\n".join(lines), parse_mode="HTML")


# ── /reload ───────────────────────────────────────────────────────────


@require_admin
async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reload — reload bot-side state without restarting."""
    admin_id = update.effective_user.id if update.effective_user else 0

    reloaded: list[str] = []
    errors: list[str] = []

    try:
        get_store().reload()
        reloaded.append("users.json")
    except Exception as exc:  # noqa: BLE001 -- surface any failure to admin
        errors.append(f"users.json: {exc}")

    try:
        get_request_store().reload()
        reloaded.append("pending_requests.json")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pending_requests.json: {exc}")

    # Clear the unregistered-user prompt cache so newly-approved folks
    # get a fresh interaction immediately instead of being muted by the
    # 10-minute cooldown.
    try:
        from echeneis.bot.handlers import requests as req_mod

        req_mod._prompt_seen.clear()
        reloaded.append("prompt cache")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"prompt cache: {exc}")

    log_admin_action(
        admin_id,
        "reload",
        detail=f"reloaded={reloaded} errors={errors}",
        admin_username=_admin_username(update),
    )

    parts = [f"✅ 已重載：{', '.join(reloaded) or '無'}"]
    if errors:
        parts.append("⚠️ 錯誤：\n" + "\n".join(errors))
    parts.append(
        "\n<i>注意：routing_rules.yaml、litellm_config.yaml "
        "需要重啟 gateway 才生效。</i>"
    )
    await update.message.reply_text("\n".join(parts), parse_mode="HTML")
