"""Provider health tracking and circuit breaker.

Monitors provider availability, tracks rate limit consumption,
and implements circuit breaker pattern for failover decisions.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum

from echeneis.gateway.config import FailoverConfig
from echeneis.gateway.notifier import send_telegram

logger = logging.getLogger(__name__)


def _fire_notification(text: str) -> None:
    """Schedule a fire-and-forget Telegram notification.

    Called from sync code inside an async runtime. Falls back to a no-op
    if no event loop is running (e.g. during unit tests).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(send_telegram(text))


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal — requests pass through
    OPEN = "open"  # Tripped — all requests fail fast
    HALF_OPEN = "half_open"  # Testing — one request allowed through


@dataclass
class ProviderState:
    """Health state for a single provider/model."""

    model_name: str
    consecutive_failures: int = 0
    circuit_state: CircuitState = CircuitState.CLOSED
    circuit_opened_at: float = 0.0
    last_failure_at: float = 0.0
    unhealthy_until: float = 0.0

    def is_available(self, now: float | None = None) -> bool:
        """Check if this provider is currently available for requests."""
        now = now or time.monotonic()

        if self.circuit_state == CircuitState.OPEN:
            return False

        if now < self.unhealthy_until:
            return False

        return True


class HealthTracker:
    """Tracks provider health and implements circuit breaker logic.

    Thread-safe for single-process asyncio usage (no locks needed
    since asyncio is cooperative).
    """

    def __init__(self, failover_config: FailoverConfig) -> None:
        self._config = failover_config
        self._states: dict[str, ProviderState] = {}

    def _get_state(self, model_name: str) -> ProviderState:
        """Get or create provider state."""
        if model_name not in self._states:
            self._states[model_name] = ProviderState(model_name=model_name)
        return self._states[model_name]

    def is_available(self, model_name: str) -> bool:
        """Check if a model/provider is available for requests."""
        state = self._get_state(model_name)
        now = time.monotonic()

        # Check if circuit breaker should transition from OPEN → HALF_OPEN
        if state.circuit_state == CircuitState.OPEN:
            elapsed = now - state.circuit_opened_at
            if (
                elapsed >= self._config.circuit_breaker_open_seconds
                and self._config.circuit_breaker_half_open
            ):
                logger.info(
                    "Circuit half-open for %s after %ds",
                    model_name,
                    elapsed,
                )
                state.circuit_state = CircuitState.HALF_OPEN
                return True
            return False

        return state.is_available(now)

    def record_success(self, model_name: str) -> None:
        """Record a successful request — reset failure counters."""
        state = self._get_state(model_name)
        recovered = state.circuit_state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
        if state.circuit_state == CircuitState.HALF_OPEN:
            logger.info("Circuit closed for %s after successful test", model_name)
        state.consecutive_failures = 0
        state.circuit_state = CircuitState.CLOSED
        state.unhealthy_until = 0.0
        if recovered:
            _fire_notification(f"✅ *{model_name}* 恢復正常 (circuit closed)")

    def record_failure(self, model_name: str, status_code: int) -> None:
        """Record a failed request and update circuit breaker state.

        Args:
            model_name: The model that failed.
            status_code: HTTP status code (429, 5xx, 0 for timeout).
        """
        state = self._get_state(model_name)
        now = time.monotonic()
        state.consecutive_failures += 1
        state.last_failure_at = now

        # Half-open test failed → back to open
        if state.circuit_state == CircuitState.HALF_OPEN:
            logger.warning(
                "Half-open test failed for %s, reopening circuit", model_name
            )
            state.circuit_state = CircuitState.OPEN
            state.circuit_opened_at = now
            return

        # 5xx → mark unhealthy for retry_after_seconds
        if 500 <= status_code < 600:
            state.unhealthy_until = now + self._config.retry_after_seconds
            logger.warning(
                "Marked %s unhealthy for %ds (HTTP %d)",
                model_name,
                self._config.retry_after_seconds,
                status_code,
            )

        # Check circuit breaker threshold
        if (
            state.circuit_state != CircuitState.OPEN
            and state.consecutive_failures >= self._config.circuit_breaker_failures
        ):
            state.circuit_state = CircuitState.OPEN
            state.circuit_opened_at = now
            logger.warning(
                "Circuit breaker OPEN for %s after %d consecutive failures",
                model_name,
                state.consecutive_failures,
            )
            _fire_notification(
                f"⚠️ *{model_name}* 斷路開啟 "
                f"(連續 {state.consecutive_failures} 次失敗, HTTP {status_code})"
            )

    def get_status(self) -> dict[str, dict[str, str | int | bool]]:
        """Get health status of all tracked providers."""
        return {
            name: {
                "circuit": state.circuit_state.value,
                "consecutive_failures": state.consecutive_failures,
                "available": self.is_available(name),
            }
            for name, state in self._states.items()
        }
