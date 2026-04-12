"""General-purpose task registry for long-running bot operations.

Tracks active tasks in a JSON file on a shared volume so that external
scripts (e.g. auto-update.sh) can check whether the bot is busy before
restarting the service.

Each task carries a ``max_minutes`` guard — if the bot crashes mid-task
the entry becomes stale after that duration and external scripts will
treat it as inactive.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_STATE_DIR = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
_STATE_FILE = os.path.join(_STATE_DIR, "active_tasks.json")


@dataclass
class TaskEntry:
    """A single active task record."""

    name: str
    started_at: float  # time.time() epoch
    max_minutes: int = 30
    detail: str = ""

    def is_stale(self) -> bool:
        """Return True if the task exceeded its max duration."""
        elapsed_min = (time.time() - self.started_at) / 60
        return elapsed_min > self.max_minutes


@dataclass
class _State:
    """Serializable state written to disk."""

    tasks: dict[str, TaskEntry] = field(default_factory=dict)


class TaskRegistry:
    """Manages active task state on disk.

    All public methods are synchronous — the file is tiny and writes
    are infrequent, so blocking I/O is fine here.
    """

    def __init__(self, state_file: str | None = None) -> None:
        self._path = state_file or _STATE_FILE

    def _read(self) -> _State:
        """Load state from disk, returning empty state on any error."""
        try:
            with open(self._path) as f:
                data = json.load(f)
            tasks = {}
            for name, entry in data.get("tasks", {}).items():
                tasks[name] = TaskEntry(**entry)
            return _State(tasks=tasks)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return _State()

    def _write(self, state: _State) -> None:
        """Persist state to disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        payload = {"tasks": {k: asdict(v) for k, v in state.tasks.items()}}
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self._path)

    def register(self, name: str, max_minutes: int = 30, detail: str = "") -> None:
        """Register an active task.

        Args:
            name: Unique task identifier (e.g. "benchmark").
            max_minutes: Maximum expected duration. After this the entry
                is considered stale by external readers.
            detail: Optional human-readable description.
        """
        state = self._read()
        state.tasks[name] = TaskEntry(
            name=name,
            started_at=time.time(),
            max_minutes=max_minutes,
            detail=detail,
        )
        self._write(state)
        logger.info("Task registered: %s (max %d min)", name, max_minutes)

    def unregister(self, name: str) -> None:
        """Remove a task from the registry.

        Args:
            name: Task identifier to remove.
        """
        state = self._read()
        if name in state.tasks:
            del state.tasks[name]
            self._write(state)
            logger.info("Task unregistered: %s", name)

    def active_tasks(self) -> list[TaskEntry]:
        """Return all non-stale active tasks."""
        state = self._read()
        return [t for t in state.tasks.values() if not t.is_stale()]

    def is_busy(self) -> bool:
        """Return True if any non-stale task is active."""
        return len(self.active_tasks()) > 0

    @asynccontextmanager
    async def track(
        self, name: str, max_minutes: int = 30, detail: str = ""
    ) -> AsyncIterator[None]:
        """Async context manager that registers/unregisters a task.

        Args:
            name: Task identifier.
            max_minutes: Maximum expected duration.
            detail: Optional description.
        """
        self.register(name, max_minutes=max_minutes, detail=detail)
        try:
            yield
        finally:
            self.unregister(name)
