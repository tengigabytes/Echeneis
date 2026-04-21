"""Async client for the Echeneis gateway.

Wraps HTTP calls to the gateway's /chat/completions, /health,
and /routes endpoints.
"""

import json
import logging
import os
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "http://localhost:4000"
_REQUEST_TIMEOUT = 150.0  # must exceed gateway's 120s per-provider timeout


class GatewayClient:
    """Async HTTP client for the Echeneis gateway."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or os.environ.get(
            "ECHENEIS_GATEWAY_URL", _DEFAULT_GATEWAY_URL
        )
        api_key = os.environ.get("ECHENEIS_API_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=_REQUEST_TIMEOUT, headers=headers
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        command: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion request to the gateway.

        Args:
            messages: OpenAI-format message list.
            command: Optional routing command ("/think", "/fast").
            model: Optional model name to bypass automatic routing.
            **kwargs: Extra params forwarded to litellm (temperature, etc.).

        Returns:
            Gateway response dict (OpenAI completion format).

        Raises:
            GatewayError: On non-2xx response or connection failure.
        """
        body: dict[str, Any] = {"messages": messages}
        if command:
            body["command"] = command
        if model:
            body["model"] = model
        body.update(kwargs)

        try:
            resp = await self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Gateway HTTP %d: %s", e.response.status_code, e.response.text)
            raise GatewayError(
                f"Gateway returned {e.response.status_code}", e.response.status_code
            ) from e
        except httpx.RequestError as e:
            logger.error("Gateway connection error: %s", e)
            raise GatewayError(f"Cannot reach gateway: {e}") from e

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        command: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream chat completion deltas from the gateway.

        Opens an SSE connection and yields text content deltas as they
        arrive. Usage/terminal chunks (no ``delta.content``) are silently
        consumed. Byte-level streaming keeps the upstream connection active,
        which matters when the gateway is fronted by a Cloudflare tunnel
        whose edge proxy enforces a 100 s idle timeout between chunks.

        Args:
            messages: OpenAI-format message list.
            command: Optional routing command ("/think", "/fast").
            model: Optional model name to bypass automatic routing.
            **kwargs: Extra params forwarded to litellm.

        Yields:
            Successive content delta strings. Empty strings are filtered.

        Raises:
            GatewayError: On connection failure, non-2xx status, or an
                error frame mid-stream.
        """
        body: dict[str, Any] = {"messages": messages, "stream": True}
        if command:
            body["command"] = command
        if model:
            body["model"] = model
        body.update(kwargs)

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=body
            ) as resp:
                if resp.status_code >= 400:
                    # Read body for the error message — non-stream path.
                    text = (await resp.aread()).decode("utf-8", errors="replace")
                    logger.error("Gateway HTTP %d: %s", resp.status_code, text)
                    raise GatewayError(
                        f"Gateway returned {resp.status_code}", resp.status_code
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            return
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Dropping malformed SSE frame: %r", data[:200])
                        continue
                    if "error" in chunk:
                        err = chunk["error"]
                        raise GatewayError(
                            f"Mid-stream error: {err.get('message', err)}"
                        )
                    choices = chunk.get("choices") or []
                    if not choices:
                        # Usage-only terminal chunk (include_usage).
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.RequestError as e:
            logger.error("Gateway connection error (stream): %s", e)
            raise GatewayError(f"Cannot reach gateway: {e}") from e

    async def health(self) -> dict[str, Any]:
        """Check gateway health status (authenticated, includes providers)."""
        resp = await self._client.get("/health/detail")
        resp.raise_for_status()
        return resp.json()

    async def routes(self) -> dict[str, Any]:
        """Get current routing configuration."""
        resp = await self._client.get("/routes")
        resp.raise_for_status()
        return resp.json()

    async def models(self) -> dict[str, Any]:
        """List available models."""
        resp = await self._client.get("/models")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


class GatewayError(Exception):
    """Raised when the gateway returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
