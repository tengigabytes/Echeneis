"""Registration-request handlers.

Flow:
- unregistered user sends any message → ``prompt_unregistered`` shows an
  inline "送出申請" button (rate-limited to once per 10 min per user)
- button press OR ``/request [reason]`` opens a pending request, then
  pushes an inline keyboard ``[✅ Approve] [❌ Deny]`` to every admin DM
- the first admin to decide settles the request; other admins' messages
  get edited to "已由 @X 處理"
- ``/pending`` lists open requests for an admin
"""

from __future__ import annotations

import logging
import time
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from echeneis.bot.audit import log_admin_action
from echeneis.bot.middleware import public, require_admin
from echeneis.bot.request_store import get_request_store
from echeneis.bot.user_store import Role, get_store

logger = logging.getLogger(__name__)

_PROMPT_COOLDOWN_SECS = 600  # 10 minutes — avoid spamming unregistered users
_CB_SEND_REQUEST = "req:send"
_CB_APPROVE_PREFIX = "req:ok:"
_CB_DENY_PREFIX = "req:no:"

# In-memory cache: user_id -> last prompt epoch.
_prompt_seen: dict[int, float] = {}


# ── Unregistered user prompt ──────────────────────────────────────────


async def prompt_unregistered(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reply to an unregistered user with a request prompt.

    Rate-limited per user to avoid spam when they send many messages.
    Safe to call from any handler that receives a message.
    """
    user = update.effective_user
    if user is None or update.message is None:
        return

    now = time.time()
    last = _prompt_seen.get(user.id, 0.0)
    if now - last < _PROMPT_COOLDOWN_SECS:
        return
    _prompt_seen[user.id] = now

    store = get_request_store()
    existing = store.get(user.id)
    if existing and existing.get("status") == "pending":
        await update.message.reply_text("你的申請已送出，等待管理員審核中。")
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🙋 送出申請", callback_data=_CB_SEND_REQUEST)]]
    )
    await update.message.reply_text(
        f"你好！我是 Echeneis Bot。\n\n"
        f"你的 Telegram ID：<code>{user.id}</code>\n"
        f"目前狀態：<b>未註冊</b>\n\n"
        f"點下方按鈕申請使用，或輸入 <code>/request [原因]</code>。",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── /request command ─────────────────────────────────────────────────


@public
async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /request [reason] — open a registration request."""
    user = update.effective_user
    if user is None or update.message is None:
        return

    role = get_store().role(user.id)
    if role is Role.ADMIN:
        await update.message.reply_text("你已是 admin，無須申請。")
        return
    if role is Role.GUEST:
        await update.message.reply_text("你已是 guest，無須申請。")
        return

    # Anonymous account guard: username and first_name both empty.
    if not (user.username or user.first_name):
        logger.info("Rejecting anonymous request from user_id=%s", user.id)
        await update.message.reply_text("❌ 無效的帳號資料，無法送出申請。")
        return

    reason = " ".join(context.args) if context.args else ""
    await _create_and_notify(update, context, user, reason)


async def request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard button presses for request flow."""
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""
    user = query.from_user

    # ── Send request button (shown to unregistered) ──
    if data == _CB_SEND_REQUEST:
        role = get_store().role(user.id)
        if role is not Role.UNREGISTERED:
            await query.answer("你已是註冊用戶")
            return
        await query.answer("送出中…")
        await _create_and_notify(update, context, user, reason="")
        # Remove the prompt keyboard so user can't spam-click.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        return

    # ── Approve / Deny (admin only) ──
    if data.startswith(_CB_APPROVE_PREFIX) or data.startswith(_CB_DENY_PREFIX):
        await _handle_decision(update, context, query, data)
        return

    await query.answer()


async def _create_and_notify(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: Any,
    reason: str,
) -> None:
    """Create a pending request and push inline keyboard to all admins."""
    store = get_request_store()
    allowed, msg = store.can_submit(user.id)
    if not allowed:
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return

    record = store.create(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        reason=reason,
    )
    if record is None:
        return

    admins = get_store().list_admins()
    if not admins:
        logger.error(
            "No admins configured; request from %s cannot be approved", user.id
        )
        if update.message:
            await update.message.reply_text("⚠️ 系統尚未設定管理員，無法處理申請。")
        return

    # Notify each admin with an inline keyboard.
    delivered_any = False
    text = _format_admin_notification(record)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve", callback_data=f"{_CB_APPROVE_PREFIX}{user.id}"
                ),
                InlineKeyboardButton(
                    "❌ Deny", callback_data=f"{_CB_DENY_PREFIX}{user.id}"
                ),
            ]
        ]
    )
    for admin_id in admins:
        try:
            sent = await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            store.set_notified(user.id, admin_id, sent.message_id)
            delivered_any = True
        except (Forbidden, BadRequest) as exc:
            logger.warning(
                "Failed to notify admin %s of request from %s: %s",
                admin_id,
                user.id,
                exc,
            )

    if not delivered_any:
        logger.error("All admin notifications failed for request from %s", user.id)

    # Confirm to the requester.
    confirm = "✅ 申請已送出，等待管理員審核。"
    if update.message:
        await update.message.reply_text(confirm)
    elif update.callback_query:
        # Prefer replying rather than a toast so the user sees it later too.
        try:
            await update.callback_query.message.reply_text(confirm)
        except AttributeError:
            pass


def _format_admin_notification(record: dict[str, Any]) -> str:
    uid = record["user_id"]
    username = record.get("username") or "—"
    first_name = record.get("first_name") or "—"
    reason = record.get("reason") or "（未填寫）"
    requested = record.get("requested_at", "")
    return (
        "🔔 <b>New access request</b>\n\n"
        f"User: {first_name} (@{username})\n"
        f"ID: <code>{uid}</code>\n"
        f"Reason: {reason}\n"
        f"Requested: {requested}"
    )


async def _handle_decision(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: Any,
    data: str,
) -> None:
    admin = query.from_user
    if not get_store().is_admin(admin.id):
        await query.answer("只有管理員能處理此申請", show_alert=True)
        return

    approve = data.startswith(_CB_APPROVE_PREFIX)
    prefix = _CB_APPROVE_PREFIX if approve else _CB_DENY_PREFIX
    try:
        target_id = int(data[len(prefix) :])
    except ValueError:
        await query.answer()
        return

    rstore = get_request_store()
    record = rstore.get(target_id)
    if record is None:
        await query.answer("找不到此申請紀錄", show_alert=True)
        return
    if record.get("status") != "pending":
        deciding = record.get("decided_by")
        await query.answer(f"已由 {deciding} 處理過", show_alert=True)
        return

    if approve:
        decided = rstore.approve(target_id, admin.id)
        if decided is None:
            await query.answer("處理失敗（可能已有其他 admin 先處理）", show_alert=True)
            return
        # Add to guest store.
        get_store().add_guest(
            target_id,
            name=record.get("first_name", ""),
            added_by=admin.id,
            notes="auto-added via /request",
        )
        final_text = (
            f"✅ <b>Approved</b> by @{admin.username or admin.id}\n"
            + _format_admin_notification(record)
        )
        user_msg = (
            "🎉 你的申請已通過！\n現在可以使用 Bot 的全部功能。\n輸入 /help 查看指令。"
        )
    else:
        decided = rstore.deny(target_id, admin.id)
        if decided is None:
            await query.answer("處理失敗", show_alert=True)
            return
        final_text = (
            f"❌ <b>Denied</b> by @{admin.username or admin.id}\n"
            + _format_admin_notification(record)
        )
        user_msg = "你的申請未通過。\n7 天後可再次申請。"

    await query.answer("完成")

    # Edit all notification messages across admins.
    for admin_id_str, msg_id in (record.get("notified_admins") or {}).items():
        try:
            admin_id_int = int(admin_id_str)
        except ValueError:
            continue
        try:
            await context.bot.edit_message_text(
                chat_id=admin_id_int,
                message_id=msg_id,
                text=final_text,
                parse_mode="HTML",
                reply_markup=None,
            )
        except (BadRequest, Forbidden) as exc:
            logger.debug(
                "Could not edit admin %s msg %s: %s", admin_id_int, msg_id, exc
            )

    # Notify the requester.
    try:
        await context.bot.send_message(chat_id=target_id, text=user_msg)
    except (Forbidden, BadRequest) as exc:
        logger.warning("Failed to notify requester %s of decision: %s", target_id, exc)

    logger.info(
        "Admin %s %s request from %s",
        admin.id,
        "approved" if approve else "denied",
        target_id,
    )
    log_admin_action(
        admin.id,
        "approve" if approve else "deny",
        target=target_id,
        detail=f"reason={record.get('reason', '')!r}",
        admin_username=admin.username or "",
    )


# ── /pending command ─────────────────────────────────────────────────


@require_admin
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pending — list open requests."""
    pending = get_request_store().list_pending()
    if not pending:
        await update.message.reply_text("目前沒有待處理申請。")
        return

    lines = [f"<b>待處理申請</b>（{len(pending)} 件）\n"]
    for rec in pending:
        uid = rec["user_id"]
        username = rec.get("username") or "—"
        first_name = rec.get("first_name") or "—"
        reason = rec.get("reason") or "—"
        requested = rec.get("requested_at", "")
        lines.append(
            f"• <code>{uid}</code>  {first_name} (@{username})\n"
            f"  理由：{reason}\n"
            f"  時間：{requested}\n"
            f"  決定：/adduser {uid} {first_name}  或等原 notification 按鈕"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
