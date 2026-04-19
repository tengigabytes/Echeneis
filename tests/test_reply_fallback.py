"""Tests for the reply-metadata fallback that reconstructs a missing parent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from echeneis.bot.conversation import ConversationStore
from echeneis.bot.handlers.messages import _ensure_reply_parent_recovered


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    yield


def _reply_message(reply_id: int | None, reply_text: str | None = None) -> MagicMock:
    msg = MagicMock()
    if reply_id is None:
        msg.reply_to_message = None
    else:
        reply = MagicMock()
        reply.message_id = reply_id
        reply.text = reply_text
        reply.caption = None
        msg.reply_to_message = reply
    return msg


def test_noop_when_not_a_reply():
    store = ConversationStore()
    _ensure_reply_parent_recovered(store, _reply_message(None))
    assert len(store._entries) == 0


def test_noop_when_parent_already_known():
    store = ConversationStore()
    store.store_assistant(42, "original text", parent_id=41, model="gemma-4-31b")
    before = dict(store._entries)

    _ensure_reply_parent_recovered(store, _reply_message(42, "different text"))

    # Store is unchanged — we trust the in-memory record over the Telegram
    # metadata copy (which could be stale if the message was edited).
    assert store._entries == before


def test_reconstructs_missing_parent_from_reply_text():
    """The core recovery case: parent dropped from archive / lost to restart."""
    store = ConversationStore()

    _ensure_reply_parent_recovered(store, _reply_message(42, "I said this earlier"))

    assert store.has(42)
    entry = store._entries[42]
    assert entry.role == "assistant"
    assert entry.content == "I said this earlier"
    # parent_id=0 is the sentinel — walks backward from here stop.
    assert entry.parent_id == 0


def test_skips_reply_with_no_text_or_caption():
    """If Telegram didn't deliver the parent's text (e.g. media-only), skip."""
    store = ConversationStore()
    msg = _reply_message(42)
    msg.reply_to_message.text = None
    msg.reply_to_message.caption = None

    _ensure_reply_parent_recovered(store, msg)
    assert not store.has(42)


def test_build_messages_sees_recovered_parent():
    """After recovery, build_messages picks up the synthesised assistant turn."""
    store = ConversationStore()

    _ensure_reply_parent_recovered(store, _reply_message(42, "old answer the bot gave"))

    # Simulate the user's new reply being stored and then asked about.
    store.store_user(100, "follow-up question", parent_id=42)
    messages = store.build_messages(100)

    roles = [m["role"] for m in messages]
    assert roles == ["system", "assistant", "user"]
    assert messages[1]["content"] == "old answer the bot gave"
    assert messages[2]["content"] == "follow-up question"
