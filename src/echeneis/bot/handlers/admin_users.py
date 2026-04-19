"""Admin commands for managing the guest whitelist.

Commands:
  /adduser <id> [name]   — register a new guest
  /removeuser <id>       — remove a guest entry
  /listusers             — list all guests (with status)
  /ban <id>              — soft-disable a guest
  /unban <id>            — re-enable a banned guest
  /whois <id>            — detailed lookup for one user ID
"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.middleware import require_admin
from echeneis.bot.user_store import Role, get_store

logger = logging.getLogger(__name__)


def _parse_user_id(raw: str) -> int | None:
    """Parse a user ID that may be prefixed with @ or be a bare integer."""
    raw = raw.strip()
    if raw.startswith("@"):
        return None  # usernames not resolvable without extra API call
    try:
        return int(raw)
    except ValueError:
        return None


def _fmt_datetime(iso: str) -> str:
    """Turn ISO timestamp into local-friendly format."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


@require_admin
async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adduser <id> [name] — add a guest."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "用法：/adduser <user_id> [name]\n"
            "例如：/adduser 123456789 Alice"
        )
        return

    uid = _parse_user_id(args[0])
    if uid is None:
        await update.message.reply_text("❌ 無效的 user ID（必須為整數）")
        return

    name = " ".join(args[1:]) if len(args) > 1 else ""
    admin_id = update.effective_user.id if update.effective_user else 0

    store = get_store()
    role = store.role(uid)
    if role is Role.ADMIN:
        await update.message.reply_text(f"❌ {uid} 已是 admin，無須新增")
        return
    if role is Role.GUEST:
        await update.message.reply_text(f"ℹ️ {uid} 已是 guest")
        return

    added = store.add_guest(uid, name=name, added_by=admin_id)
    if not added:
        await update.message.reply_text(f"❌ 新增失敗：{uid}")
        return

    label = f" ({name})" if name else ""
    await update.message.reply_text(
        f"✅ 已新增 guest：<code>{uid}</code>{label}",
        parse_mode="HTML",
    )
    logger.info("Admin %s added guest %s (name=%r)", admin_id, uid, name)


@require_admin
async def removeuser_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /removeuser <id> — remove a guest."""
    args = context.args or []
    if not args:
        await update.message.reply_text("用法：/removeuser <user_id>")
        return

    uid = _parse_user_id(args[0])
    if uid is None:
        await update.message.reply_text("❌ 無效的 user ID")
        return

    store = get_store()
    if store.role(uid) is Role.ADMIN:
        await update.message.reply_text(
            "❌ 無法移除 admin（需改 TELEGRAM_ADMIN_USERS 並重啟）"
        )
        return

    removed = store.remove_guest(uid)
    if not removed:
        await update.message.reply_text(f"❌ {uid} 不在 guest 名單中")
        return

    admin_id = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(f"✅ 已移除 guest：<code>{uid}</code>", parse_mode="HTML")
    logger.info("Admin %s removed guest %s", admin_id, uid)


@require_admin
async def listusers_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /listusers — show all admins and guests."""
    store = get_store()
    admins = store.list_admins()
    guests = store.list_guests()

    lines = ["<b>👑 Admin</b>"]
    if admins:
        for uid in admins:
            lines.append(f"  <code>{uid}</code>")
    else:
        lines.append("  （無）")

    lines.append(f"\n<b>✅ Guest</b>（{len(guests)} 人）")
    if guests:
        for uid, record in guests:
            banned = "🚫 " if record.get("banned") else ""
            name = record.get("name") or "—"
            added = _fmt_datetime(record.get("added_at", ""))
            lines.append(
                f"  {banned}<code>{uid}</code>  {name}  · 新增於 {added}"
            )
    else:
        lines.append("  （無）")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@require_admin
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ban <id> — soft-disable a guest."""
    args = context.args or []
    if not args:
        await update.message.reply_text("用法：/ban <user_id>")
        return

    uid = _parse_user_id(args[0])
    if uid is None:
        await update.message.reply_text("❌ 無效的 user ID")
        return

    store = get_store()
    if store.role(uid) is Role.ADMIN:
        await update.message.reply_text("❌ 無法 ban admin")
        return

    if not store.set_banned(uid, True):
        await update.message.reply_text(f"❌ {uid} 不在 guest 名單中")
        return

    admin_id = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(f"🚫 已停用 guest：<code>{uid}</code>", parse_mode="HTML")
    logger.info("Admin %s banned guest %s", admin_id, uid)


@require_admin
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unban <id> — reinstate a banned guest."""
    args = context.args or []
    if not args:
        await update.message.reply_text("用法：/unban <user_id>")
        return

    uid = _parse_user_id(args[0])
    if uid is None:
        await update.message.reply_text("❌ 無效的 user ID")
        return

    store = get_store()
    if not store.set_banned(uid, False):
        await update.message.reply_text(f"❌ {uid} 不在 guest 名單中")
        return

    admin_id = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(f"✅ 已恢復 guest：<code>{uid}</code>", parse_mode="HTML")
    logger.info("Admin %s unbanned guest %s", admin_id, uid)


@require_admin
async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /whois <id> — detailed lookup."""
    args = context.args or []
    if not args:
        await update.message.reply_text("用法：/whois <user_id>")
        return

    uid = _parse_user_id(args[0])
    if uid is None:
        await update.message.reply_text("❌ 無效的 user ID")
        return

    store = get_store()
    role = store.role(uid)
    role_label = {
        Role.ADMIN: "👑 Admin",
        Role.GUEST: "✅ Guest",
        Role.UNREGISTERED: "❌ 未註冊",
    }[role]

    lines = [f"<b>User {uid}</b>", f"權限：{role_label}"]

    if role is Role.GUEST:
        record = store.get_guest(uid) or {}
        lines.append(f"名稱：{record.get('name') or '—'}")
        lines.append(f"新增者：<code>{record.get('added_by', '—')}</code>")
        lines.append(f"新增時間：{_fmt_datetime(record.get('added_at', ''))}")
        if record.get("banned"):
            lines.append("狀態：🚫 <b>已停用</b>")
        if record.get("notes"):
            lines.append(f"備註：{record['notes']}")
    elif role is Role.UNREGISTERED:
        record = store.get_guest(uid)
        if record and record.get("banned"):
            lines.append("（guest 紀錄存在但已停用）")

    # Phase 6.4 will extend this with 7-day usage counts.
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
