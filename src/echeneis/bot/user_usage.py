"""Per-user × per-model usage tracking for the admin dashboard.

The gateway's UsageTracker records traffic per *model* for rate-limit
purposes. This module complements that with a per-*user* view so admins
can see who is using the bot and how much.

Writes append-only JSONL to ``state/user_usage.jsonl``. Schema:

    {"ts": epoch_seconds, "user_id": 12345, "model": "gemma-4-31b",
     "tokens_in": 245, "tokens_out": 512, "task": "chat"}

Aggregation reads the file, filters by time window, and bucketises.
For current usage patterns (tens of requests/day/user) the tail-and-scan
approach is fine. An ``archive_old_entries`` helper compacts records
older than 90 days into ``user_usage.archive.jsonl.gz`` and keeps a
running all-time summary in ``user_usage_summary.json`` so /status
doesn't have to read gigabytes once the log has grown.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ACTIVE_FILE = "user_usage.jsonl"
_ARCHIVE_FILE = "user_usage.archive.jsonl.gz"
_SUMMARY_FILE = "user_usage_summary.json"

_ARCHIVE_CUTOFF_SECS = 90 * 86400

_lock = threading.Lock()


def _state_dir() -> Path:
    return Path(os.environ.get("ECHENEIS_STATE_DIR", "/app/state"))


def _active_path() -> Path:
    return _state_dir() / _ACTIVE_FILE


def _archive_path() -> Path:
    return _state_dir() / _ARCHIVE_FILE


def _summary_path() -> Path:
    return _state_dir() / _SUMMARY_FILE


# ── Recording ─────────────────────────────────────────────────────────


def record_usage(
    user_id: int,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    task: str = "",
    *,
    ts: float | None = None,
) -> None:
    """Append one usage entry. Swallows I/O errors after logging them.

    Never raises — the bot would rather lose a record than break a
    user-facing request because of analytics.
    """
    entry = {
        "ts": int(ts if ts is not None else time.time()),
        "user_id": user_id,
        "model": model or "",
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "task": task or "",
    }
    path = _active_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError as exc:
        logger.warning("Failed to record user usage: %s", exc)


def record_from_result(
    user_id: int,
    result: dict[str, Any],
    task: str = "",
) -> None:
    """Extract tokens + model from a gateway chat result and record.

    Safe against partial/malformed responses. Picks up the routed model
    name from ``_echeneis_model`` (added by the gateway) and falls back
    to the OpenAI-style ``model`` field.
    """
    if not isinstance(result, dict):
        return
    model = result.get("_echeneis_model") or result.get("model") or ""
    usage = result.get("usage") or {}
    record_usage(
        user_id,
        model,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
        task=task,
    )


# ── Reading / aggregation ────────────────────────────────────────────


def _iter_active() -> list[dict[str, Any]]:
    path = _active_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("Failed to read user_usage.jsonl: %s", exc)
    return entries


def _load_summary() -> dict[str, Any]:
    path = _summary_path()
    if not path.exists():
        return {"all_time_requests_by_user": {}, "all_time_requests_by_model": {}}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"all_time_requests_by_user": {}, "all_time_requests_by_model": {}}


def summary(now: float | None = None) -> dict[str, Any]:
    """Compute the full dashboard payload from active log + summary snapshot.

    Returns a dict shaped for direct rendering:

        {
            "users": {uid: {"1d": n, "7d": n, "all": n}, ...},
            "totals": {"1d": n, "7d": n, "all": n},
            "top_models_7d": [(model, count), ...],
        }
    """
    now = now if now is not None else time.time()
    cutoff_1d = now - 86400
    cutoff_7d = now - 7 * 86400

    entries = _iter_active()
    snapshot = _load_summary()
    baseline_user = snapshot.get("all_time_requests_by_user", {})
    baseline_model = snapshot.get("all_time_requests_by_model", {})

    by_user_1d: dict[int, int] = defaultdict(int)
    by_user_7d: dict[int, int] = defaultdict(int)
    by_user_active: dict[int, int] = defaultdict(int)
    by_model_7d: dict[str, int] = defaultdict(int)
    by_model_active: dict[str, int] = defaultdict(int)

    for e in entries:
        ts = e.get("ts", 0)
        try:
            uid = int(e.get("user_id", 0))
        except (TypeError, ValueError):
            continue
        model = e.get("model") or "unknown"
        by_user_active[uid] += 1
        by_model_active[model] += 1
        if ts >= cutoff_7d:
            by_user_7d[uid] += 1
            by_model_7d[model] += 1
        if ts >= cutoff_1d:
            by_user_1d[uid] += 1

    # All-time per user: archived baseline + entries still in active file.
    user_ids = set(by_user_active) | {int(k) for k in baseline_user}
    users: dict[int, dict[str, int]] = {}
    for uid in user_ids:
        all_time = int(baseline_user.get(str(uid), 0)) + by_user_active.get(uid, 0)
        users[uid] = {
            "1d": by_user_1d.get(uid, 0),
            "7d": by_user_7d.get(uid, 0),
            "all": all_time,
        }

    total_1d = sum(v["1d"] for v in users.values())
    total_7d = sum(v["7d"] for v in users.values())
    total_all = sum(v["all"] for v in users.values())

    # Top models 7d (active window).
    top_models_7d = sorted(by_model_7d.items(), key=lambda kv: kv[1], reverse=True)

    return {
        "users": users,
        "totals": {"1d": total_1d, "7d": total_7d, "all": total_all},
        "top_models_7d": top_models_7d,
        "baseline_model_all_time": baseline_model,
    }


def user_row(user_id: int, now: float | None = None) -> dict[str, int]:
    """Quick single-user lookup for guest /status."""
    data = summary(now=now)
    return data["users"].get(user_id, {"1d": 0, "7d": 0, "all": 0})


# ── Archive / compaction ─────────────────────────────────────────────


def archive_old_entries(
    cutoff_secs: int = _ARCHIVE_CUTOFF_SECS, *, now: float | None = None
) -> dict[str, int]:
    """Move records older than ``cutoff_secs`` to the gzip archive.

    Also updates ``user_usage_summary.json`` so the /status dashboard's
    all-time column doesn't have to scan the archive on every request.

    Returns a small report dict: ``{"archived": N, "kept": M}``.

    Intended to be invoked by a nightly cron. Safe to run manually.
    """
    now = now if now is not None else time.time()
    cutoff = now - cutoff_secs
    entries = _iter_active()
    if not entries:
        return {"archived": 0, "kept": 0}

    archived = [e for e in entries if e.get("ts", 0) < cutoff]
    kept = [e for e in entries if e.get("ts", 0) >= cutoff]

    if not archived:
        return {"archived": 0, "kept": len(kept)}

    # Append to gzip archive (accumulating across runs).
    try:
        _archive_path().parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(_archive_path(), "ab") as gf:
            for e in archived:
                gf.write((json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError as exc:
        logger.error("Archive write failed, keeping active file intact: %s", exc)
        return {"archived": 0, "kept": len(entries)}

    # Update the summary snapshot so /status all-time counts remain accurate.
    snap = _load_summary()
    by_user = snap.get("all_time_requests_by_user", {})
    by_model = snap.get("all_time_requests_by_model", {})
    for e in archived:
        uid_str = str(e.get("user_id", 0))
        by_user[uid_str] = by_user.get(uid_str, 0) + 1
        model = e.get("model") or "unknown"
        by_model[model] = by_model.get(model, 0) + 1
    snap["all_time_requests_by_user"] = by_user
    snap["all_time_requests_by_model"] = by_model
    snap["last_archive_ts"] = int(now)

    # Atomic rewrites for the summary and the slimmed active file.
    _atomic_write_json(_summary_path(), snap)
    _atomic_write_active(kept)

    logger.info(
        "Archived %d old usage entries, kept %d active", len(archived), len(kept)
    )
    return {"archived": len(archived), "kept": len(kept)}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".usage-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_active(entries: list[dict[str, Any]]) -> None:
    path = _active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".usage-", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
