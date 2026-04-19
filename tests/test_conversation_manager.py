"""Tests for ConversationManager — per-chat isolation + archive + migration."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from echeneis.bot.conversation import (
    ConversationManager,
    ConversationStore,
)


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    yield


def test_for_chat_returns_same_instance(tmp_path):
    mgr = ConversationManager()
    a = mgr.for_chat(100)
    b = mgr.for_chat(100)
    assert a is b


def test_different_chats_get_isolated_stores(tmp_path):
    mgr = ConversationManager()
    a_store = mgr.for_chat(111)
    b_store = mgr.for_chat(222)
    assert a_store is not b_store

    # Crucial: same message_id in different chats must not collide.
    a_store.store_user(42, "alice question")
    b_store.store_user(42, "bob question")
    assert a_store._entries[42].content == "alice question"
    assert b_store._entries[42].content == "bob question"


def test_per_chat_files_written(tmp_path):
    mgr = ConversationManager()
    mgr.for_chat(111).store_user(1, "hello")
    mgr.for_chat(222).store_user(1, "hi")

    convo_dir = tmp_path / "conversations"
    assert (convo_dir / "111.jsonl").exists()
    assert (convo_dir / "222.jsonl").exists()


def test_chain_walk_stays_within_one_chat(tmp_path):
    """Chain walking never leaves the chat's own store."""
    mgr = ConversationManager()
    mgr.for_chat(111).store_user(10, "A1")
    mgr.for_chat(111).store_assistant(11, "B1", parent_id=10, model="m1")

    # Different chat reuses message_ids — must be independent.
    mgr.for_chat(222).store_user(10, "A2")
    mgr.for_chat(222).store_assistant(11, "B2", parent_id=10, model="m2")
    mgr.for_chat(222).store_user(12, "follow", parent_id=11)

    messages_222 = mgr.for_chat(222).build_messages(12)
    texts = [m["content"] for m in messages_222[1:]]
    assert texts == ["A2", "B2", "follow"]


def test_archive_all_iterates_chats_on_disk(tmp_path):
    mgr = ConversationManager()
    now = time.time()

    # Chat 111 has one old + one recent; 222 only old; 333 only recent.
    for chat_id, entries in {
        111: [(1, now - 95 * 86400, "old1"), (2, now - 1, "recent1")],
        222: [(3, now - 100 * 86400, "old2")],
        333: [(4, now - 2, "recent3")],
    }.items():
        store = mgr.for_chat(chat_id)
        for mid, ts, content in entries:
            store.store_user(mid, content)
            store._entries[mid].timestamp = ts

        # Rewrite the JSONL so on-disk timestamps match the mutated ones.
        path = tmp_path / "conversations" / f"{chat_id}.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "ts": ts,
                        "message_id": mid,
                        "role": "user",
                        "content": content,
                        "parent_id": None,
                    },
                    ensure_ascii=False,
                )
                for mid, ts, content in entries
            )
            + "\n",
            encoding="utf-8",
        )

    report = mgr.archive_all(now=now)
    assert report["chats"] == 3
    assert report["dropped"] == 2  # 95d + 100d old entries
    assert report["kept"] == 2


def test_archive_ignores_legacy_snapshot(tmp_path):
    """_legacy.jsonl must be left alone by archive_all."""
    # Seed a legacy file so migration produces a snapshot.
    (tmp_path / "conversations.jsonl").write_text(
        json.dumps(
            {
                "ts": time.time(),
                "message_id": 1,
                "role": "user",
                "content": "legacy",
                "parent_id": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    mgr = ConversationManager()
    legacy_snapshot = tmp_path / "conversations" / "_legacy.jsonl"
    assert legacy_snapshot.exists()

    # Archive only counts the snapshot-excluded chats.
    mgr.for_chat(111).store_user(1, "alive")
    report = mgr.archive_all()
    assert report["chats"] == 1
    assert legacy_snapshot.exists()  # untouched


def test_legacy_migration_moves_old_file(tmp_path):
    legacy = tmp_path / "conversations.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "message_id": 1,
                "role": "user",
                "content": "pre-split data",
                "parent_id": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = ConversationManager()
    # Old file is gone; _legacy snapshot exists.
    assert not legacy.exists()
    assert (tmp_path / "conversations" / "_legacy.jsonl").exists()


def test_legacy_migration_concatenates_on_reinit(tmp_path):
    """A second migration must not overwrite the first snapshot."""
    (tmp_path / "conversations.jsonl").write_text(
        json.dumps(
            {"ts": 1, "message_id": 1, "role": "user", "content": "first round"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = ConversationManager()

    # New legacy dropped again — simulating a stale path — must append,
    # not clobber the earlier snapshot.
    (tmp_path / "conversations.jsonl").write_text(
        json.dumps(
            {"ts": 2, "message_id": 2, "role": "user", "content": "second round"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _ = ConversationManager()

    snapshot = (tmp_path / "conversations" / "_legacy.jsonl").read_text(
        encoding="utf-8"
    )
    assert "first round" in snapshot
    assert "second round" in snapshot


def test_standalone_store_still_works(tmp_path):
    """ConversationStore remains usable directly (tests, one-off scripts)."""
    store = ConversationStore(log_path=str(tmp_path / "explicit.jsonl"))
    store.store_user(1, "hello")
    assert Path(tmp_path / "explicit.jsonl").exists()
