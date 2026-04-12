"""Request usage tracking per model.

Tracks RPM (requests per minute) and RPD (requests per day)
using sliding time windows. RPD counts are persisted to disk
so they survive gateway restarts.
"""

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIR_ENV = "ECHENEIS_STATE_DIR"
_USAGE_FILE = "usage_rpd.jsonl"


@dataclass
class RateLimit:
    """Known rate limits for a model."""

    rpm: int = 0  # 0 means unlimited / unknown
    rpd: int = 0


@dataclass
class _ModelUsage:
    """Usage tracking state for a single model."""

    # RPM: monotonic timestamps, only needs in-memory (60s window)
    minute_window: deque[float] = field(default_factory=deque)

    # RPD: wall-clock timestamps (epoch), persisted to disk
    day_window: deque[float] = field(default_factory=deque)

    def record_rpm(self, now_mono: float) -> None:
        """Record a request for RPM tracking (in-memory only)."""
        self.minute_window.append(now_mono)

    def record_rpd(self, now_wall: float) -> None:
        """Record a request for RPD tracking (persisted)."""
        self.day_window.append(now_wall)

    def prune_rpm(self, now_mono: float) -> None:
        """Remove RPM entries older than 60 seconds."""
        cutoff = now_mono - 60.0
        while self.minute_window and self.minute_window[0] < cutoff:
            self.minute_window.popleft()

    def prune_rpd(self, now_wall: float) -> None:
        """Remove RPD entries older than 24 hours."""
        cutoff = now_wall - 86400.0
        while self.day_window and self.day_window[0] < cutoff:
            self.day_window.popleft()

    def get_counts(self, now_mono: float, now_wall: float) -> tuple[int, int]:
        """Return (requests_this_minute, requests_today) after pruning."""
        self.prune_rpm(now_mono)
        self.prune_rpd(now_wall)
        return len(self.minute_window), len(self.day_window)


class UsageTracker:
    """Tracks per-model request usage and computes remaining quota.

    RPD (requests per day) are persisted to a JSONL file in the state
    directory so they survive gateway restarts.
    """

    def __init__(self, state_dir: str | None = None) -> None:
        self._usage: dict[str, _ModelUsage] = {}
        self._limits: dict[str, RateLimit] = {}

        # Resolve persistence path
        base = state_dir or os.environ.get(_STATE_DIR_ENV, "")
        if base:
            self._usage_path: Path | None = Path(base) / _USAGE_FILE
        else:
            self._usage_path = None

        self._load_rpd()

    def _load_rpd(self) -> None:
        """Load today's RPD entries from disk."""
        if not self._usage_path or not self._usage_path.exists():
            return

        cutoff = time.time() - 86400.0
        loaded = 0
        stale = 0

        try:
            with open(self._usage_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry["t"]
                        model = entry["m"]
                        if ts < cutoff:
                            stale += 1
                            continue
                        if model not in self._usage:
                            self._usage[model] = _ModelUsage()
                        self._usage[model].record_rpd(ts)
                        loaded += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError as e:
            logger.warning("Failed to load RPD state: %s", e)
            return

        if loaded or stale:
            logger.info(
                "Loaded %d RPD entries (%d stale discarded) from %s",
                loaded,
                stale,
                self._usage_path,
            )

        # Compact: rewrite file without stale entries
        if stale > 0:
            self._compact()

    def _compact(self) -> None:
        """Rewrite the usage file, removing entries older than 24h."""
        if not self._usage_path:
            return
        try:
            with open(self._usage_path, "w") as f:
                for model, mu in self._usage.items():
                    for ts in mu.day_window:
                        f.write(json.dumps({"t": ts, "m": model}) + "\n")
        except OSError as e:
            logger.warning("Failed to compact RPD state: %s", e)

    def _persist_rpd(self, model_name: str, wall_ts: float) -> None:
        """Append a single RPD entry to disk."""
        if not self._usage_path:
            return
        try:
            self._usage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._usage_path, "a") as f:
                f.write(json.dumps({"t": wall_ts, "m": model_name}) + "\n")
        except OSError as e:
            logger.debug("Failed to persist RPD entry: %s", e)

    def set_limits(self, model_name: str, rpm: int = 0, rpd: int = 0) -> None:
        """Register known rate limits for a model.

        Args:
            model_name: Short model name.
            rpm: Requests per minute limit (0 = unlimited).
            rpd: Requests per day limit (0 = unlimited).
        """
        self._limits[model_name] = RateLimit(rpm=rpm, rpd=rpd)

    def record_request(self, model_name: str) -> None:
        """Record a successful request for a model."""
        if model_name not in self._usage:
            self._usage[model_name] = _ModelUsage()

        now_mono = time.monotonic()
        now_wall = time.time()

        self._usage[model_name].record_rpm(now_mono)
        self._usage[model_name].record_rpd(now_wall)
        self._persist_rpd(model_name, now_wall)

    def get_usage(self, model_name: str) -> dict[str, Any]:
        """Get usage and remaining quota for a model.

        Returns:
            Dict with used_rpm, used_rpd, limit_rpm, limit_rpd,
            remaining_rpm, remaining_rpd.
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        limits = self._limits.get(model_name, RateLimit())

        if model_name in self._usage:
            used_rpm, used_rpd = self._usage[model_name].get_counts(now_mono, now_wall)
        else:
            used_rpm, used_rpd = 0, 0

        return {
            "used_rpm": used_rpm,
            "used_rpd": used_rpd,
            "limit_rpm": limits.rpm,
            "limit_rpd": limits.rpd,
            "remaining_rpm": (max(0, limits.rpm - used_rpm) if limits.rpm else None),
            "remaining_rpd": (max(0, limits.rpd - used_rpd) if limits.rpd else None),
        }

    def get_all_usage(self) -> dict[str, dict[str, Any]]:
        """Get usage for all models that have limits registered."""
        return {name: self.get_usage(name) for name in self._limits}
