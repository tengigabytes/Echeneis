"""Tests for the Telegram bot middleware (whitelist)."""

from unittest.mock import MagicMock

import pytest

from echeneis.bot.middleware import (
    get_allowed_users,
    is_authorized,
    reset_whitelist,
)


@pytest.fixture(autouse=True)
def _reset():
    """Reset whitelist cache before each test."""
    reset_whitelist()
    yield
    reset_whitelist()


class TestWhitelist:
    """Tests for the whitelist loading and checking."""

    def test_load_from_env(self, monkeypatch):
        """Loads comma-separated user IDs from env."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111,222,333")
        users = get_allowed_users()
        assert users == {111, 222, 333}

    def test_empty_env_returns_empty_set(self, monkeypatch):
        """Empty env var results in empty whitelist."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
        users = get_allowed_users()
        assert users == set()

    def test_missing_env_returns_empty_set(self, monkeypatch):
        """Missing env var results in empty whitelist."""
        monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
        users = get_allowed_users()
        assert users == set()

    def test_whitespace_handling(self, monkeypatch):
        """Handles whitespace around IDs."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", " 111 , 222 , ")
        users = get_allowed_users()
        assert users == {111, 222}

    def test_invalid_id_skipped(self, monkeypatch):
        """Non-integer IDs are skipped with a warning."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111,abc,333")
        users = get_allowed_users()
        assert users == {111, 333}


class TestIsAuthorized:
    """Tests for the is_authorized check."""

    def test_authorized_user(self, monkeypatch):
        """User in whitelist is authorized."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
        update = _make_update(user_id=12345)
        assert is_authorized(update) is True

    def test_unauthorized_user(self, monkeypatch):
        """User not in whitelist is rejected."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
        update = _make_update(user_id=99999)
        assert is_authorized(update) is False

    def test_empty_whitelist_blocks_all(self, monkeypatch):
        """Empty whitelist blocks everyone."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
        update = _make_update(user_id=12345)
        assert is_authorized(update) is False

    def test_no_effective_user(self, monkeypatch):
        """Update without effective_user is rejected."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "12345")
        update = MagicMock()
        update.effective_user = None
        assert is_authorized(update) is False


def _make_update(user_id: int) -> MagicMock:
    """Create a minimal mock Update with a user ID."""
    update = MagicMock()
    update.effective_user.id = user_id
    return update
