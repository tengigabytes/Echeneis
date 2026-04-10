"""Async client for the Echeneis gateway.

Wraps HTTP calls to the gateway's /chat/completions, /health,
and /routes endpoints.
"""

import logging
import os
from typing import Any

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
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        headers = {"Authorization": f"Bearer {master_key}"} if master_key else {}
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

    async def health(self) -> dict[str, Any]:
        """Check gateway health status."""
        resp = await self._client.get("/health")
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
