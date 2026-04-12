"""Tests for the bot task registry."""

import json
import time

import pytest

from echeneis.bot.task_registry import TaskRegistry


@pytest.fixture()
def registry(tmp_path):
    """Create a TaskRegistry writing to a temp directory."""
    state_file = str(tmp_path / "active_tasks.json")
    return TaskRegistry(state_file=state_file)


def test_register_and_unregister(registry):
    """Register a task, verify it's active, then unregister."""
    registry.register("bench", max_minutes=30, detail="test run")
    assert registry.is_busy()
    tasks = registry.active_tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "bench"

    registry.unregister("bench")
    assert not registry.is_busy()


def test_stale_task_ignored(registry):
    """A task past max_minutes should not count as active."""
    registry.register("bench", max_minutes=0)
    # max_minutes=0 means it's immediately stale
    assert not registry.is_busy()


def test_state_persists_to_disk(tmp_path):
    """State file is readable JSON with expected structure."""
    state_file = str(tmp_path / "active_tasks.json")
    reg = TaskRegistry(state_file=state_file)
    reg.register("bench", detail="latency only")

    with open(state_file) as f:
        data = json.load(f)

    assert "bench" in data["tasks"]
    assert data["tasks"]["bench"]["detail"] == "latency only"
    assert "started_at" in data["tasks"]["bench"]


def test_missing_state_file_not_busy(tmp_path):
    """Registry with no state file reports not busy."""
    reg = TaskRegistry(state_file=str(tmp_path / "nonexistent.json"))
    assert not reg.is_busy()


@pytest.mark.asyncio()
async def test_track_context_manager(registry):
    """The track() context manager registers and unregisters."""
    async with registry.track("bench", max_minutes=30):
        assert registry.is_busy()
    assert not registry.is_busy()


@pytest.mark.asyncio()
async def test_track_unregisters_on_error(registry):
    """track() unregisters even if the body raises."""
    with pytest.raises(RuntimeError):
        async with registry.track("bench"):
            raise RuntimeError("boom")
    assert not registry.is_busy()
