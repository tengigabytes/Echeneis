"""MCP delegation server.

Exposes Echeneis gateway capabilities as MCP tools,
allowing Claude Code and other agents to delegate
tasks to the free model pool.
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from echeneis.bot.gateway_client import GatewayClient, GatewayError

logger = logging.getLogger(__name__)

mcp = FastMCP("echeneis", log_level="WARNING")

_client: GatewayClient | None = None


def _get_client() -> GatewayClient:
    """Return a lazily-initialized gateway client singleton."""
    global _client
    if _client is None:
        _client = GatewayClient()
    return _client


def _extract_text(response: dict[str, Any]) -> str:
    """Extract plain text content from an OpenAI-format completion response."""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "[echeneis error] Unexpected response format"


@mcp.tool()
async def delegate_code_task(
    prompt: str,
    context: str = "",
    language: str = "",
    max_tokens: int = 4096,
) -> str:
    """Delegate a coding task to the Echeneis free model pool.

    Args:
        prompt: The coding task description.
        context: Optional existing code or file contents for reference.
        language: Programming language hint (e.g. "python", "c").
        max_tokens: Maximum response length in tokens.
    """
    system_parts = ["You are a code assistant."]
    if language:
        system_parts.append(f"Language: {language}.")
    if context:
        system_parts.append(f"Reference code:\n```\n{context}\n```")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": " ".join(system_parts)},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = await _get_client().chat(messages, max_tokens=max_tokens)
        return _extract_text(resp)
    except GatewayError as e:
        return f"[echeneis error] {e}"


@mcp.tool()
async def delegate_translate(
    text: str,
    target_language: str,
    source_language: str = "",
    preserve_terms: str = "",
) -> str:
    """Delegate a translation task to the Echeneis free model pool.

    Args:
        text: The text to translate.
        target_language: Target language (e.g. "zh-TW", "en", "ja").
        source_language: Optional source language.
        preserve_terms: Comma-separated terms to keep untranslated.
    """
    system_parts = [f"Translate the following text to {target_language}."]
    if source_language:
        system_parts.append(f"The source language is {source_language}.")
    if preserve_terms:
        system_parts.append(f"Keep these terms untranslated: {preserve_terms}.")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": " ".join(system_parts)},
        {"role": "user", "content": text},
    ]

    try:
        resp = await _get_client().chat(messages)
        return _extract_text(resp)
    except GatewayError as e:
        return f"[echeneis error] {e}"


@mcp.tool()
async def delegate_batch(
    prompt: str,
    max_tokens: int = 2048,
) -> str:
    """Delegate a batch/fast task to the Echeneis free model pool (tier B).

    Uses the fastest, lowest-cost models for simple tasks like
    format conversion, labeling, or simple summarization.

    Args:
        prompt: The task description.
        max_tokens: Maximum response length in tokens.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    try:
        resp = await _get_client().chat(
            messages, command="/fast", max_tokens=max_tokens
        )
        return _extract_text(resp)
    except GatewayError as e:
        return f"[echeneis error] {e}"


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.WARNING,
        stream=__import__("sys").stderr,
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
