"""Tests for admin user-management command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from echeneis.bot.handlers.admin_users import (
    adduser_command,
    ban_command,
    listusers_command,
    removeuser_command,
    unban_command,
    whois_command,
)
from echeneis.bot.user_store import Role, get_store, reset_store

ADMIN_ID = 100
GUEST_ID = 200
STRANGER_ID = 999


@pytest.fixture(autouse=True)
def _fresh_store(tmp_path, monkeypatch):
    """Give each test a clean user store rooted at tmp_path."""
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", str(ADMIN_ID))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    reset_store()
    yield
    reset_store()


def _mk_update(
    user_id: int, args: list[str] | None = None
) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "tester"  # keep audit log JSON-serialisable
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot_data = {}
    return update, ctx


@pytest.mark.asyncio
async def test_guest_cannot_use_adduser():
    """Guest users are denied (silent drop, no reply)."""
    get_store().add_guest(GUEST_ID)
    update, ctx = _mk_update(GUEST_ID, ["400"])
    await adduser_command(update, ctx)
    update.message.reply_text.assert_not_called()
    assert get_store().role(400) is Role.UNREGISTERED


@pytest.mark.asyncio
async def test_unregistered_cannot_use_adduser():
    """Unregistered users are denied silently."""
    update, ctx = _mk_update(STRANGER_ID, ["400"])
    await adduser_command(update, ctx)
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_admin_adds_guest():
    update, ctx = _mk_update(ADMIN_ID, ["400", "Alice"])
    await adduser_command(update, ctx)
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "400" in text
    assert get_store().role(400) is Role.GUEST


@pytest.mark.asyncio
async def test_adduser_rejects_invalid_id():
    update, ctx = _mk_update(ADMIN_ID, ["not-a-number"])
    await adduser_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "無效" in text


@pytest.mark.asyncio
async def test_adduser_cannot_shadow_admin():
    update, ctx = _mk_update(ADMIN_ID, [str(ADMIN_ID)])
    await adduser_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "已是 admin" in text


@pytest.mark.asyncio
async def test_removeuser_roundtrip():
    get_store().add_guest(400, name="Alice")
    update, ctx = _mk_update(ADMIN_ID, ["400"])
    await removeuser_command(update, ctx)
    update.message.reply_text.assert_called_once()
    assert get_store().role(400) is Role.UNREGISTERED


@pytest.mark.asyncio
async def test_removeuser_missing():
    update, ctx = _mk_update(ADMIN_ID, ["999"])
    await removeuser_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "不在" in text


@pytest.mark.asyncio
async def test_ban_and_unban_cycle():
    get_store().add_guest(400)

    update, ctx = _mk_update(ADMIN_ID, ["400"])
    await ban_command(update, ctx)
    assert get_store().role(400) is Role.UNREGISTERED  # banned -> unregistered
    assert not get_store().is_authorized(400)

    update2, ctx2 = _mk_update(ADMIN_ID, ["400"])
    await unban_command(update2, ctx2)
    assert get_store().role(400) is Role.GUEST


@pytest.mark.asyncio
async def test_listusers_shows_admins_and_guests():
    get_store().add_guest(400, name="Alice")
    get_store().add_guest(500, name="Bob")
    update, ctx = _mk_update(ADMIN_ID)
    await listusers_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert str(ADMIN_ID) in text
    assert "400" in text
    assert "500" in text
    assert "Alice" in text


@pytest.mark.asyncio
async def test_whois_guest_shows_detail():
    get_store().add_guest(400, name="Alice", added_by=ADMIN_ID, notes="beta")
    update, ctx = _mk_update(ADMIN_ID, ["400"])
    await whois_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "400" in text
    assert "Alice" in text
    assert "beta" in text


@pytest.mark.asyncio
async def test_whois_unknown_shows_unregistered():
    update, ctx = _mk_update(ADMIN_ID, ["777"])
    await whois_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "777" in text
    assert "未註冊" in text


@pytest.mark.asyncio
async def test_whois_includes_usage_counts():
    """/whois must surface 1d/7d/all usage so admins can size each user."""
    import time

    from echeneis.bot.user_usage import record_usage

    get_store().add_guest(400, name="Alice", added_by=ADMIN_ID)
    # Three entries spanning the windows: today, 3 days ago, 30 days ago.
    now = time.time()
    record_usage(400, "gemma-4-31b", ts=now)
    record_usage(400, "gemma-4-31b", ts=now - 3 * 86400)
    record_usage(400, "gemma-4-31b", ts=now - 30 * 86400)

    update, ctx = _mk_update(ADMIN_ID, ["400"])
    await whois_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]

    # 1d=1 (today), 7d=2 (today + 3d ago), all=3.
    assert "用量" in text
    assert "1d <b>1</b>" in text
    assert "7d <b>2</b>" in text
    assert "all <b>3</b>" in text
