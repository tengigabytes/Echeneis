"""Tests for audit log append/tail."""

from __future__ import annotations

from echeneis.bot.audit import log_admin_action, tail_audit


def test_audit_append_and_tail(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_admin_action(
        100, "adduser", target=200, detail="name='Alice'", audit_file=str(path)
    )
    log_admin_action(
        100, "ban", target=300, admin_username="terry", audit_file=str(path)
    )
    log_admin_action(100, "broadcast", detail="msg='hi'", audit_file=str(path))

    entries = tail_audit(10, audit_file=str(path))
    assert len(entries) == 3
    assert [e["action"] for e in entries] == ["adduser", "ban", "broadcast"]
    assert entries[0]["target"] == 200
    assert entries[1]["admin_username"] == "terry"


def test_audit_tail_respects_n(tmp_path):
    path = tmp_path / "audit.jsonl"
    for i in range(10):
        log_admin_action(100, f"action{i}", target=i, audit_file=str(path))

    entries = tail_audit(3, audit_file=str(path))
    assert len(entries) == 3
    assert [e["action"] for e in entries] == ["action7", "action8", "action9"]


def test_audit_missing_file_returns_empty(tmp_path):
    entries = tail_audit(10, audit_file=str(tmp_path / "nope.jsonl"))
    assert entries == []


def test_audit_skips_malformed_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_admin_action(100, "ok", target=1, audit_file=str(path))
    # Corrupt the file by appending a non-JSON line.
    with path.open("a", encoding="utf-8") as f:
        f.write("not-json\n")
    log_admin_action(100, "ok2", target=2, audit_file=str(path))

    entries = tail_audit(10, audit_file=str(path))
    # Two valid entries survive despite the garbage line in the middle.
    assert [e["action"] for e in entries] == ["ok", "ok2"]
