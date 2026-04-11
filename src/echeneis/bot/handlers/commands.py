"""Telegram command handlers.

Handles /start, /help, /think, /fast, and /model commands.
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
        "/models — 列出所有可用模型\n"
        "/use <模型> <訊息> — 指定模型回覆\n"
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


async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /models — list all available models."""
    if not is_authorized(update):
        return

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


async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /use <model> <message> — send to a specific model."""
    if not is_authorized(update):
        return

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
