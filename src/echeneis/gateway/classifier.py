"""Task classifier using keyword matching.

Classifies incoming requests by task type (translation, code, vision, etc.)
and maps them to the appropriate routing tier (S/A/B).
No API calls are spent on classification — pure keyword matching.
"""

import logging
from dataclasses import dataclass

from echeneis.gateway.config import RoutingConfig

logger = logging.getLogger(__name__)


@dataclass
class Classification:
    """Result of classifying a request."""

    task_type: str
    tier: str


class TaskClassifier:
    """Keyword-based task classifier.

    Uses keyword matching as the first-pass classifier.
    Falls back to tier A / general_qa when no keywords match.
    """

    def __init__(self, config: RoutingConfig) -> None:
        self._config = config
        self._keyword_map: dict[str, tuple[str, str]] = {}
        for tt in config.task_types:
            for kw in tt.keywords:
                self._keyword_map[kw.lower()] = (tt.name, tt.default_tier)

    def classify(
        self,
        text: str,
        *,
        has_image: bool = False,
        command: str | None = None,
    ) -> Classification:
        """Classify a request into task type and tier.

        Args:
            text: The user message text.
            has_image: Whether the request includes an image attachment.
            command: Explicit command override (e.g. "/think", "/fast").

        Returns:
            Classification with task_type and tier.
        """
        # Command overrides take priority
        if command == "/think":
            return Classification(task_type="reasoning", tier="S")
        if command == "/fast":
            return Classification(task_type="batch", tier="B")

        # Image attachment → vision
        if has_image:
            return Classification(task_type="vision", tier="A")

        # Keyword matching on lowercased text
        text_lower = text.lower()
        for keyword, (task_type, tier) in self._keyword_map.items():
            if keyword in text_lower:
                logger.debug(
                    "Classified as %s (tier %s) via keyword '%s'",
                    task_type,
                    tier,
                    keyword,
                )
                return Classification(task_type=task_type, tier=tier)

        # Default: general QA at tier A
        return Classification(task_type="general_qa", tier="A")
