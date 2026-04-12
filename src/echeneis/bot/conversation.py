"""In-memory conversation store for multi-turn Telegram chats.

Tracks message chains via Telegram's reply-to mechanism.
Replying to a bot message continues the conversation;
sending a new message starts a fresh one.
"""

import logging
import time
from dataclasses import dataclass, field
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

# Evict entries older than this (seconds). Guards against unbounded growth
# in long-running bot processes.
_EVICT_AGE = 3600  # 1 hour
_EVICT_INTERVAL = 300  # run eviction at most every 5 minutes


@dataclass
class _Entry:
    """A single message in a conversation chain."""

    role: str  # "user" or "assistant"
    content: str | list[dict[str, Any]]  # text or vision content
    parent_id: int | None  # message_id of the previous bot reply
    timestamp: float = field(default_factory=time.time)
    model: str | None = None  # short model name (assistant entries only)


class ConversationStore:
    """Stores conversation chains keyed by Telegram message_id.

    Each bot reply is stored with a pointer to the user message that
    triggered it, forming a linked list. To build history for a new
    user message that replies to a bot message, we walk the chain
    backwards and collect up to ``max_turns`` turns.
    """

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self._entries: dict[int, _Entry] = {}
        self._max_turns = max_turns
        self._last_evict: float = 0.0

    def store_user(
        self,
        message_id: int,
        content: str | list[dict[str, Any]],
        parent_id: int | None = None,
    ) -> None:
        """Record a user message.

        Args:
            message_id: Telegram message_id of the user's message.
            content: Text or vision content list.
            parent_id: message_id of the bot reply being replied to,
                       or None for a new conversation.
        """
        self._entries[message_id] = _Entry(
            role="user", content=content, parent_id=parent_id
        )
        self._maybe_evict()

    def store_assistant(
        self,
        message_id: int,
        content: str,
        parent_id: int,
        model: str | None = None,
    ) -> None:
        """Record a bot (assistant) reply.

        Args:
            message_id: Telegram message_id of the bot's reply.
            content: The assistant's response text.
            parent_id: message_id of the user message this replies to.
            model: Short model name that generated this reply.
        """
        self._entries[message_id] = _Entry(
            role="assistant", content=content, parent_id=parent_id, model=model
        )

    def build_messages(
        self,
        user_message_id: int,
    ) -> list[dict[str, Any]]:
        """Build the full message list for an LLM call.

        Walks the reply chain backwards from ``user_message_id``,
        collects up to ``max_turns`` turns, prepends the system prompt.

        Returns:
            OpenAI-format messages list: [system, ...history, latest_user].
        """
        chain: list[dict[str, Any]] = []
        current_id: int | None = user_message_id

        # Walk backwards collecting entries
        while current_id is not None and current_id in self._entries:
            entry = self._entries[current_id]
            chain.append({"role": entry.role, "content": entry.content})
            current_id = entry.parent_id

        # chain is newest-first; reverse to chronological
        chain.reverse()

        # Apply sliding window: keep at most max_turns turns (2 messages each)
        max_messages = self._max_turns * 2
        if len(chain) > max_messages:
            chain = chain[-max_messages:]

        # Prepend system prompt
        return [{"role": "system", "content": TELEGRAM_SYSTEM_PROMPT}] + chain

    def get_chain_model(self, user_message_id: int) -> str | None:
        """Find the model used in the most recent assistant reply in the chain.

        Walks backwards from the user message's parent (the bot reply
        being replied to) looking for the first assistant entry with a
        stored model name.

        Args:
            user_message_id: The current user message's Telegram message_id.

        Returns:
            Short model name, or None if this is a new conversation or
            no model was recorded.
        """
        entry = self._entries.get(user_message_id)
        if entry is None:
            return None

        # Walk from the parent (the bot message being replied to)
        current_id = entry.parent_id
        while current_id is not None and current_id in self._entries:
            parent = self._entries[current_id]
            if parent.role == "assistant" and parent.model:
                return parent.model
            current_id = parent.parent_id
        return None

    def _maybe_evict(self) -> None:
        """Remove entries older than _EVICT_AGE, at most once per _EVICT_INTERVAL."""
        now = time.time()
        if now - self._last_evict < _EVICT_INTERVAL:
            return
        self._last_evict = now
        cutoff = now - _EVICT_AGE
        stale = [mid for mid, e in self._entries.items() if e.timestamp < cutoff]
        if stale:
            for mid in stale:
                del self._entries[mid]
            logger.info("Evicted %d stale conversation entries", len(stale))
