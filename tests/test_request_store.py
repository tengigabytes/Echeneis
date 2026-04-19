"""Tests for RequestStore persistence and state machine."""

from __future__ import annotations

import pytest

from echeneis.bot.request_store import (
    DENY_COOLDOWN_SECS,
    RequestStore,
    Status,
    get_request_store,
    reset_request_store,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_request_store()
    yield
    reset_request_store()


def _new_store(tmp_path) -> RequestStore:
    return RequestStore(requests_file=str(tmp_path / "pending.json"))


def test_create_new_request(tmp_path):
    store = _new_store(tmp_path)
    rec = store.create(300, "alice", "Alice", "testing")
    assert rec is not None
    assert rec["user_id"] == 300
    assert rec["status"] == Status.PENDING.value
    assert rec["reason"] == "testing"


def test_duplicate_pending_rejected(tmp_path):
    store = _new_store(tmp_path)
    store.create(300, "alice", "Alice", "")
    assert store.create(300, "alice", "Alice", "") is None
    allowed, msg = store.can_submit(300)
    assert not allowed
    assert "處理中" in msg


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "pending.json"
    a = RequestStore(requests_file=str(path))
    a.create(300, "alice", "Alice", "reason")

    # Reload from disk.
    b = RequestStore(requests_file=str(path))
    rec = b.get(300)
    assert rec is not None
    assert rec["status"] == Status.PENDING.value


def test_approve_and_then_blocked(tmp_path):
    store = _new_store(tmp_path)
    store.create(300, "alice", "Alice", "")
    decided = store.approve(300, admin_id=100)
    assert decided is not None
    assert decided["decided_by"] == 100
    assert decided["status"] == Status.APPROVED.value
    allowed, msg = store.can_submit(300)
    assert not allowed
    assert "註冊" in msg


def test_deny_enforces_cooldown(tmp_path, monkeypatch):
    store = _new_store(tmp_path)
    store.create(300, "alice", "Alice", "")
    store.deny(300, admin_id=100)

    allowed, msg = store.can_submit(300)
    assert not allowed
    assert "小時" in msg

    # Fake the decided_at to be past the cooldown.
    from datetime import datetime, timedelta, timezone

    stale = datetime.now(timezone.utc) - timedelta(seconds=DENY_COOLDOWN_SECS + 1)
    # Mutate via internal dict (test-only shortcut).
    store._data[300]["decided_at"] = stale.isoformat()
    allowed2, _ = store.can_submit(300)
    assert allowed2


def test_double_decide_returns_none(tmp_path):
    store = _new_store(tmp_path)
    store.create(300, "alice", "Alice", "")
    assert store.approve(300, 100) is not None
    # Second admin tries to deny -> no-op.
    assert store.deny(300, 200) is None


def test_list_pending_is_ordered(tmp_path):
    store = _new_store(tmp_path)
    for uid in (301, 302, 303):
        store.create(uid, f"user{uid}", "N", "")
    pending = store.list_pending()
    assert [r["user_id"] for r in pending] == [301, 302, 303]


def test_set_notified_persists(tmp_path):
    store = _new_store(tmp_path)
    store.create(300, "alice", "Alice", "")
    store.set_notified(300, admin_id=100, message_id=555)
    store.set_notified(300, admin_id=101, message_id=666)
    rec = store.get(300)
    assert rec["notified_admins"] == {"100": 555, "101": 666}


def test_malformed_json_recovers(tmp_path):
    path = tmp_path / "pending.json"
    path.write_text("not-json")
    store = RequestStore(requests_file=str(path))
    assert store.list_pending() == []


def test_reason_truncated_to_300(tmp_path):
    store = _new_store(tmp_path)
    rec = store.create(300, "alice", "Alice", "x" * 500)
    assert rec is not None
    assert len(rec["reason"]) == 300


def test_singleton():
    reset_request_store()
    a = get_request_store()
    b = get_request_store()
    assert a is b
