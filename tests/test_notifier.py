"""Tests for the Telegram notifier."""

import httpx
import pytest

from echeneis.gateway import notifier
from echeneis.gateway.notifier import _resolve_chat_id, send_telegram


class TestResolveChatId:
    def test_prefers_admin_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "111")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "222,333")
        assert _resolve_chat_id() == "111"

    def test_falls_back_to_first_allowed(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "222,333")
        assert _resolve_chat_id() == "222"

    def test_empty_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
        assert _resolve_chat_id() == ""


class TestSendTelegram:
    @pytest.mark.asyncio
    async def test_noop_without_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123")
        # Should not attempt any HTTP call — would raise if it did.
        await send_telegram("hello")

    @pytest.mark.asyncio
    async def test_noop_without_chat_id(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
        monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
        await send_telegram("hello")

    @pytest.mark.asyncio
    async def test_posts_when_configured(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123")

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={"ok": True})

        class _PatchedClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(notifier.httpx, "AsyncClient", _PatchedClient)
        await send_telegram("hello world")

        assert "bot abc" in captured["url"] or "botabc" in captured["url"]
        assert "123" in captured["body"]
        assert "hello world" in captured["body"]

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123")

        class _BrokenClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                raise RuntimeError("network down")

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(notifier.httpx, "AsyncClient", _BrokenClient)
        # Must not raise.
        await send_telegram("hello")
