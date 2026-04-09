"""Tests for the gateway client."""

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
        gw._client = httpx.AsyncClient(
            transport=transport, base_url="http://test:4000"
        )

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
        gw._client = httpx.AsyncClient(
            transport=transport, base_url="http://test:4000"
        )

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
        gw._client = httpx.AsyncClient(
            transport=transport, base_url="http://test:4000"
        )

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
        gw._client = httpx.AsyncClient(
            transport=transport, base_url="http://test:4000"
        )

        with pytest.raises(GatewayError) as exc_info:
            await gw.chat(messages=[{"role": "user", "content": "Hi"}])
        assert exc_info.value.status_code is None
        await gw.close()


class TestHealth:
    """Tests for the health method."""

    @pytest.mark.asyncio
    async def test_health_returns_status(self):
        """Health returns parsed response."""
        mock_resp = {"status": "ok", "providers": {}}
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_resp)
        )
        gw = GatewayClient(base_url="http://test:4000")
        gw._client = httpx.AsyncClient(
            transport=transport, base_url="http://test:4000"
        )

        result = await gw.health()
        assert result["status"] == "ok"
        await gw.close()
