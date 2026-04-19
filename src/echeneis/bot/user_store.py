"""Persistent user store and role model for the Telegram bot.

Three roles exist:
  - Admin: static, seeded from TELEGRAM_ADMIN_USERS env (cannot be removed
    at runtime; reboot-required)
  - Guest: dynamic, stored in ``state/users.json`` (admins manage via
    /adduser, /removeuser, /ban, /unban)
  - Unregistered: anyone not in either list

Migration note: if TELEGRAM_ADMIN_USERS is unset but the legacy
TELEGRAM_ALLOWED_USERS is set, the legacy list is treated as admins to
keep existing deployments working during the rollout.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Role(enum.Enum):
    """Permission level for a user interacting with the bot."""

    ADMIN = "admin"
    GUEST = "guest"
    UNREGISTERED = "unregistered"


_SCHEMA_VERSION = 1


def _default_users_file() -> str:
    """Resolve the users.json path from env at call time (test-friendly)."""
    state_dir = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
    return os.path.join(state_dir, "users.json")


def _parse_id_list(raw: str) -> set[int]:
    """Parse a comma-separated list of integer user IDs."""
    if not raw or not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Invalid user ID in env list: %r", part)
    return ids


def _load_admins() -> set[int]:
    """Load admin IDs from env, with legacy fallback."""
    admins = _parse_id_list(os.environ.get("TELEGRAM_ADMIN_USERS", ""))
    if admins:
        return admins
    # Legacy fallback: if no TELEGRAM_ADMIN_USERS set, treat the old
    # TELEGRAM_ALLOWED_USERS as admins for smooth migration.
    legacy = _parse_id_list(os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
    if legacy:
        logger.info(
            "TELEGRAM_ADMIN_USERS not set; using legacy TELEGRAM_ALLOWED_USERS "
            "(%d user(s)) as admins for migration",
            len(legacy),
        )
    return legacy


class UserStore:
    """Thread-safe persistent store for guest users + in-memory admin set.

    Admin set is immutable at runtime (loaded once from env). Guest set is
    mutable and persisted atomically to ``users.json``.
    """

    def __init__(self, users_file: str | None = None) -> None:
        self._path = Path(users_file or _default_users_file())
        self._lock = threading.RLock()
        self._admins: set[int] = _load_admins()
        self._guests: dict[int, dict[str, Any]] = {}
        self._load()
        logger.info(
            "User store initialized: %d admin(s), %d guest(s)",
            len(self._admins),
            len(self._guests),
        )

    # ── Loading / persistence ─────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.error("users.json malformed (not an object) — ignoring")
                return
            users = data.get("users", {})
            for uid_str, record in users.items():
                try:
                    uid = int(uid_str)
                except ValueError:
                    logger.warning("Skipping non-integer user ID: %r", uid_str)
                    continue
                if not isinstance(record, dict):
                    continue
                self._guests[uid] = record
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load users.json: %s", exc)

    def _persist(self) -> None:
        """Atomically write the guest map to disk (tmp + rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _SCHEMA_VERSION,
            "users": {str(uid): record for uid, record in self._guests.items()},
        }
        # Atomic write: write to tmp in same dir, then rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".users-", suffix=".json", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def reload(self) -> None:
        """Force re-read users.json from disk (for /reload command)."""
        with self._lock:
            self._guests.clear()
            self._admins = _load_admins()
            self._load()
            logger.info(
                "User store reloaded: %d admin(s), %d guest(s)",
                len(self._admins),
                len(self._guests),
            )

    # ── Queries ───────────────────────────────────────────────────────

    def role(self, user_id: int) -> Role:
        """Return the role of a user ID."""
        with self._lock:
            if user_id in self._admins:
                return Role.ADMIN
            record = self._guests.get(user_id)
            if record is None:
                return Role.UNREGISTERED
            if record.get("banned"):
                return Role.UNREGISTERED
            return Role.GUEST

    def is_admin(self, user_id: int) -> bool:
        return self.role(user_id) is Role.ADMIN

    def is_authorized(self, user_id: int) -> bool:
        """True if user is admin or non-banned guest."""
        return self.role(user_id) in (Role.ADMIN, Role.GUEST)

    def get_guest(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            record = self._guests.get(user_id)
            return dict(record) if record else None

    def list_admins(self) -> list[int]:
        with self._lock:
            return sorted(self._admins)

    def list_guests(self) -> list[tuple[int, dict[str, Any]]]:
        """Return (user_id, record) pairs sorted by added_at."""
        with self._lock:
            items = list(self._guests.items())
        items.sort(key=lambda kv: kv[1].get("added_at", ""))
        return [(uid, dict(rec)) for uid, rec in items]

    # ── Mutations ─────────────────────────────────────────────────────

    def add_guest(
        self,
        user_id: int,
        name: str = "",
        added_by: int = 0,
        notes: str = "",
    ) -> bool:
        """Add a new guest. Returns False if already registered or is admin."""
        with self._lock:
            if user_id in self._admins:
                return False
            if user_id in self._guests and not self._guests[user_id].get("banned"):
                return False
            self._guests[user_id] = {
                "name": name,
                "added_by": added_by,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "banned": False,
                "notes": notes,
            }
            self._persist()
        return True

    def remove_guest(self, user_id: int) -> bool:
        """Remove a guest entry. Returns False if not present."""
        with self._lock:
            if user_id not in self._guests:
                return False
            del self._guests[user_id]
            self._persist()
        return True

    def set_banned(self, user_id: int, banned: bool) -> bool:
        """Set banned flag on a guest. Returns False if not present."""
        with self._lock:
            record = self._guests.get(user_id)
            if record is None:
                return False
            record["banned"] = banned
            self._persist()
        return True


# ── Module-level singleton ────────────────────────────────────────────

_store: UserStore | None = None
_store_lock = threading.Lock()


def get_store() -> UserStore:
    """Return the singleton UserStore (thread-safe, lazy-initialized)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = UserStore()
    return _store


def reset_store() -> None:
    """Reset the singleton (for testing)."""
    global _store
    with _store_lock:
        _store = None
