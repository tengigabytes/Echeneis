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


def _mk_update(user_id: int, args: list[str] | None = None) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    update.effective_user.id = user_id
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
