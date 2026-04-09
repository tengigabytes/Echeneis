"""Telegram Bot middleware.

Handles authentication via user whitelist.
"""

import logging
import os

from telegram import Update

logger = logging.getLogger(__name__)


def _load_allowed_users() -> set[int]:
    """Load allowed Telegram user IDs from environment.

    Expects TELEGRAM_ALLOWED_USERS as a comma-separated list of
    integer user IDs, e.g. "123456,789012".

    Returns:
        Set of allowed user IDs. Empty set means NO users are allowed.
    """
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if not raw:
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                logger.warning("Invalid user ID in TELEGRAM_ALLOWED_USERS: %r", part)
    return ids


# Module-level cache; loaded once at import time.
_allowed_users: set[int] | None = None


def get_allowed_users() -> set[int]:
    """Get the set of allowed user IDs (cached)."""
    global _allowed_users
    if _allowed_users is None:
        _allowed_users = _load_allowed_users()
        logger.info("Whitelist loaded: %d user(s)", len(_allowed_users))
    return _allowed_users


def is_authorized(update: Update) -> bool:
    """Check whether the update's sender is in the whitelist.

    Args:
        update: Incoming Telegram update.

    Returns:
        True if the user is allowed, False otherwise.
    """
    if not update.effective_user:
        return False
    allowed = get_allowed_users()
    if not allowed:
        logger.warning("Whitelist is empty — all users are blocked")
        return False
    return update.effective_user.id in allowed


def reset_whitelist() -> None:
    """Force reload of the whitelist on next check (for testing)."""
    global _allowed_users
    _allowed_users = None
