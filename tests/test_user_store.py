"""Tests for UserStore persistence and role resolution."""

from __future__ import annotations

import json

import pytest

from echeneis.bot.user_store import Role, UserStore, get_store, reset_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip auth env vars between tests for determinism."""
    monkeypatch.delenv("TELEGRAM_ADMIN_USERS", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    reset_store()
    yield
    reset_store()


def test_empty_store_has_no_users(tmp_path):
    store = UserStore(users_file=str(tmp_path / "users.json"))
    assert store.list_admins() == []
    assert store.list_guests() == []
    assert store.role(123) is Role.UNREGISTERED


def test_admin_seeded_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "100, 200")
    store = UserStore(users_file=str(tmp_path / "users.json"))
    assert set(store.list_admins()) == {100, 200}
    assert store.role(100) is Role.ADMIN
    assert store.is_admin(200)
    assert store.role(999) is Role.UNREGISTERED


def test_legacy_fallback_treats_allowed_as_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "500")
    store = UserStore(users_file=str(tmp_path / "users.json"))
    assert store.role(500) is Role.ADMIN


def test_add_and_remove_guest_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "100")
    store = UserStore(users_file=str(path))

    assert store.add_guest(300, name="Bob", added_by=100) is True
    assert store.role(300) is Role.GUEST

    # Persisted to disk.
    with path.open() as f:
        data = json.load(f)
    assert "300" in data["users"]
    assert data["users"]["300"]["name"] == "Bob"
    assert data["users"]["300"]["added_by"] == 100

    # Reload from disk preserves state.
    store2 = UserStore(users_file=str(path))
    assert store2.role(300) is Role.GUEST

    assert store.remove_guest(300) is True
    assert store.role(300) is Role.UNREGISTERED


def test_cannot_add_admin_as_guest(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "100")
    store = UserStore(users_file=str(tmp_path / "users.json"))
    assert store.add_guest(100) is False
    assert store.role(100) is Role.ADMIN


def test_banned_guest_is_unregistered(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "100")
    store = UserStore(users_file=str(tmp_path / "users.json"))
    store.add_guest(300)
    assert store.set_banned(300, True) is True
    assert store.role(300) is Role.UNREGISTERED
    assert not store.is_authorized(300)

    store.set_banned(300, False)
    assert store.role(300) is Role.GUEST


def test_atomic_persist_does_not_leak_tmp_file(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "1")
    store = UserStore(users_file=str(path))
    store.add_guest(42)
    tmp_leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".users-")]
    assert tmp_leftovers == [], f"tmp files leaked: {tmp_leftovers}"


def test_malformed_json_does_not_crash(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    path.write_text("{not valid json")
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "1")
    # Should not raise.
    store = UserStore(users_file=str(path))
    assert store.list_guests() == []


def test_singleton_get_store(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USERS", "1")
    a = get_store()
    b = get_store()
    assert a is b
