"""Request usage tracking per model.

Tracks RPM (requests per minute) and RPD (requests per day)
using sliding time windows. Compares against known rate limits
to report remaining quota.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RateLimit:
    """Known rate limits for a model."""

    rpm: int = 0  # 0 means unlimited / unknown
    rpd: int = 0


@dataclass
class _ModelUsage:
    """Usage tracking state for a single model."""

    # Timestamps of successful requests (monotonic seconds)
    minute_window: deque[float] = field(default_factory=deque)
    day_window: deque[float] = field(default_factory=deque)

    def record(self, now: float) -> None:
        """Record a successful request."""
        self.minute_window.append(now)
        self.day_window.append(now)

    def _prune(self, now: float) -> None:
        """Remove expired entries from windows."""
        minute_cutoff = now - 60.0
        day_cutoff = now - 86400.0
        while self.minute_window and self.minute_window[0] < minute_cutoff:
            self.minute_window.popleft()
        while self.day_window and self.day_window[0] < day_cutoff:
            self.day_window.popleft()

    def get_counts(self, now: float) -> tuple[int, int]:
        """Return (requests_this_minute, requests_today) after pruning."""
        self._prune(now)
        return len(self.minute_window), len(self.day_window)


class UsageTracker:
    """Tracks per-model request usage and computes remaining quota."""

    def __init__(self) -> None:
        self._usage: dict[str, _ModelUsage] = {}
        self._limits: dict[str, RateLimit] = {}

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
        self._usage[model_name].record(time.monotonic())

    def get_usage(self, model_name: str) -> dict[str, Any]:
        """Get usage and remaining quota for a model.

        Returns:
            Dict with used_rpm, used_rpd, limit_rpm, limit_rpd,
            remaining_rpm, remaining_rpd.
        """
        now = time.monotonic()
        limits = self._limits.get(model_name, RateLimit())

        if model_name in self._usage:
            used_rpm, used_rpd = self._usage[model_name].get_counts(now)
        else:
            used_rpm, used_rpd = 0, 0

        return {
            "used_rpm": used_rpm,
            "used_rpd": used_rpd,
            "limit_rpm": limits.rpm,
            "limit_rpd": limits.rpd,
            "remaining_rpm": max(0, limits.rpm - used_rpm) if limits.rpm else None,
            "remaining_rpd": max(0, limits.rpd - used_rpd) if limits.rpd else None,
        }

    def get_all_usage(self) -> dict[str, dict[str, Any]]:
        """Get usage for all models that have limits registered."""
        return {name: self.get_usage(name) for name in self._limits}
