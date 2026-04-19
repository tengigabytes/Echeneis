"""Persistent store for registration requests.

When an unregistered user wants access, they submit a request (via
``/request`` or the inline keyboard prompt). Requests live in
``state/pending_requests.json`` until an admin approves or denies them
through the inline keyboard pushed to admin DMs.

Abuse controls:
- a given user may open only one pending request at a time
- after a denial, re-requests are blocked for ``DENY_COOLDOWN_SECS``
- successive /request attempts while pending/cooling are idempotent
  (no new entry is created)
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

DENY_COOLDOWN_SECS = 7 * 86400  # 7 days
REQUEST_RATE_LIMIT_SECS = 86400  # 24 hours between repeat /request attempts
SCHEMA_VERSION = 1


class Status(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


def _default_requests_file() -> str:
    state_dir = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
    return os.path.join(state_dir, "pending_requests.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class RequestStore:
    """Thread-safe persistent store for registration requests."""

    def __init__(self, requests_file: str | None = None) -> None:
        self._path = Path(requests_file or _default_requests_file())
        self._lock = threading.RLock()
        self._data: dict[int, dict[str, Any]] = {}
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open() as f:
                payload = json.load(f)
            entries = payload.get("requests", {})
            for uid_str, record in entries.items():
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    self._data[uid] = record
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load pending_requests.json: %s", exc)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "requests": {str(uid): rec for uid, rec in self._data.items()},
        }
        fd, tmp = tempfile.mkstemp(
            prefix=".pending-", suffix=".json", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── Query ────────────────────────────────────────────────────────

    def get(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            rec = self._data.get(user_id)
            return dict(rec) if rec else None

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = [
                dict(rec)
                for rec in self._data.values()
                if rec.get("status") == Status.PENDING.value
            ]
        pending.sort(key=lambda r: r.get("requested_at", ""))
        return pending

    def _cooldown_remaining(self, record: dict[str, Any]) -> int:
        """Seconds until a denied request can be retried (0 if ready)."""
        if record.get("status") != Status.DENIED.value:
            return 0
        decided = _parse_iso(record.get("decided_at", ""))
        if not decided:
            return 0
        now = datetime.now(timezone.utc)
        elapsed = (now - decided).total_seconds()
        return max(0, int(DENY_COOLDOWN_SECS - elapsed))

    def can_submit(self, user_id: int) -> tuple[bool, str]:
        """Check whether a user may submit a (new) request.

        Returns ``(allowed, reason)``; reason is empty on success.
        """
        with self._lock:
            rec = self._data.get(user_id)
        if rec is None:
            return True, ""
        status = rec.get("status")
        if status == Status.PENDING.value:
            return False, "你的申請已在處理中，請等待管理員回覆。"
        if status == Status.APPROVED.value:
            return False, "你已經是註冊用戶。"
        if status == Status.DENIED.value:
            remaining = self._cooldown_remaining(rec)
            if remaining > 0:
                hours = remaining // 3600
                return False, (
                    f"你的申請曾被拒絕，請於 {hours} 小時後再試。"
                )
            return True, ""
        return True, ""

    # ── Mutation ─────────────────────────────────────────────────────

    def create(
        self,
        user_id: int,
        username: str,
        first_name: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Open a new pending request. Returns the new record or None
        if the user cannot currently submit.
        """
        allowed, _ = self.can_submit(user_id)
        if not allowed:
            return None
        now = _now_iso()
        record = {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "",
            "reason": (reason or "").strip()[:300],
            "requested_at": now,
            "status": Status.PENDING.value,
            "notified_admins": {},  # admin_id -> message_id (filled by handler)
            "decided_by": None,
            "decided_at": None,
        }
        with self._lock:
            self._data[user_id] = record
            self._persist()
        return dict(record)

    def set_notified(self, user_id: int, admin_id: int, message_id: int) -> None:
        with self._lock:
            rec = self._data.get(user_id)
            if rec is None:
                return
            notified = rec.setdefault("notified_admins", {})
            notified[str(admin_id)] = message_id
            self._persist()

    def approve(self, user_id: int, admin_id: int) -> dict[str, Any] | None:
        return self._decide(user_id, admin_id, Status.APPROVED)

    def deny(self, user_id: int, admin_id: int) -> dict[str, Any] | None:
        return self._decide(user_id, admin_id, Status.DENIED)

    def _decide(
        self, user_id: int, admin_id: int, status: Status
    ) -> dict[str, Any] | None:
        with self._lock:
            rec = self._data.get(user_id)
            if rec is None:
                return None
            if rec.get("status") != Status.PENDING.value:
                return None  # already decided
            rec["status"] = status.value
            rec["decided_by"] = admin_id
            rec["decided_at"] = _now_iso()
            self._persist()
            return dict(rec)

    # ── Lifecycle ────────────────────────────────────────────────────

    def reload(self) -> None:
        with self._lock:
            self._data.clear()
            self._load()


# ── Singleton ────────────────────────────────────────────────────────

_store: RequestStore | None = None
_store_lock = threading.Lock()


def get_request_store() -> RequestStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RequestStore()
    return _store


def reset_request_store() -> None:
    global _store
    with _store_lock:
        _store = None
