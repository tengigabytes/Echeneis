"""Integration-ish tests for the /request flow handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from echeneis.bot.handlers.requests import (
    pending_command,
    prompt_unregistered,
    request_callback,
    request_command,
)
from echeneis.bot.request_store import (
    get_request_store,
    reset_request_store,
)
from echeneis.bot.user_store import Role, get_store, reset_store

ADMIN_ID = 100
STRANGER_ID = 999


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", str(ADMIN_ID))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    reset_store()
    reset_request_store()
    # Clear prompt cooldown cache between tests.
    from echeneis.bot.handlers import requests as req_mod

    req_mod._prompt_seen.clear()
    yield
    reset_store()
    reset_request_store()


def _mk_update(user_id: int, username: str = "alice", first_name: str = "Alice"):
    """Build a mock Update with message + user."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = first_name
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return update, ctx


# ── prompt_unregistered ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_unregistered_sends_keyboard():
    update, ctx = _mk_update(STRANGER_ID)
    await prompt_unregistered(update, ctx)
    update.message.reply_text.assert_called_once()
    kwargs = update.message.reply_text.call_args.kwargs
    assert kwargs["parse_mode"] == "HTML"
    assert "送出申請" in kwargs["reply_markup"].inline_keyboard[0][0].text


@pytest.mark.asyncio
async def test_prompt_rate_limited_second_call():
    """Second call within 10 min must not send another message."""
    update, ctx = _mk_update(STRANGER_ID)
    await prompt_unregistered(update, ctx)
    update.message.reply_text.assert_called_once()

    update.message.reply_text.reset_mock()
    await prompt_unregistered(update, ctx)
    update.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_shows_pending_hint_if_already_requested():
    update, ctx = _mk_update(STRANGER_ID)
    get_request_store().create(STRANGER_ID, "alice", "Alice", "")
    await prompt_unregistered(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "已送出" in text or "審核中" in text


# ── /request ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_creates_pending_and_notifies_admin():
    update, ctx = _mk_update(STRANGER_ID)
    ctx.args = ["測試用途"]
    await request_command(update, ctx)

    # Notification sent to admin.
    ctx.bot.send_message.assert_called_once()
    call_kwargs = ctx.bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == ADMIN_ID
    assert "New access request" in call_kwargs["text"]

    # Requester got confirmation.
    update.message.reply_text.assert_called_once()
    assert "送出" in update.message.reply_text.call_args[0][0]

    # Store has pending record with the reason.
    rec = get_request_store().get(STRANGER_ID)
    assert rec is not None
    assert rec["reason"] == "測試用途"


@pytest.mark.asyncio
async def test_request_rejects_admin():
    update, ctx = _mk_update(ADMIN_ID)
    await request_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "admin" in text.lower()


@pytest.mark.asyncio
async def test_request_rejects_guest():
    get_store().add_guest(STRANGER_ID, name="Alice")
    update, ctx = _mk_update(STRANGER_ID)
    await request_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "guest" in text.lower()


@pytest.mark.asyncio
async def test_request_rejects_anonymous():
    update, ctx = _mk_update(STRANGER_ID, username="", first_name="")
    await request_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "無效" in text
    assert get_request_store().get(STRANGER_ID) is None


# ── Approve / Deny callback ────────────────────────────────────────────


def _mk_callback_query(user_id: int, data: str):
    query = MagicMock()
    query.from_user.id = user_id
    query.from_user.username = "admin"
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    ctx = MagicMock()
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    ctx.bot.edit_message_text = AsyncMock()
    return update, ctx, query


@pytest.mark.asyncio
async def test_approve_callback_adds_guest_and_notifies():
    get_request_store().create(STRANGER_ID, "alice", "Alice", "r")
    get_request_store().set_notified(STRANGER_ID, ADMIN_ID, 42)

    update, ctx, query = _mk_callback_query(ADMIN_ID, f"req:ok:{STRANGER_ID}")
    await request_callback(update, ctx)

    assert get_store().role(STRANGER_ID) is Role.GUEST
    rec = get_request_store().get(STRANGER_ID)
    assert rec["status"] == "approved"

    # Approval message edited (in all notified admin chats).
    ctx.bot.edit_message_text.assert_called()

    # Requester informed.
    ctx.bot.send_message.assert_called()
    requester_call = [
        c
        for c in ctx.bot.send_message.call_args_list
        if c.kwargs.get("chat_id") == STRANGER_ID
    ]
    assert len(requester_call) == 1
    assert "通過" in requester_call[0].kwargs["text"]


@pytest.mark.asyncio
async def test_deny_callback_denies_and_notifies():
    get_request_store().create(STRANGER_ID, "alice", "Alice", "r")
    update, ctx, query = _mk_callback_query(ADMIN_ID, f"req:no:{STRANGER_ID}")
    await request_callback(update, ctx)

    rec = get_request_store().get(STRANGER_ID)
    assert rec["status"] == "denied"
    assert get_store().role(STRANGER_ID) is Role.UNREGISTERED

    requester_call = [
        c
        for c in ctx.bot.send_message.call_args_list
        if c.kwargs.get("chat_id") == STRANGER_ID
    ]
    assert len(requester_call) == 1
    assert "未通過" in requester_call[0].kwargs["text"]


@pytest.mark.asyncio
async def test_non_admin_callback_rejected():
    get_request_store().create(STRANGER_ID, "alice", "Alice", "r")
    update, ctx, query = _mk_callback_query(STRANGER_ID, f"req:ok:{STRANGER_ID}")
    await request_callback(update, ctx)
    query.answer.assert_called_once()
    args = query.answer.call_args
    text = args[0][0] if args[0] else args.kwargs.get("text", "")
    assert "管理員" in text
    assert get_store().role(STRANGER_ID) is Role.UNREGISTERED


@pytest.mark.asyncio
async def test_second_decision_reports_already_handled():
    get_request_store().create(STRANGER_ID, "alice", "Alice", "r")
    # First admin approves.
    update1, ctx1, _ = _mk_callback_query(ADMIN_ID, f"req:ok:{STRANGER_ID}")
    await request_callback(update1, ctx1)

    # Second admin clicks deny on their (stale) message.
    update2, ctx2, query2 = _mk_callback_query(ADMIN_ID, f"req:no:{STRANGER_ID}")
    await request_callback(update2, ctx2)

    # The store already has status=approved, so we expect a "已處理" toast.
    query2.answer.assert_called()
    # Guest stays approved.
    assert get_store().role(STRANGER_ID) is Role.GUEST


# ── /pending ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_lists_open_requests():
    get_request_store().create(300, "alice", "Alice", "")
    get_request_store().create(301, "bob", "Bob", "please")
    update, ctx = _mk_update(ADMIN_ID)
    await pending_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "300" in text
    assert "301" in text
    assert "please" in text


@pytest.mark.asyncio
async def test_pending_empty_message():
    update, ctx = _mk_update(ADMIN_ID)
    await pending_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "沒有" in text
