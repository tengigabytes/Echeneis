"""Tests for per-user usage tracking, aggregation, and archive."""

from __future__ import annotations

import gzip
import json
import time

import pytest

from echeneis.bot import user_usage


@pytest.fixture(autouse=True)
def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    yield


def test_record_usage_appends_jsonl(tmp_path):
    user_usage.record_usage(
        100, "gemma-4-31b", tokens_in=10, tokens_out=20, task="chat"
    )
    user_usage.record_usage(200, "mistral-large-3", tokens_in=5, tokens_out=7)

    path = tmp_path / "user_usage.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["user_id"] == 100
    assert rec["model"] == "gemma-4-31b"
    assert rec["tokens_in"] == 10
    assert rec["task"] == "chat"


def test_record_from_result_extracts_fields(tmp_path):
    result = {
        "_echeneis_model": "gemma-4-31b",
        "usage": {"prompt_tokens": 15, "completion_tokens": 22},
        "choices": [{"message": {"content": "hi"}}],
    }
    user_usage.record_from_result(300, result, task="think")
    entries = user_usage._iter_active()
    assert len(entries) == 1
    assert entries[0]["model"] == "gemma-4-31b"
    assert entries[0]["tokens_in"] == 15
    assert entries[0]["tokens_out"] == 22
    assert entries[0]["task"] == "think"


def test_record_from_result_tolerates_partial():
    # Must not crash on non-dict, missing fields, etc.
    user_usage.record_from_result(1, None)  # type: ignore[arg-type]
    user_usage.record_from_result(1, {})
    user_usage.record_from_result(1, {"usage": {}})
    entries = user_usage._iter_active()
    # Last two got recorded (first — None — is rejected early).
    assert len(entries) == 2


def test_summary_window_buckets(tmp_path):
    now = time.time()
    # 3 recent, 2 mid, 1 ancient for user 100
    for offset in (0, 100, 200):
        user_usage.record_usage(100, "gemma-4-31b", ts=now - offset)
    for offset in (3 * 86400, 5 * 86400):  # within 7d, outside 1d
        user_usage.record_usage(100, "gemma-4-31b", ts=now - offset)
    user_usage.record_usage(100, "gemma-4-31b", ts=now - 40 * 86400)  # > 7d, < 90d

    # Different user
    user_usage.record_usage(200, "groq-llama-70b", ts=now)

    data = user_usage.summary(now=now)
    assert data["users"][100]["1d"] == 3
    assert data["users"][100]["7d"] == 5
    assert data["users"][100]["all"] == 6
    assert data["users"][200]["1d"] == 1
    assert data["totals"] == {"1d": 4, "7d": 6, "all": 7}


def test_summary_top_models_7d(tmp_path):
    now = time.time()
    for _ in range(5):
        user_usage.record_usage(100, "gemma-4-31b", ts=now)
    for _ in range(3):
        user_usage.record_usage(200, "mistral-large-3", ts=now)
    # Outside 7d — should not count in top
    user_usage.record_usage(100, "old-model", ts=now - 30 * 86400)

    data = user_usage.summary(now=now)
    top = data["top_models_7d"]
    assert top[0] == ("gemma-4-31b", 5)
    assert top[1] == ("mistral-large-3", 3)
    assert all(m != "old-model" for m, _ in top)


def test_user_row_returns_zero_for_unknown(tmp_path):
    row = user_usage.user_row(42)
    assert row == {"1d": 0, "7d": 0, "all": 0}


def test_archive_moves_old_records(tmp_path):
    now = time.time()
    # 3 recent + 4 ancient
    for offset in (0, 10, 20):
        user_usage.record_usage(100, "gemma-4-31b", ts=now - offset)
    for offset in (91 * 86400, 95 * 86400, 100 * 86400, 120 * 86400):
        user_usage.record_usage(200, "old-model", ts=now - offset)

    report = user_usage.archive_old_entries(now=now)
    assert report == {"archived": 4, "kept": 3}

    # Active file has only recent entries.
    remaining = user_usage._iter_active()
    assert len(remaining) == 3
    assert all(e["user_id"] == 100 for e in remaining)

    # Archive file contains 4 records.
    with gzip.open(tmp_path / "user_usage.archive.jsonl.gz", "rt") as gf:
        archived = [json.loads(line) for line in gf if line.strip()]
    assert len(archived) == 4

    # Summary snapshot updated with all-time counts for archived users.
    snap = json.loads((tmp_path / "user_usage_summary.json").read_text())
    assert snap["all_time_requests_by_user"]["200"] == 4
    assert snap["all_time_requests_by_model"]["old-model"] == 4


def test_summary_includes_archived_baseline(tmp_path):
    # Pre-populate the summary snapshot (simulating a prior archive).
    summary_file = tmp_path / "user_usage_summary.json"
    summary_file.write_text(
        json.dumps(
            {
                "all_time_requests_by_user": {"200": 500},
                "all_time_requests_by_model": {"archived-model": 500},
            }
        )
    )
    now = time.time()
    user_usage.record_usage(200, "gemma-4-31b", ts=now)

    data = user_usage.summary(now=now)
    # all-time = baseline (500) + active (1) = 501
    assert data["users"][200]["all"] == 501
    assert data["users"][200]["1d"] == 1


def test_archive_idempotent_on_empty(tmp_path):
    report = user_usage.archive_old_entries()
    assert report == {"archived": 0, "kept": 0}


def test_summary_handles_malformed_lines(tmp_path):
    path = tmp_path / "user_usage.jsonl"
    path.write_text(
        '{"ts":1,"user_id":1,"model":"m","tokens_in":0,"tokens_out":0,"task":""}\n'
        "not-json\n"
        '{"ts":2,"user_id":1,"model":"m","tokens_in":0,"tokens_out":0,"task":""}\n',
        encoding="utf-8",
    )
    data = user_usage.summary(now=time.time())
    # Both well-formed entries counted (1d=0 since timestamps are epoch 1/2).
    assert data["users"][1]["all"] == 2
