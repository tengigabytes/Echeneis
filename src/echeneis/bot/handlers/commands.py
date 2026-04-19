"""Telegram command handlers.

Handles /start, /help, /whoami, /think, /fast, /models, /use, /model.
UI text is in Traditional Chinese (繁體中文).
"""

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.bot.handlers.messages import (
    _DEFAULT_MAX_TOKENS,
    _format_reply,
    _send_reply,
)
from echeneis.bot.middleware import public, require_user, role_of
from echeneis.bot.user_store import Role

logger = logging.getLogger(__name__)


# ── Role-aware help text ──────────────────────────────────────────────

_HELP_COMMON = (
    "/start — 開始使用\n/help — 顯示此說明\n/whoami — 查看你的 Telegram ID 與權限\n"
)

_HELP_USER = (
    "/think &lt;訊息&gt; — 深度推理模式（S 級）\n"
    "/fast &lt;訊息&gt; — 快速回覆模式（B 級）\n"
    "/models — 列出所有可用模型\n"
    "/use &lt;模型&gt; &lt;訊息&gt; — 指定模型回覆\n"
    "/model — 查看目前路由狀態\n"
    "/status — 系統狀態、配額、用量\n"
)

_HELP_ADMIN = (
    "/eviction — Anti-eviction idle service 狀態\n"
    "/bench [維度] [模型] — 執行 benchmark 測試\n"
    "\n<b>用戶管理</b>\n"
    "/adduser &lt;id&gt; [name] — 新增 guest\n"
    "/removeuser &lt;id&gt; — 移除 guest\n"
    "/listusers — 列出所有 guest\n"
    "/ban &lt;id&gt; / /unban &lt;id&gt; — 停用 / 恢復\n"
    "/whois &lt;id&gt; — 查詢用戶\n"
    "/pending — 列出待處理申請\n"
    "\n<b>運維</b>\n"
    "/broadcast &lt;訊息&gt; — 公告所有用戶\n"
    "/logs [N] — 最近 N 條 WARNING+ log\n"
    "/health — 強制健康檢查\n"
    "/reload — 熱重載設定與白名單\n"
)

_HELP_UNREGISTERED = (
    "/request [原因] — 送出使用申請\n"
    "\n你目前未註冊，請先申請並等待管理員核准。\n"
    "申請通過後即可使用完整功能。"
)

_CHAT_HINT = (
    "\n直接傳送文字 → 一般對話（A 級）\n"
    "傳送圖片 → 視覺分析（A 級）\n"
    "傳送檔案 → 自動處理\n\n"
    "💬 回覆 Bot 的訊息可延續對話（最多 6 輪）\n"
    "直接發新訊息則開啟全新對話"
)


@public
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — role-aware welcome."""
    user = update.effective_user
    role = role_of(update)
    uid = user.id if user else 0

    if role is Role.UNREGISTERED:
        await update.message.reply_text(
            f"你好！我是 Echeneis Bot。\n\n"
            f"你的 Telegram ID：<code>{uid}</code>\n"
            f"目前狀態：<b>未註冊</b>\n\n"
            f"送出申請：<code>/request [原因]</code>\n"
            f"或聯絡管理員並提供上方 ID。",
            parse_mode="HTML",
        )
        return

    role_label = "Admin" if role is Role.ADMIN else "Guest"
    await update.message.reply_text(
        f"你好！我是 Echeneis Bot。\n"
        f"權限：<b>{role_label}</b>\n\n"
        f"直接傳送訊息即可開始對話。\n"
        f"輸入 /help 查看完整指令列表。",
        parse_mode="HTML",
    )


@public
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — role-aware command list."""
    role = role_of(update)

    parts = ["<b>可用指令：</b>\n", _HELP_COMMON]
    if role is Role.UNREGISTERED:
        parts.append(_HELP_UNREGISTERED)
    else:
        parts.append(_HELP_USER)
        if role is Role.ADMIN:
            parts.append(_HELP_ADMIN)
        parts.append(_CHAT_HINT)

    await update.message.reply_text("".join(parts), parse_mode="HTML")


@public
async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /whoami — public, returns user's ID and role."""
    user = update.effective_user
    if not user:
        return
    role = role_of(update)

    role_label = {
        Role.ADMIN: "👑 Admin",
        Role.GUEST: "✅ Guest",
        Role.UNREGISTERED: "❌ 未註冊",
    }[role]

    username_line = f"@{user.username}\n" if user.username else ""
    name_line = user.first_name or "（未提供名稱）"

    await update.message.reply_text(
        f"<b>你的資訊</b>\n\n"
        f"ID：<code>{user.id}</code>\n"
        f"名稱：{name_line}\n"
        f"{username_line}"
        f"權限：{role_label}",
        parse_mode="HTML",
    )


@require_user
async def think_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /think — deep reasoning via Tier S."""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("用法：/think <你的問題>")
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("思考中…")

    try:
        started = time.perf_counter()
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            command="/think",
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        elapsed = time.perf_counter() - started
        reply = _format_reply(result, elapsed)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error in /think: %s", e)
        await sent.edit_text("抱歉，處理請求時發生錯誤。請稍後再試。")


@require_user
async def fast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fast — quick batch-tier response."""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("用法：/fast <你的問題>")
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("處理中…")

    try:
        started = time.perf_counter()
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            command="/fast",
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        elapsed = time.perf_counter() - started
        reply = _format_reply(result, elapsed)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error in /fast: %s", e)
        await sent.edit_text("抱歉，處理請求時發生錯誤。請稍後再試。")


@require_user
async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /models — list all available models."""
    gateway: GatewayClient = context.bot_data["gateway"]

    try:
        data = await gateway.models()
    except GatewayError as e:
        logger.error("Gateway error in /models: %s", e)
        await update.message.reply_text("無法取得模型清單，閘道可能離線。")
        return

    lines = ["可用模型：\n"]
    for m in data.get("models", []):
        status = "✓" if m["available"] else "✗"
        key_hint = "" if m["has_key"] else "（未設定 key）"
        usage = m.get("usage", {})
        quota_parts = []
        rem_rpm = usage.get("remaining_rpm")
        rem_rpd = usage.get("remaining_rpd")
        limit_rpm = usage.get("limit_rpm", 0)
        limit_rpd = usage.get("limit_rpd", 0)
        if limit_rpm:
            quota_parts.append(f"{rem_rpm}/{limit_rpm} rpm")
        if limit_rpd:
            quota_parts.append(f"{rem_rpd}/{limit_rpd} rpd")
        quota = f"  [{', '.join(quota_parts)}]" if quota_parts else ""
        lines.append(f"  {status} {m['name']}{key_hint}{quota}")

    lines.append("\n使用方式：/use <模型名稱> <訊息>")
    await update.message.reply_text("\n".join(lines))


@require_user
async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /use <model> <message> — send to a specific model."""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "用法：/use <模型名稱> <訊息>\n例如：/use gemma-4-31b 你好"
        )
        return

    model_name = args[0]
    text = " ".join(args[1:])

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("處理中…")

    try:
        started = time.perf_counter()
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            model=model_name,
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        elapsed = time.perf_counter() - started
        reply = _format_reply(result, elapsed)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error in /use: %s", e)
        detail = ""
        if e.status_code == 400:
            detail = "\n模型名稱可能不正確，請用 /models 查看可用模型。"
        await sent.edit_text(f"抱歉，處理請求時發生錯誤。{detail}")


@require_user
async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /model — show current routing & health status."""
    gateway: GatewayClient = context.bot_data["gateway"]

    try:
        health = await gateway.health()
        routes = await gateway.routes()
    except GatewayError as e:
        logger.error("Gateway error in /model: %s", e)
        await update.message.reply_text("無法取得路由狀態，閘道可能離線。")
        return

    lines = ["路由狀態：\n"]

    # Show tiers
    for tier_name, tier_info in routes.get("tiers", {}).items():
        desc = tier_info.get("description", "")
        models = tier_info.get("models", {})
        lines.append(f"[{tier_name}] {desc}")
        for task, model in models.items():
            lines.append(f"  {task} → {model}")
        lines.append("")

    # Show provider health
    providers = health.get("providers", {})
    if providers:
        lines.append("Provider 健康狀態：")
        for name, status in providers.items():
            circuit = status.get("circuit", "unknown")
            available = "✓" if status.get("available", False) else "✗"
            lines.append(f"  {available} {name} ({circuit})")

    await update.message.reply_text("\n".join(lines))
