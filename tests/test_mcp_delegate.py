"""Tests for the MCP delegation server."""

import json

import httpx
import pytest

from echeneis.bot.gateway_client import GatewayClient
from echeneis.mcp.delegate import (
    delegate_batch,
    delegate_code_task,
    delegate_translate,
    mcp,
)


def _sse_body(content: str) -> bytes:
    """Build an SSE body with one delta frame carrying content, then [DONE]."""
    escaped = json.dumps(content)
    frames = [
        f'data: {{"choices":[{{"delta":{{"content":{escaped}}}}}]}}',
        "",
        "data: [DONE]",
        "",
    ]
    return ("\n".join(frames) + "\n").encode("utf-8")


def _mock_gateway(
    response_content: str = "ok",
    status_code: int = 200,
    capture: dict | None = None,
) -> GatewayClient:
    """Create a GatewayClient whose transport returns an SSE stream."""

    def handler(req: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["body"] = json.loads(req.content)
            capture["url"] = str(req.url)
        if status_code >= 400:
            return httpx.Response(status_code, json={"detail": "fail"})
        return httpx.Response(
            status_code,
            content=_sse_body(response_content),
            headers={"content-type": "text/event-stream"},
        )

    gw = GatewayClient(base_url="http://test:4000")
    gw._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test:4000",
    )
    return gw


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """Reset the module-level client singleton before each test."""
    import echeneis.mcp.delegate as mod

    mod._client = None


def _inject_client(monkeypatch, gw: GatewayClient) -> None:
    """Inject a mock GatewayClient into the delegate module."""
    import echeneis.mcp.delegate as mod

    mod._client = gw


# -- delegate_code_task ------------------------------------------------------


class TestDelegateCodeTask:
    """Tests for the delegate_code_task tool."""

    @pytest.mark.asyncio
    async def test_returns_content(self, monkeypatch):
        _inject_client(monkeypatch, _mock_gateway("def foo(): pass"))
        result = await delegate_code_task(prompt="write a function")
        assert result == "def foo(): pass"

    @pytest.mark.asyncio
    async def test_includes_context_in_messages(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_code_task(
            prompt="refactor this",
            context="def old(): pass",
            language="python",
        )
        body = captured["body"]
        system_msg = body["messages"][0]["content"]
        assert "python" in system_msg.lower()
        assert "def old(): pass" in system_msg

    @pytest.mark.asyncio
    async def test_forwards_max_tokens(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_code_task(prompt="test", max_tokens=1024)
        assert captured["body"]["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_sets_stream_true(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_code_task(prompt="test")
        assert captured["body"]["stream"] is True

    @pytest.mark.asyncio
    async def test_returns_error_on_gateway_failure(self, monkeypatch):
        _inject_client(monkeypatch, _mock_gateway(status_code=502))
        result = await delegate_code_task(prompt="test")
        assert "[echeneis error]" in result


# -- delegate_translate ------------------------------------------------------


class TestDelegateTranslate:
    """Tests for the delegate_translate tool."""

    @pytest.mark.asyncio
    async def test_returns_translated_text(self, monkeypatch):
        _inject_client(monkeypatch, _mock_gateway("Hola mundo"))
        result = await delegate_translate(text="Hello world", target_language="es")
        assert result == "Hola mundo"

    @pytest.mark.asyncio
    async def test_system_prompt_contains_target_language(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_translate(text="hi", target_language="zh-TW")
        system_msg = captured["body"]["messages"][0]["content"]
        assert "zh-TW" in system_msg
        assert "translate" in system_msg.lower()

    @pytest.mark.asyncio
    async def test_preserve_terms_in_system_prompt(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_translate(
            text="Configure the GPIO pin",
            target_language="zh-TW",
            preserve_terms="GPIO, SPI, UART",
        )
        system_msg = captured["body"]["messages"][0]["content"]
        assert "GPIO, SPI, UART" in system_msg

    @pytest.mark.asyncio
    async def test_source_language_in_system_prompt(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_translate(
            text="hello", target_language="ja", source_language="en"
        )
        system_msg = captured["body"]["messages"][0]["content"]
        assert "en" in system_msg


# -- delegate_batch ----------------------------------------------------------


class TestDelegateBatch:
    """Tests for the delegate_batch tool."""

    @pytest.mark.asyncio
    async def test_returns_content(self, monkeypatch):
        _inject_client(monkeypatch, _mock_gateway("done"))
        result = await delegate_batch(prompt="format this JSON")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_uses_fast_command(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_batch(prompt="convert csv to json")
        assert captured["body"]["command"] == "/fast"

    @pytest.mark.asyncio
    async def test_forwards_max_tokens(self, monkeypatch):
        captured: dict = {}
        _inject_client(monkeypatch, _mock_gateway("ok", capture=captured))
        await delegate_batch(prompt="test", max_tokens=512)
        assert captured["body"]["max_tokens"] == 512


# -- MCP server registration ------------------------------------------------


class TestMCPServerRegistration:
    """Tests that the MCP server has the expected tools."""

    def test_server_has_three_tools(self):
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        assert tool_names == {
            "delegate_code_task",
            "delegate_translate",
            "delegate_batch",
        }
