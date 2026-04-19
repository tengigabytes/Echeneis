"""Tests for admin ops handlers (/broadcast, /logs, /health, /reload)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from echeneis.bot.audit import tail_audit
from echeneis.bot.handlers.ops import (
    broadcast_command,
    health_command,
    logs_command,
    reload_command,
)
from echeneis.bot.request_store import reset_request_store
from echeneis.bot.user_store import get_store, reset_store

ADMIN_ID = 100
GUEST1_ID = 201
GUEST2_ID = 202
STRANGER_ID = 999


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHENEIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", str(ADMIN_ID))
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    reset_store()
    reset_request_store()
    yield
    reset_store()
    reset_request_store()


def _mk_update(user_id: int, args: list[str] | None = None):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "terry"
    update.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot_data = {}
    return update, ctx


# ── /broadcast ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_non_sender(tmp_path):
    get_store().add_guest(GUEST1_ID, name="A")
    get_store().add_guest(GUEST2_ID, name="B")

    update, ctx = _mk_update(ADMIN_ID, ["Hello", "everyone"])
    await broadcast_command(update, ctx)

    # Two guest sends, zero admin-self sends (sender skipped).
    chat_ids = {c.kwargs["chat_id"] for c in ctx.bot.send_message.call_args_list}
    assert chat_ids == {GUEST1_ID, GUEST2_ID}

    # Audit entry exists.
    entries = tail_audit(10, audit_file=str(tmp_path / "admin_audit.jsonl"))
    assert any(e["action"] == "broadcast" for e in entries)


@pytest.mark.asyncio
async def test_broadcast_requires_message():
    update, ctx = _mk_update(ADMIN_ID)
    await broadcast_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "用法" in text
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_skips_banned_guests():
    get_store().add_guest(GUEST1_ID)
    get_store().add_guest(GUEST2_ID)
    get_store().set_banned(GUEST2_ID, True)

    update, ctx = _mk_update(ADMIN_ID, ["ping"])
    await broadcast_command(update, ctx)

    chat_ids = {c.kwargs["chat_id"] for c in ctx.bot.send_message.call_args_list}
    assert chat_ids == {GUEST1_ID}  # banned guest not notified


@pytest.mark.asyncio
async def test_broadcast_non_admin_silent():
    update, ctx = _mk_update(STRANGER_ID, ["hi"])
    await broadcast_command(update, ctx)
    update.message.reply_text.assert_not_called()
    ctx.bot.send_message.assert_not_called()


# ── /logs ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logs_empty_state_reports_nothing(tmp_path):
    update, ctx = _mk_update(ADMIN_ID)
    await logs_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "沒有" in text


@pytest.mark.asyncio
async def test_logs_reads_tail_of_state_files(tmp_path):
    (tmp_path / "bot.log").write_text(
        "line1\nline2\nERROR something\n", encoding="utf-8"
    )
    (tmp_path / "gateway.log").write_text("gw-line\n", encoding="utf-8")
    update, ctx = _mk_update(ADMIN_ID, ["10"])
    await logs_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "bot.log" in text
    assert "ERROR something" in text
    assert "gw-line" in text


@pytest.mark.asyncio
async def test_logs_rejects_bad_n():
    update, ctx = _mk_update(ADMIN_ID, ["not-a-number"])
    await logs_command(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "用法" in text


# ── /health (active probe) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_probes_all_available_models():
    from echeneis.bot.gateway_client import GatewayError

    update, ctx = _mk_update(ADMIN_ID)
    gateway = MagicMock()
    gateway.models = AsyncMock(
        return_value={
            "models": [
                {"name": "ok-model", "available": True, "has_key": True},
                {"name": "degraded-model", "available": True, "has_key": True},
                {"name": "no-key-model", "available": True, "has_key": False},
            ]
        }
    )

    async def fake_chat(**kwargs):
        model = kwargs.get("model")
        if model == "ok-model":
            return {"choices": [{"message": {"content": "ok"}}]}
        if model == "degraded-model":
            raise GatewayError(
                "Model degraded-model failed: ... 'DEGRADED function ...'",
                status_code=502,
            )
        raise AssertionError(f"unexpected probe target: {model}")

    gateway.chat = AsyncMock(side_effect=fake_chat)
    ctx.bot_data["gateway"] = gateway

    await health_command(update, ctx)

    # Only models with has_key were probed.
    probed = {c.kwargs["model"] for c in gateway.chat.call_args_list}
    assert probed == {"ok-model", "degraded-model"}

    sent_msg = update.message.reply_text.return_value
    final = sent_msg.edit_text.call_args[0][0]
    assert "ok-model" in final
    assert "degraded-model" in final
    assert "DEGRADED" in final
    assert "1 ok" in final
    assert "1 degraded" in final


@pytest.mark.asyncio
async def test_health_handles_models_api_error():
    from echeneis.bot.gateway_client import GatewayError

    update, ctx = _mk_update(ADMIN_ID)
    gateway = MagicMock()
    gateway.models = AsyncMock(side_effect=GatewayError("boom"))
    ctx.bot_data["gateway"] = gateway

    await health_command(update, ctx)
    sent_msg = update.message.reply_text.return_value
    final = sent_msg.edit_text.call_args[0][0]
    assert "boom" in final or "無法" in final


@pytest.mark.asyncio
async def test_health_empty_probe_list():
    update, ctx = _mk_update(ADMIN_ID)
    gateway = MagicMock()
    gateway.models = AsyncMock(
        return_value={
            "models": [
                {"name": "nope", "available": False, "has_key": True},
            ]
        }
    )
    ctx.bot_data["gateway"] = gateway

    await health_command(update, ctx)
    sent_msg = update.message.reply_text.return_value
    final = sent_msg.edit_text.call_args[0][0]
    assert "沒有" in final


@pytest.mark.asyncio
async def test_probe_model_classifies_statuses():
    """Direct unit test of the classifier. Exercises each status branch."""
    from echeneis.bot.gateway_client import GatewayError
    from echeneis.bot.handlers.ops import _probe_model

    gateway = MagicMock()

    # ok
    gateway.chat = AsyncMock(return_value={"choices": []})
    r = await _probe_model(gateway, "m1", timeout_s=5.0)
    assert r["status"] == "ok"
    assert "latency_ms" in r

    # degraded
    gateway.chat = AsyncMock(side_effect=GatewayError("... DEGRADED ...", 502))
    r = await _probe_model(gateway, "m2", timeout_s=5.0)
    assert r["status"] == "degraded"

    # rate_limited
    gateway.chat = AsyncMock(side_effect=GatewayError("rate-limited", 429))
    r = await _probe_model(gateway, "m3", timeout_s=5.0)
    assert r["status"] == "rate_limited"

    # generic error
    gateway.chat = AsyncMock(side_effect=GatewayError("something else", 500))
    r = await _probe_model(gateway, "m4", timeout_s=5.0)
    assert r["status"] == "error"

    # timeout
    async def _hang(**_):
        await asyncio.sleep(10)

    gateway.chat = AsyncMock(side_effect=_hang)
    r = await _probe_model(gateway, "m5", timeout_s=0.05)
    assert r["status"] == "timeout"


# ── /reload ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_refreshes_store(tmp_path):
    # Write a guest directly to disk, bypassing the in-memory store.
    users_file = tmp_path / "users.json"
    users_file.write_text(
        '{"version":1,"users":{"777":{"name":"Direct","added_by":0,'
        '"added_at":"2026-04-19T00:00:00+00:00","banned":false,"notes":""}}}',
        encoding="utf-8",
    )

    update, ctx = _mk_update(ADMIN_ID)
    await reload_command(update, ctx)

    from echeneis.bot.user_store import Role

    assert get_store().role(777) is Role.GUEST
    text = update.message.reply_text.call_args[0][0]
    assert "users.json" in text

    entries = tail_audit(5, audit_file=str(tmp_path / "admin_audit.jsonl"))
    assert any(e["action"] == "reload" for e in entries)


@pytest.mark.asyncio
async def test_reload_clears_prompt_cache():
    from echeneis.bot.handlers import requests as req_mod

    req_mod._prompt_seen[555] = 12345.0

    update, ctx = _mk_update(ADMIN_ID)
    await reload_command(update, ctx)

    assert 555 not in req_mod._prompt_seen
