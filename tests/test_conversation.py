"""Tests for the conversation store."""

import json
import time

import pytest

from echeneis.bot.conversation import (
    TELEGRAM_SYSTEM_PROMPT,
    ConversationStore,
)


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path, monkeypatch):
    """Route each test's persistence to a throwaway tmp directory."""
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    yield


class TestConversationStore:
    """Tests for ConversationStore chain building."""

    def test_new_message_has_system_prompt_and_user(self):
        """A fresh message (no reply) produces [system, user]."""
        store = ConversationStore()
        store.store_user(10, "hello")
        messages = store.build_messages(10)

        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": TELEGRAM_SYSTEM_PROMPT}
        assert messages[1] == {"role": "user", "content": "hello"}

    def test_reply_chain_builds_history(self):
        """Replying to a bot message includes the full chain."""
        store = ConversationStore()

        # Turn 1: user sends, bot replies
        store.store_user(10, "what is LoRa")
        store.store_assistant(11, "LoRa is a modulation scheme.", parent_id=10)

        # Turn 2: user replies to bot
        store.store_user(12, "what about SF", parent_id=11)
        messages = store.build_messages(12)

        assert len(messages) == 4  # system + user + assistant + user
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "what is LoRa"}
        assert messages[2] == {
            "role": "assistant",
            "content": "LoRa is a modulation scheme.",
        }
        assert messages[3] == {"role": "user", "content": "what about SF"}

    def test_three_turn_chain(self):
        """Three turns of conversation are preserved."""
        store = ConversationStore()

        store.store_user(10, "Q1")
        store.store_assistant(11, "A1", parent_id=10)
        store.store_user(12, "Q2", parent_id=11)
        store.store_assistant(13, "A2", parent_id=12)
        store.store_user(14, "Q3", parent_id=13)

        messages = store.build_messages(14)
        # system + 3 user + 2 assistant = 6
        assert len(messages) == 6
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user", "assistant", "user"]

    def test_sliding_window_truncates(self):
        """History beyond max_turns is dropped."""
        store = ConversationStore(max_turns=2)

        # Build 4 turns
        store.store_user(10, "Q1")
        store.store_assistant(11, "A1", parent_id=10)
        store.store_user(12, "Q2", parent_id=11)
        store.store_assistant(13, "A2", parent_id=12)
        store.store_user(14, "Q3", parent_id=13)
        store.store_assistant(15, "A3", parent_id=14)
        store.store_user(16, "Q4", parent_id=15)

        messages = store.build_messages(16)
        # system + last 2 turns (4 messages) = 5
        assert len(messages) == 5
        # Oldest kept should be A3 (assistant from turn 3), then Q4
        assert messages[1]["content"] == "A2"
        assert messages[2]["content"] == "Q3"
        assert messages[3]["content"] == "A3"
        assert messages[4]["content"] == "Q4"

    def test_new_message_no_history_leak(self):
        """A direct message (no reply) does not carry prior history."""
        store = ConversationStore()

        store.store_user(10, "old question")
        store.store_assistant(11, "old answer", parent_id=10)

        # New message — not a reply
        store.store_user(20, "new question")
        messages = store.build_messages(20)

        assert len(messages) == 2  # system + user only
        assert messages[1]["content"] == "new question"

    def test_vision_content_preserved(self):
        """Vision content (list format) is stored and retrieved correctly."""
        store = ConversationStore()

        vision_content = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
        ]
        store.store_user(10, vision_content)
        messages = store.build_messages(10)

        assert messages[1]["content"] == vision_content

    def test_get_chain_model_returns_last_model(self):
        """get_chain_model returns the model from the most recent assistant reply."""
        store = ConversationStore()

        store.store_user(10, "hello")
        store.store_assistant(11, "hi", parent_id=10, model="gemma-4-31b")
        store.store_user(12, "follow up", parent_id=11)

        assert store.get_chain_model(12) == "gemma-4-31b"

    def test_get_chain_model_none_for_new_conversation(self):
        """get_chain_model returns None for a fresh message (no reply chain)."""
        store = ConversationStore()

        store.store_user(10, "new question")
        assert store.get_chain_model(10) is None

    def test_get_chain_model_none_when_no_model_stored(self):
        """get_chain_model returns None if assistant reply has no model."""
        store = ConversationStore()

        store.store_user(10, "hello")
        store.store_assistant(11, "hi", parent_id=10)  # no model
        store.store_user(12, "follow up", parent_id=11)

        assert store.get_chain_model(12) is None

    def test_persistence_survives_reinstantiation(self, tmp_path):
        """Restarting the bot must not drop conversation history."""
        store1 = ConversationStore()
        store1.store_user(10, "hi")
        store1.store_assistant(11, "hey", parent_id=10, model="gemma-4-31b")
        store1.store_user(12, "more", parent_id=11)

        # Simulate bot restart by creating a fresh store — it should
        # replay the JSONL on disk and produce identical chain history.
        store2 = ConversationStore()
        messages = store2.build_messages(12)
        assert len(messages) == 4
        assert messages[1]["content"] == "hi"
        assert messages[2]["content"] == "hey"
        assert messages[3]["content"] == "more"
        assert store2.get_chain_model(12) == "gemma-4-31b"

    def test_persistence_file_is_append_only_jsonl(self, tmp_path):
        """Each write appends one line to conversations.jsonl."""
        store = ConversationStore()
        store.store_user(10, "one")
        store.store_assistant(11, "two", parent_id=10)

        path = tmp_path / "conversations" / "default.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["role"] == "user"
        assert first["message_id"] == 10

    def test_archive_drops_old_entries_fifo(self, tmp_path):
        """archive_old_entries drops records beyond the retention window."""
        store = ConversationStore()
        now = time.time()

        # 91 days old — should be dropped
        store.store_user(10, "ancient")
        store._entries[10].timestamp = now - 91 * 86400

        # recent — should survive
        store.store_user(20, "recent")

        # Rewrite the underlying JSONL to reflect the mutated timestamps.
        path = tmp_path / "conversations" / "default.jsonl"
        path.write_text(
            json.dumps(
                {
                    "ts": now - 91 * 86400,
                    "message_id": 10,
                    "role": "user",
                    "content": "ancient",
                    "parent_id": None,
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "ts": now,
                    "message_id": 20,
                    "role": "user",
                    "content": "recent",
                    "parent_id": None,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        report = store.archive_old_entries(now=now)
        assert report == {"dropped": 1, "kept": 1}

        # In-memory state and on-disk file both trimmed.
        assert 10 not in store._entries
        assert 20 in store._entries
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["message_id"] == 20

    def test_archive_noop_when_all_recent(self):
        store = ConversationStore()
        store.store_user(10, "fresh")
        report = store.archive_old_entries()
        assert report["dropped"] == 0
        assert 10 in store._entries

    def test_has_reflects_store_state(self):
        store = ConversationStore()
        assert not store.has(42)
        store.store_user(42, "hello")
        assert store.has(42)
