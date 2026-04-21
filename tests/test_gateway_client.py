"""Tests for the gateway client."""

from typing import Any

import httpx
import pytest

from echeneis.bot.gateway_client import GatewayClient, GatewayError


@pytest.fixture()
def mock_transport():
    """Create a mock transport for httpx."""
    return httpx.MockTransport(lambda req: httpx.Response(200, json={}))


@pytest.fixture()
def client(mock_transport):
    """Create a GatewayClient with mocked transport."""
    gw = GatewayClient(base_url="http://test-gateway:4000")
    gw._client = httpx.AsyncClient(
        transport=mock_transport, base_url="http://test-gateway:4000"
    )
    return gw


class TestChat:
    """Tests for the chat method."""

    @pytest.mark.asyncio
    async def test_successful_chat(self):
        """Chat returns parsed response on success."""
        mock_response = {
            "choices": [{"message": {"content": "Hello!"}}],
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_response)
        )
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        result = await gw.chat(messages=[{"role": "user", "content": "Hi"}])
        assert result["choices"][0]["message"]["content"] == "Hello!"
        await gw.close()

    @pytest.mark.asyncio
    async def test_chat_with_command(self):
        """Chat includes command in request body."""
        captured = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"choices": []})

        transport = httpx.MockTransport(handler)
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        await gw.chat(
            messages=[{"role": "user", "content": "test"}],
            command="/think",
        )
        assert captured["body"]["command"] == "/think"
        await gw.close()

    @pytest.mark.asyncio
    async def test_chat_gateway_error_on_502(self):
        """Chat raises GatewayError on non-2xx response."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(502, json={"detail": "All models exhausted"})
        )
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        with pytest.raises(GatewayError) as exc_info:
            await gw.chat(messages=[{"role": "user", "content": "Hi"}])
        assert exc_info.value.status_code == 502
        await gw.close()

    @pytest.mark.asyncio
    async def test_chat_connection_error(self):
        """Chat raises GatewayError on connection failure."""

        def fail(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(fail)
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        with pytest.raises(GatewayError) as exc_info:
            await gw.chat(messages=[{"role": "user", "content": "Hi"}])
        assert exc_info.value.status_code is None
        await gw.close()


class TestChatStream:
    """Tests for the streaming chat method."""

    @staticmethod
    def _sse_body(lines: list[str]) -> bytes:
        return ("\n".join(lines) + "\n").encode("utf-8")

    @pytest.mark.asyncio
    async def test_stream_accumulates_deltas(self):
        """chat_stream yields successive content deltas then stops at [DONE]."""
        body = TestChatStream._sse_body(
            [
                'data: {"choices":[{"delta":{"content":"Hel"}}]}',
                "",
                'data: {"choices":[{"delta":{"content":"lo"}}]}',
                "",
                'data: {"choices":[{"delta":{"content":"!"}}]}',
                "",
                'data: {"choices":[],"usage":{"total_tokens":5}}',
                "",
                "data: [DONE]",
                "",
            ]
        )
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            )
        )
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        chunks = []
        async for delta in gw.chat_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(delta)
        assert "".join(chunks) == "Hello!"
        await gw.close()

    @pytest.mark.asyncio
    async def test_stream_drops_malformed_frames(self):
        """chat_stream skips non-JSON data frames without raising."""
        body = TestChatStream._sse_body(
            [
                "data: <<<not json>>>",
                "",
                'data: {"choices":[{"delta":{"content":"ok"}}]}',
                "",
                "data: [DONE]",
                "",
            ]
        )
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        messages = [{"role": "user", "content": "x"}]
        chunks = [c async for c in gw.chat_stream(messages=messages)]
        assert chunks == ["ok"]
        await gw.close()

    @pytest.mark.asyncio
    async def test_stream_raises_on_mid_stream_error_frame(self):
        """An error frame inside the stream surfaces as GatewayError."""
        body = TestChatStream._sse_body(
            [
                'data: {"choices":[{"delta":{"content":"partial "}}]}',
                "",
                'data: {"error":{"message":"provider exploded","type":"RuntimeError"}}',
                "",
                "data: [DONE]",
                "",
            ]
        )
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        got: list[str] = []
        with pytest.raises(GatewayError) as exc_info:
            async for delta in gw.chat_stream(
                messages=[{"role": "user", "content": "x"}]
            ):
                got.append(delta)
        assert got == ["partial "]
        assert "provider exploded" in str(exc_info.value)
        await gw.close()

    @pytest.mark.asyncio
    async def test_stream_raises_on_4xx(self):
        """Non-2xx response at stream open raises GatewayError with status."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(502, json={"detail": "All models exhausted"})
        )
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        with pytest.raises(GatewayError) as exc_info:
            async for _ in gw.chat_stream(messages=[{"role": "user", "content": "x"}]):
                pass
        assert exc_info.value.status_code == 502
        await gw.close()

    @pytest.mark.asyncio
    async def test_stream_sends_stream_true_in_body(self):
        """chat_stream sets stream=True and forwards command/model."""
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            captured["body"] = _json.loads(req.content)
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        transport = httpx.MockTransport(handler)
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        async for _ in gw.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
            command="/think",
            model="gemma-4-31b",
        ):
            pass
        assert captured["body"]["stream"] is True
        assert captured["body"]["command"] == "/think"
        assert captured["body"]["model"] == "gemma-4-31b"
        await gw.close()


class TestHealth:
    """Tests for the health method."""

    @pytest.mark.asyncio
    async def test_health_returns_status(self):
        """Health returns parsed response."""
        mock_resp = {"status": "ok", "providers": {}}
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=mock_resp))
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(transport=transport, base_url="http://test:4000")

        result = await gw.health()
        assert result["status"] == "ok"
        await gw.close()
