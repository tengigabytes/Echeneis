"""Append-only audit log for admin actions.

Writes one JSON object per line to ``state/admin_audit.jsonl``. Used by
every handler that performs a privileged mutation (add/remove/ban
guests, broadcast, approve/deny requests, reload).

Append-only design means a compromised admin can't erase their tracks
by editing this file; the *only* way to lose entries is to truncate
the file itself, which is obviously visible.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_audit_file() -> str:
    state_dir = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
    return os.path.join(state_dir, "admin_audit.jsonl")


_lock = threading.Lock()


def log_admin_action(
    admin_id: int,
    action: str,
    target: int | str | None = None,
    detail: str = "",
    admin_username: str = "",
    *,
    audit_file: str | None = None,
) -> None:
    """Append an audit entry.

    Swallows I/O errors after logging them — auditing must never block
    the user-facing action. The bot would rather succeed without a
    record than refuse an operation because the disk is full.
    """
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "admin_id": admin_id,
        "admin_username": admin_username,
        "action": action,
    }
    if target is not None:
        entry["target"] = target
    if detail:
        entry["detail"] = detail

    path = Path(audit_file or _default_audit_file())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as exc:
        logger.warning("Failed to write audit entry: %s", exc)


def tail_audit(n: int = 50, *, audit_file: str | None = None) -> list[dict[str, Any]]:
    """Return the last ``n`` audit entries in chronological order."""
    path = Path(audit_file or _default_audit_file())
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        logger.warning("Failed to read audit log: %s", exc)
        return []
    tail = lines[-n:]
    entries: list[dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
