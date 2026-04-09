"""Telegram command handlers.

Handles /start, /help, /think, /fast, and /model commands.
UI text is in Traditional Chinese (繁體中文).
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.bot.middleware import is_authorized

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "你好！我是 Echeneis Bot。\n\n"
        "直接傳送訊息即可開始對話。\n"
        "輸入 /help 查看完整指令列表。"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show available commands."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "可用指令：\n\n"
        "/start — 開始使用\n"
        "/help — 顯示此說明\n"
        "/think <訊息> — 深度推理模式（S 級）\n"
        "/fast <訊息> — 快速回覆模式（B 級）\n"
        "/model — 查看目前路由狀態\n\n"
        "直接傳送文字 → 一般對話（A 級）\n"
        "傳送圖片 → 視覺分析（A 級）\n"
        "傳送檔案 → 自動處理"
    )


async def think_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /think — deep reasoning via Tier S."""
    if not is_authorized(update):
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("用法：/think <你的問題>")
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("思考中…")

    try:
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            command="/think",
        )
        reply = result["choices"][0]["message"]["content"]
        await sent.edit_text(reply)
    except GatewayError as e:
        logger.error("Gateway error in /think: %s", e)
        await sent.edit_text("抱歉，處理請求時發生錯誤。請稍後再試。")


async def fast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fast — quick batch-tier response."""
    if not is_authorized(update):
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("用法：/fast <你的問題>")
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("處理中…")

    try:
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            command="/fast",
        )
        reply = result["choices"][0]["message"]["content"]
        await sent.edit_text(reply)
    except GatewayError as e:
        logger.error("Gateway error in /fast: %s", e)
        await sent.edit_text("抱歉，處理請求時發生錯誤。請稍後再試。")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /model — show current routing & health status."""
    if not is_authorized(update):
        return

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
