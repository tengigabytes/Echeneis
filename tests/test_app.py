"""Integration tests for the FastAPI gateway application."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from echeneis.gateway.app import create_app

_CONFIG_DIR = "config"


@pytest.fixture()
def client():
    """Create a test client with real config files."""
    app = create_app(
        routing_rules_path=f"{_CONFIG_DIR}/routing_rules.yaml",
        litellm_config_path=f"{_CONFIG_DIR}/litellm_config.yaml",
    )
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "providers" in data

    def test_health_providers_initially_empty(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.json()["providers"] == {}


class TestRoutesEndpoint:
    def test_routes_returns_tiers(self, client: TestClient) -> None:
        resp = client.get("/routes")
        assert resp.status_code == 200
        data = resp.json()
        assert "tiers" in data
        assert "S" in data["tiers"]
        assert "A" in data["tiers"]
        assert "B" in data["tiers"]

    def test_routes_returns_task_types(self, client: TestClient) -> None:
        resp = client.get("/routes")
        data = resp.json()
        task_names = [tt["name"] for tt in data["task_types"]]
        assert "translation" in task_names
        assert "code_generation" in task_names
        assert "vision" in task_names


class TestChatCompletions:
    def test_invalid_json_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/chat/completions",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_empty_messages_returns_400(self, client: TestClient) -> None:
        resp = client.post("/chat/completions", json={"messages": []})
        assert resp.status_code == 400

    def test_routes_translation_to_gemma(self, client: TestClient) -> None:
        """Verify a translation request is routed to the correct model."""
        fake_resp = MagicMock()
        fake_resp.model_dump.return_value = {
            "id": "test",
            "choices": [{"message": {"content": "你好"}}],
        }
        mock_acompletion = AsyncMock(return_value=fake_resp)

        with patch(
            "echeneis.gateway.router.litellm.acompletion", mock_acompletion
        ):
            resp = client.post(
                "/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": "translate hello to Chinese"}
                    ]
                },
            )
            assert resp.status_code == 200
            call_kwargs = mock_acompletion.call_args.kwargs
            assert "gemma" in call_kwargs["model"]

    def test_routes_code_to_gemma(self, client: TestClient) -> None:
        """Verify a code request is routed to the correct model."""
        fake_resp = MagicMock()
        fake_resp.model_dump.return_value = {
            "id": "test",
            "choices": [{"message": {"content": "def foo(): pass"}}],
        }
        mock_acompletion = AsyncMock(return_value=fake_resp)

        with patch(
            "echeneis.gateway.router.litellm.acompletion", mock_acompletion
        ):
            resp = client.post(
                "/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": "write a function to sort a list"}
                    ]
                },
            )
            assert resp.status_code == 200
            call_kwargs = mock_acompletion.call_args.kwargs
            assert "gemma" in call_kwargs["model"]

    def test_think_command_routes_to_tier_s(self, client: TestClient) -> None:
        """Verify /think command triggers tier S routing."""
        fake_resp = MagicMock()
        fake_resp.model_dump.return_value = {
            "id": "test",
            "choices": [{"message": {"content": "deep thought"}}],
        }
        mock_acompletion = AsyncMock(return_value=fake_resp)

        with patch(
            "echeneis.gateway.router.litellm.acompletion", mock_acompletion
        ):
            resp = client.post(
                "/chat/completions",
                json={
                    "command": "/think",
                    "messages": [
                        {"role": "user", "content": "explain quantum computing"}
                    ],
                },
            )
            assert resp.status_code == 200
            call_kwargs = mock_acompletion.call_args.kwargs
            assert "gemini" in call_kwargs["model"]

    def test_fast_command_routes_to_tier_b(self, client: TestClient) -> None:
        """Verify /fast command triggers tier B routing."""
        fake_resp = MagicMock()
        fake_resp.model_dump.return_value = {
            "id": "test",
            "choices": [{"message": {"content": "quick answer"}}],
        }
        mock_acompletion = AsyncMock(return_value=fake_resp)

        with patch(
            "echeneis.gateway.router.litellm.acompletion", mock_acompletion
        ):
            resp = client.post(
                "/chat/completions",
                json={
                    "command": "/fast",
                    "messages": [
                        {"role": "user", "content": "summarize this text"}
                    ],
                },
            )
            assert resp.status_code == 200

    def test_all_providers_down_returns_502(self, client: TestClient) -> None:
        """Verify 502 when all models fail."""
        import litellm

        async def always_fail(**kwargs):
            raise litellm.RateLimitError(
                message="rate limited",
                llm_provider="test",
                model="test",
            )

        with patch(
            "echeneis.gateway.router.litellm.acompletion",
            side_effect=always_fail,
        ):
            resp = client.post(
                "/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hello"}]
                },
            )
            assert resp.status_code == 502
