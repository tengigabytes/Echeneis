"""Tests for Telegram bot command and message handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from echeneis.bot.conversation import ConversationStore
from echeneis.bot.handlers.commands import (
    fast_command,
    help_command,
    model_command,
    start_command,
    think_command,
)
from echeneis.bot.handlers.messages import text_message


def _make_context(gateway_mock: AsyncMock) -> MagicMock:
    """Create a mock context with gateway and conversation store in bot_data."""
    ctx = MagicMock()
    ctx.bot_data = {
        "gateway": gateway_mock,
        "conversation": ConversationStore(),
    }
    ctx.args = []
    return ctx


def _make_authorized_update(
    text: str = "",
    message_id: int = 1,
    reply_to_message_id: int | None = None,
) -> MagicMock:
    """Create a mock authorized update with a text message."""
    update = MagicMock()
    update.effective_user.id = 12345
    update.message.text = text
    update.message.caption = None
    update.message.message_id = message_id
    sent_msg = MagicMock()
    sent_msg.message_id = message_id + 1
    sent_msg.edit_text = AsyncMock()
    sent_msg.chat = MagicMock()
    update.message.reply_text = AsyncMock(return_value=sent_msg)
    if reply_to_message_id is not None:
        update.message.reply_to_message = MagicMock()
        update.message.reply_to_message.message_id = reply_to_message_id
    else:
        update.message.reply_to_message = None
    return update


@pytest.fixture(autouse=True)
def _whitelist(monkeypatch):
    """Set up whitelist for all tests."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
    from echeneis.bot.middleware import reset_whitelist

    reset_whitelist()
    yield
    reset_whitelist()


class TestStartCommand:
    """Tests for /start."""

    @pytest.mark.asyncio
    async def test_sends_welcome(self):
        """Start sends welcome message."""
        update = _make_authorized_update()
        ctx = _make_context(AsyncMock())

        await start_command(update, ctx)
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "Echeneis Bot" in call_text

    @pytest.mark.asyncio
    async def test_unregistered_sees_request_prompt(self, monkeypatch):
        """Unregistered user receives their ID and a prompt to /request."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "99999")
        from echeneis.bot.middleware import reset_whitelist

        reset_whitelist()

        update = _make_authorized_update()
        ctx = _make_context(AsyncMock())

        await start_command(update, ctx)
        update.message.reply_text.assert_called_once()
        call_text = update.message.reply_text.call_args[0][0]
        assert "未註冊" in call_text
        assert "/request" in call_text


class TestHelpCommand:
    """Tests for /help."""

    @pytest.mark.asyncio
    async def test_shows_commands(self):
        """Help lists available commands."""
        update = _make_authorized_update()
        ctx = _make_context(AsyncMock())

        await help_command(update, ctx)
        call_text = update.message.reply_text.call_args[0][0]
        assert "/think" in call_text
        assert "/fast" in call_text
        assert "/model" in call_text


class TestThinkCommand:
    """Tests for /think."""

    @pytest.mark.asyncio
    async def test_sends_to_gateway_with_think_command(self):
        """Think command sends request with /think command."""
        gateway = AsyncMock()
        gateway.chat.return_value = {
            "model": "gemini-2.5-flash",
            "choices": [{"message": {"content": "Deep thought."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

        update = _make_authorized_update()
        sent_msg = AsyncMock()
        update.message.reply_text.return_value = sent_msg

        ctx = _make_context(gateway)
        ctx.args = ["what", "is", "life"]

        await think_command(update, ctx)
        gateway.chat.assert_called_once()
        call_kwargs = gateway.chat.call_args
        assert call_kwargs.kwargs["command"] == "/think"
        reply = sent_msg.edit_text.call_args[0][0]
        assert "gemini-2.5-flash" in reply
        assert "Deep thought." in reply

    @pytest.mark.asyncio
    async def test_empty_args_shows_usage(self):
        """Think with no args shows usage."""
        update = _make_authorized_update()
        ctx = _make_context(AsyncMock())
        ctx.args = []

        await think_command(update, ctx)
        call_text = update.message.reply_text.call_args[0][0]
        assert "/think" in call_text


class TestFastCommand:
    """Tests for /fast."""

    @pytest.mark.asyncio
    async def test_sends_to_gateway_with_fast_command(self):
        """Fast command sends request with /fast command."""
        gateway = AsyncMock()
        gateway.chat.return_value = {
            "model": "llama-3.3-70b-versatile",
            "choices": [{"message": {"content": "Quick reply."}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 15},
        }

        update = _make_authorized_update()
        sent_msg = AsyncMock()
        update.message.reply_text.return_value = sent_msg

        ctx = _make_context(gateway)
        ctx.args = ["hello"]

        await fast_command(update, ctx)
        gateway.chat.assert_called_once()
        call_kwargs = gateway.chat.call_args
        assert call_kwargs.kwargs["command"] == "/fast"
        reply = sent_msg.edit_text.call_args[0][0]
        assert "llama-3.3-70b-versatile" in reply
        assert "Quick reply." in reply


class TestModelCommand:
    """Tests for /model."""

    @pytest.mark.asyncio
    async def test_shows_routes_and_health(self):
        """Model command shows routing info."""
        gateway = AsyncMock()
        gateway.health.return_value = {
            "status": "ok",
            "providers": {
                "mistral-large-3": {
                    "circuit": "closed",
                    "available": True,
                    "consecutive_failures": 0,
                },
            },
        }
        gateway.routes.return_value = {
            "tiers": {
                "A": {
                    "description": "General purpose",
                    "models": {"translation": "mistral-large-3"},
                },
            },
            "task_types": [],
        }

        update = _make_authorized_update()
        ctx = _make_context(gateway)

        await model_command(update, ctx)
        call_text = update.message.reply_text.call_args[0][0]
        assert "mistral-large-3" in call_text
        assert "General purpose" in call_text


class TestTextMessage:
    """Tests for plain text message handling."""

    @pytest.mark.asyncio
    async def test_routes_text_to_gateway(self):
        """Text message is sent to gateway without command."""
        gateway = AsyncMock()
        gateway.chat.return_value = {
            "model": "llama3.3-70b",
            "choices": [{"message": {"content": "Response."}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 12},
        }

        update = _make_authorized_update("Hello there")
        sent_msg = AsyncMock()
        update.message.reply_text.return_value = sent_msg

        ctx = _make_context(gateway)

        await text_message(update, ctx)
        gateway.chat.assert_called_once()
        call_args = gateway.chat.call_args
        assert call_args.kwargs.get("command") is None
        reply = sent_msg.edit_text.call_args[0][0]
        assert "llama3.3-70b" in reply
        assert "Response." in reply
        assert "tok/s" in reply

    @pytest.mark.asyncio
    async def test_reply_falls_back_without_usage(self):
        """Missing usage field still renders header with elapsed time only."""
        gateway = AsyncMock()
        gateway.chat.return_value = {
            "model": "llama3.3-70b",
            "choices": [{"message": {"content": "Response."}}],
        }

        update = _make_authorized_update("Hello there")
        sent_msg = AsyncMock()
        update.message.reply_text.return_value = sent_msg

        ctx = _make_context(gateway)

        await text_message(update, ctx)
        reply = sent_msg.edit_text.call_args[0][0]
        assert "llama3.3-70b" in reply
        assert "tok/s" not in reply
        assert "Response." in reply

    @pytest.mark.asyncio
    async def test_gateway_error_shows_error_message(self):
        """Gateway error results in user-friendly error message."""
        from echeneis.bot.gateway_client import GatewayError

        gateway = AsyncMock()
        gateway.chat.side_effect = GatewayError("All models exhausted", 502)

        update = _make_authorized_update("Hello")
        sent_msg = AsyncMock()
        update.message.reply_text.return_value = sent_msg

        ctx = _make_context(gateway)

        await text_message(update, ctx)
        error_text = sent_msg.edit_text.call_args[0][0]
        assert "錯誤" in error_text
