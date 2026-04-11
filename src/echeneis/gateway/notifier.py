"""Telegram notifier for gateway-side events.

Used for fire-and-forget alerts such as circuit breaker state changes.
Silently no-ops if TELEGRAM_BOT_TOKEN / chat id are not configured,
so it is safe to call from anywhere.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_TIMEOUT = 10.0


def _resolve_chat_id() -> str:
    """Return the admin chat id, falling back to the first allowed user."""
    admin = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if admin:
        return admin
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if not allowed:
        return ""
    return allowed.split(",")[0].strip()


async def send_telegram(text: str) -> None:
    """Send a Telegram message to the admin chat.

    Fire-and-forget: failures are logged but never raised, so the caller
    does not need to worry about notification errors crashing real work.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = _resolve_chat_id()
    if not token or not chat_id:
        return

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_TELEGRAM_API}/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Telegram notify returned %d: %s", resp.status_code, resp.text
                )
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)
