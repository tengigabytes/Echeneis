"""Multi-turn conversation store for Telegram chats.

Tracks message chains via Telegram's reply-to mechanism. Replying to a
bot message continues the conversation; sending a new (non-reply)
message starts a fresh one.

Persistence: every write append-logs to ``state/conversations.jsonl``.
On startup the whole file is replayed into memory, so a bot rebuild or
restart doesn't lose the last 90 days of chat context. A nightly cron
(``deploy/archive-conversations.sh``) compacts the file by dropping
entries older than 90 days (FIFO).

When a user replies to a bot message the store doesn't know about
(entry expired, or bot was down when the original was sent), handlers
can inject the reply's text as a synthetic assistant entry so the
chain isn't silently broken.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Telegram chat system prompt — keeps Gemma (and other models) concise.
TELEGRAM_SYSTEM_PROMPT = (
    "You are a concise assistant in a Telegram chat. "
    "Reply in the same language the user writes in. "
    "Be direct — no filler, no preamble, no recap. "
    "If the conversation has prior messages, focus on answering the latest one. "
    "Earlier messages are context only — do not repeat or summarize them. "
    "Use short paragraphs. Prefer bullet points over walls of text. "
    "Only elaborate when the user explicitly asks for detail."
)

# Default sliding window: max number of *turns* (one turn = user + assistant).
DEFAULT_MAX_TURNS = 6

# Retention window for the on-disk log. The nightly archive script drops
# entries older than this. Matches the /status usage dashboard window.
RETENTION_SECS = 90 * 86400


@dataclass
class _Entry:
    """A single message in a conversation chain."""

    role: str  # "user" or "assistant"
    content: str | list[dict[str, Any]]  # text or vision content
    parent_id: int | None  # message_id of the previous bot reply
    timestamp: float = field(default_factory=time.time)
    model: str | None = None  # short model name (assistant entries only)


def _default_log_path() -> Path:
    state_dir = os.environ.get("ECHENEIS_STATE_DIR", "/app/state")
    return Path(state_dir) / "conversations.jsonl"


# Every _format_reply output starts with "[<model>\u00a0·\u00a0..." so the
# model name is the first token before any middle dot or closing bracket.
# Used by the reply-metadata fallback to recover the model a previous
# turn used, enabling session sticky across store gaps and restarts.
_HEADER_MODEL_RE = re.compile(r"^\[([^\s·\]]+)")


def extract_model_from_reply(text: str) -> str | None:
    """Parse the model name from a bot reply's first-line header.

    Returns None if the text doesn't start with a bracketed header —
    for example when the message came from a different source, was
    edited, or is from a plain-text fallback path.
    """
    if not isinstance(text, str) or not text:
        return None
    m = _HEADER_MODEL_RE.match(text.strip())
    return m.group(1) if m else None


class ConversationStore:
    """Stores conversation chains keyed by Telegram message_id.

    In-memory dict mirrors a persistent JSONL. Each bot reply stores a
    pointer to the user message that triggered it, forming a linked
    list. To build history for a new user message that replies to a bot
    message, walk the chain backwards and collect up to ``max_turns``
    turns.
    """

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        log_path: str | Path | None = None,
    ) -> None:
        self._entries: dict[int, _Entry] = {}
        self._max_turns = max_turns
        self._log_path = Path(log_path) if log_path else _default_log_path()
        self._io_lock = threading.Lock()
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Replay the JSONL log into memory. Silently skips malformed lines."""
        if not self._log_path.exists():
            return
        count = 0
        try:
            with self._log_path.open(encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    try:
                        mid = int(rec["message_id"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    self._entries[mid] = _Entry(
                        role=rec.get("role", "user"),
                        content=rec.get("content", ""),
                        parent_id=rec.get("parent_id"),
                        timestamp=float(rec.get("ts", time.time())),
                        model=rec.get("model"),
                    )
                    count += 1
        except OSError as exc:
            logger.warning("Failed to load conversations.jsonl: %s", exc)
        if count:
            logger.info("Loaded %d conversation entries from disk", count)

    def _append(self, message_id: int, entry: _Entry) -> None:
        """Atomically append one entry to the JSONL log. Never raises."""
        rec = {
            "ts": entry.timestamp,
            "message_id": message_id,
            "role": entry.role,
            "content": entry.content,
            "parent_id": entry.parent_id,
        }
        if entry.model is not None:
            rec["model"] = entry.model
        try:
            line = json.dumps(rec, ensure_ascii=False) + "\n"
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._io_lock:
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except (OSError, TypeError) as exc:
            # TypeError covers non-serialisable content (e.g. bytes in
            # vision payloads, test-double mocks leaking through).
            # Keep the in-memory entry; the disk copy just gets skipped.
            logger.warning("Failed to persist conversation entry: %s", exc)

    # ── Queries / writes ─────────────────────────────────────────────

    def has(self, message_id: int) -> bool:
        return message_id in self._entries

    def store_user(
        self,
        message_id: int,
        content: str | list[dict[str, Any]],
        parent_id: int | None = None,
    ) -> None:
        entry = _Entry(role="user", content=content, parent_id=parent_id)
        self._entries[message_id] = entry
        self._append(message_id, entry)

    def store_assistant(
        self,
        message_id: int,
        content: str,
        parent_id: int,
        model: str | None = None,
    ) -> None:
        entry = _Entry(
            role="assistant", content=content, parent_id=parent_id, model=model
        )
        self._entries[message_id] = entry
        self._append(message_id, entry)

    def build_messages(
        self,
        user_message_id: int,
    ) -> list[dict[str, Any]]:
        """Build the full message list for an LLM call.

        Walks the reply chain backwards from ``user_message_id``,
        collects up to ``max_turns`` turns, prepends the system prompt.
        """
        chain: list[dict[str, Any]] = []
        current_id: int | None = user_message_id
        while current_id is not None and current_id in self._entries:
            entry = self._entries[current_id]
            chain.append({"role": entry.role, "content": entry.content})
            current_id = entry.parent_id
        chain.reverse()

        # Sliding window: keep at most max_turns turns (2 messages each).
        max_messages = self._max_turns * 2
        if len(chain) > max_messages:
            chain = chain[-max_messages:]

        return [{"role": "system", "content": TELEGRAM_SYSTEM_PROMPT}] + chain

    def get_chain_model(self, user_message_id: int) -> str | None:
        """Most recent assistant reply's model in the chain, or None."""
        entry = self._entries.get(user_message_id)
        if entry is None:
            return None
        current_id = entry.parent_id
        while current_id is not None and current_id in self._entries:
            parent = self._entries[current_id]
            if parent.role == "assistant" and parent.model:
                return parent.model
            current_id = parent.parent_id
        return None

    # ── Disk compaction ──────────────────────────────────────────────

    def archive_old_entries(
        self, retention_secs: int = RETENTION_SECS, *, now: float | None = None
    ) -> dict[str, int]:
        """FIFO drop entries older than ``retention_secs`` from disk.

        Rewrites the JSONL keeping only recent entries, and rebuilds the
        in-memory dict from the surviving set. Returns a small report:
        ``{"dropped": N, "kept": M}``. Safe to run while the bot is
        live — the write is atomic (tmp + rename under a lock).

        Intended for nightly cron.
        """
        now = now if now is not None else time.time()
        cutoff = now - retention_secs
        path = self._log_path
        if not path.exists():
            return {"dropped": 0, "kept": 0}

        kept: list[tuple[int, dict[str, Any]]] = []
        dropped = 0
        try:
            with path.open(encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts = float(rec.get("ts", 0))
                    if ts < cutoff:
                        dropped += 1
                        continue
                    try:
                        mid = int(rec["message_id"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    kept.append((mid, rec))
        except OSError as exc:
            logger.error("archive_old_entries: read failed: %s", exc)
            return {"dropped": 0, "kept": len(self._entries)}

        if dropped == 0:
            return {"dropped": 0, "kept": len(kept)}

        # Atomic rewrite under lock so concurrent appends don't interleave.
        fd, tmp = tempfile.mkstemp(
            prefix=".convo-", suffix=".jsonl", dir=str(path.parent)
        )
        try:
            with self._io_lock:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for _, rec in kept:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                os.replace(tmp, path)
                # Rebuild in-memory dict from the surviving set.
                self._entries.clear()
                for mid, rec in kept:
                    self._entries[mid] = _Entry(
                        role=rec.get("role", "user"),
                        content=rec.get("content", ""),
                        parent_id=rec.get("parent_id"),
                        timestamp=float(rec.get("ts", time.time())),
                        model=rec.get("model"),
                    )
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        logger.info(
            "Conversation archive: dropped %d, kept %d (retention=%ds)",
            dropped,
            len(kept),
            retention_secs,
        )
        return {"dropped": dropped, "kept": len(kept)}
