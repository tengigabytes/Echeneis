"""Tests for task classifier."""

from echeneis.gateway.classifier import Classification, TaskClassifier
from echeneis.gateway.config import RoutingConfig

# Minimal config for testing
_TEST_CONFIG = RoutingConfig.from_dict(
    {
        "tiers": {
            "S": {
                "description": "Deep reasoning",
                "trigger": [{"command": "/think"}],
                "models": {"reasoning": "gemini-2.5-flash"},
                "fallback": ["github-gpt-4o"],
            },
            "A": {
                "description": "General purpose",
                "trigger": [{"default": True}],
                "models": {
                    "translation": "mistral-large-3",
                    "code_generation": "gemma-4-31b",
                    "general_qa": "cerebras-llama-70b",
                    "vision": "gemma-4-31b",
                },
                "fallback": {
                    "translation": "gemma-4-26b",
                    "code_generation": "mistral-large-3",
                    "general_qa": "groq-llama-70b",
                    "vision": "gemini-2.5-flash",
                },
            },
            "B": {
                "description": "Batch",
                "trigger": [{"command": "/fast"}],
                "models": {"batch": "groq-llama-70b", "summarization": "gemma-4-26b"},
                "fallback": {
                    "batch": "cerebras-llama-70b",
                    "summarization": "groq-llama-70b",
                },
            },
        },
        "task_types": [
            {
                "name": "translation",
                "keywords": ["translate", "翻譯", "翻成", "translation"],
                "default_tier": "A",
            },
            {
                "name": "code_generation",
                "keywords": ["code", "implement", "function", "debug", "refactor"],
                "default_tier": "A",
            },
            {
                "name": "summarization",
                "keywords": ["summarize", "summary", "tldr", "摘要"],
                "default_tier": "B",
            },
            {
                "name": "reasoning",
                "keywords": ["think", "reason", "explain why"],
                "default_tier": "S",
            },
            {
                "name": "vision",
                "keywords": [],
                "detect": "image_attachment",
                "default_tier": "A",
            },
        ],
        "failover": {},
        "cross_check": {},
    }
)


def _classifier() -> TaskClassifier:
    return TaskClassifier(_TEST_CONFIG)


class TestCommandOverrides:
    def test_think_command(self) -> None:
        result = _classifier().classify("hello", command="/think")
        assert result == Classification(task_type="reasoning", tier="S")

    def test_fast_command(self) -> None:
        result = _classifier().classify("hello", command="/fast")
        assert result == Classification(task_type="batch", tier="B")


class TestImageDetection:
    def test_image_attachment(self) -> None:
        result = _classifier().classify("what is this?", has_image=True)
        assert result == Classification(task_type="vision", tier="A")

    def test_image_overrides_keywords(self) -> None:
        result = _classifier().classify("translate this image", has_image=True)
        assert result.task_type == "vision"


class TestKeywordMatching:
    def test_translation_english(self) -> None:
        result = _classifier().classify("Please translate this to Japanese")
        assert result == Classification(task_type="translation", tier="A")

    def test_translation_chinese(self) -> None:
        result = _classifier().classify("幫我翻譯這段話")
        assert result == Classification(task_type="translation", tier="A")

    def test_code_generation(self) -> None:
        result = _classifier().classify("implement a linked list")
        assert result == Classification(task_type="code_generation", tier="A")

    def test_debug(self) -> None:
        result = _classifier().classify("debug this error")
        assert result.task_type == "code_generation"

    def test_summarization(self) -> None:
        result = _classifier().classify("summarize this document")
        assert result == Classification(task_type="summarization", tier="B")

    def test_case_insensitive(self) -> None:
        result = _classifier().classify("TRANSLATE this now")
        assert result.task_type == "translation"


class TestDefaultFallback:
    def test_no_keywords_match(self) -> None:
        result = _classifier().classify("what is the weather today?")
        assert result == Classification(task_type="general_qa", tier="A")

    def test_empty_text(self) -> None:
        result = _classifier().classify("")
        assert result == Classification(task_type="general_qa", tier="A")
