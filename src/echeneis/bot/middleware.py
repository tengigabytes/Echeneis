"""Telegram Bot authentication middleware.

Provides role-based access decorators for handlers. Uses the UserStore
singleton to resolve roles (admin / guest / unregistered).
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from echeneis.bot.user_store import Role, get_store

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def _user_id(update: Update) -> int | None:
    if update.effective_user is None:
        return None
    return update.effective_user.id


def is_authorized(update: Update) -> bool:
    """Return True if sender is admin or non-banned guest.

    Retained for backward compatibility with handlers that have not yet
    been converted to use @require_user. New handlers should use the
    decorators below.
    """
    uid = _user_id(update)
    if uid is None:
        return False
    return get_store().is_authorized(uid)


def is_admin(update: Update) -> bool:
    uid = _user_id(update)
    if uid is None:
        return False
    return get_store().is_admin(uid)


def role_of(update: Update) -> Role:
    uid = _user_id(update)
    if uid is None:
        return Role.UNREGISTERED
    return get_store().role(uid)


# ── Decorators ────────────────────────────────────────────────────────


def require_admin(handler: Handler) -> Handler:
    """Allow only admins; silently drop others.

    Silent drop (not a reply) matches prior is_authorized behavior —
    bots that reply to unauthorized users help attackers confirm the
    bot exists.
    """

    @functools.wraps(handler)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> Any:
        uid = _user_id(update)
        if uid is None or not get_store().is_admin(uid):
            logger.debug(
                "require_admin: denied for user_id=%s on %s",
                uid,
                handler.__name__,
            )
            return None
        return await handler(update, context)

    return wrapper


def require_user(handler: Handler) -> Handler:
    """Allow admins and non-banned guests; silently drop others."""

    @functools.wraps(handler)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> Any:
        uid = _user_id(update)
        if uid is None or not get_store().is_authorized(uid):
            logger.debug(
                "require_user: denied for user_id=%s on %s",
                uid,
                handler.__name__,
            )
            return None
        return await handler(update, context)

    return wrapper


def public(handler: Handler) -> Handler:
    """Allow anyone, including unregistered users.

    Used for /whoami, /start, /help, /request. A pass-through for now,
    but kept as a decorator so the intent is explicit at the call site
    and so future rate-limiting for public endpoints has a single hook.
    """
    return handler


# ── Legacy API (kept for existing tests) ──────────────────────────────


def reset_whitelist() -> None:
    """Force reload of the user store on next check (for testing)."""
    from echeneis.bot.user_store import reset_store

    reset_store()


def get_allowed_users() -> set[int]:
    """Return the admin ID set (legacy API).

    Legacy tests expect this to mirror TELEGRAM_ALLOWED_USERS env. New code
    should use UserStore directly to get admins vs guests distinctly.
    """
    return set(get_store().list_admins())
