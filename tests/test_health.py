"""Tests for health tracker and circuit breaker."""

import time

from echeneis.gateway.config import FailoverConfig
from echeneis.gateway.health import CircuitState, HealthTracker


def _tracker(
    failures: int = 3, open_seconds: int = 300, retry_after: int = 60
) -> HealthTracker:
    return HealthTracker(
        FailoverConfig(
            circuit_breaker_failures=failures,
            circuit_breaker_open_seconds=open_seconds,
            circuit_breaker_half_open=True,
            retry_after_seconds=retry_after,
        )
    )


class TestBasicTracking:
    def test_new_provider_is_available(self) -> None:
        tracker = _tracker()
        assert tracker.is_available("model-a") is True

    def test_success_resets_failures(self) -> None:
        tracker = _tracker(failures=2)
        tracker.record_failure("model-a", 429)
        tracker.record_success("model-a")
        assert tracker.is_available("model-a") is True
        state = tracker._get_state("model-a")
        assert state.consecutive_failures == 0


class TestCircuitBreaker:
    def test_opens_after_consecutive_failures(self) -> None:
        tracker = _tracker(failures=3)
        for _ in range(3):
            tracker.record_failure("model-a", 429)
        assert tracker.is_available("model-a") is False
        assert tracker._get_state("model-a").circuit_state == CircuitState.OPEN

    def test_half_open_after_timeout(self) -> None:
        tracker = _tracker(failures=3, open_seconds=10)
        for _ in range(3):
            tracker.record_failure("model-a", 429)

        # Simulate time passing
        state = tracker._get_state("model-a")
        state.circuit_opened_at = time.monotonic() - 11

        assert tracker.is_available("model-a") is True
        assert state.circuit_state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self) -> None:
        tracker = _tracker(failures=3, open_seconds=10)
        for _ in range(3):
            tracker.record_failure("model-a", 429)

        state = tracker._get_state("model-a")
        state.circuit_opened_at = time.monotonic() - 11
        tracker.is_available("model-a")  # triggers HALF_OPEN

        tracker.record_success("model-a")
        assert state.circuit_state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self) -> None:
        tracker = _tracker(failures=3, open_seconds=10)
        for _ in range(3):
            tracker.record_failure("model-a", 429)

        state = tracker._get_state("model-a")
        state.circuit_opened_at = time.monotonic() - 11
        tracker.is_available("model-a")  # triggers HALF_OPEN

        tracker.record_failure("model-a", 500)
        assert state.circuit_state == CircuitState.OPEN


class TestUnhealthyMarking:
    def test_5xx_marks_unhealthy(self) -> None:
        tracker = _tracker(failures=5, retry_after=60)
        tracker.record_failure("model-a", 503)
        state = tracker._get_state("model-a")
        assert state.unhealthy_until > time.monotonic()
        assert tracker.is_available("model-a") is False

    def test_429_does_not_mark_unhealthy(self) -> None:
        tracker = _tracker(failures=5)
        tracker.record_failure("model-a", 429)
        # Still available after single 429 (circuit not tripped)
        assert tracker.is_available("model-a") is True


class TestStatusReport:
    def test_get_status(self) -> None:
        tracker = _tracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-b", 429)
        status = tracker.get_status()
        assert "model-a" in status
        assert "model-b" in status
        assert status["model-a"]["available"] is True
