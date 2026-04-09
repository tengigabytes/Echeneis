"""Routing configuration parser.

Loads routing_rules.yaml and exposes typed dataclasses
for tiers, task types, and failover settings.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class FailoverConfig:
    """Failover behavior configuration."""

    on_429: str = "switch_to_fallback"
    retry_after_seconds: int = 60
    timeout_seconds: int = 30
    circuit_breaker_failures: int = 3
    circuit_breaker_open_seconds: int = 300
    circuit_breaker_half_open: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailoverConfig":
        """Parse failover section from routing_rules.yaml."""
        cb = data.get("circuit_breaker", {})
        return cls(
            on_429=data.get("on_429", "switch_to_fallback"),
            retry_after_seconds=data.get("on_5xx", {}).get("retry_after_seconds", 60),
            timeout_seconds=data.get("on_timeout", {}).get("timeout_seconds", 30),
            circuit_breaker_failures=cb.get("consecutive_failures", 3),
            circuit_breaker_open_seconds=cb.get("open_duration_seconds", 300),
            circuit_breaker_half_open=cb.get("half_open_test", True),
        )


@dataclass
class TaskType:
    """A task type with keyword triggers and default tier."""

    name: str
    keywords: list[str]
    default_tier: str
    detect: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskType":
        """Parse a single task_type entry."""
        return cls(
            name=data["name"],
            keywords=data.get("keywords", []),
            default_tier=data.get("default_tier", "A"),
            detect=data.get("detect"),
        )


@dataclass
class Tier:
    """A routing tier (S/A/B) with model assignments and fallbacks."""

    name: str
    description: str
    models: dict[str, str]
    fallback: dict[str, str] | list[str]
    triggers: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "Tier":
        """Parse a single tier entry."""
        fallback_raw = data.get("fallback", {})
        if isinstance(fallback_raw, list):
            fallback: dict[str, str] | list[str] = fallback_raw
        else:
            fallback = dict(fallback_raw)

        return cls(
            name=name,
            description=data.get("description", ""),
            models=dict(data.get("models", {})),
            fallback=fallback,
            triggers=data.get("trigger", []),
        )

    def get_model(self, task_type: str) -> str | None:
        """Get primary model for a task type within this tier."""
        return self.models.get(task_type)

    def get_fallback(self, task_type: str) -> str | None:
        """Get fallback model for a task type within this tier."""
        if isinstance(self.fallback, list):
            return self.fallback[0] if self.fallback else None
        return self.fallback.get(task_type)


@dataclass
class CrossCheckConfig:
    """Cross-check configuration for quality assurance."""

    enabled: bool = False
    verify_model: str = ""
    trigger: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrossCheckConfig":
        """Parse cross_check section."""
        return cls(
            enabled=data.get("enabled", False),
            verify_model=data.get("verify_model", ""),
            trigger=data.get("trigger", ""),
        )


@dataclass
class RoutingConfig:
    """Top-level routing configuration."""

    tiers: dict[str, Tier]
    task_types: list[TaskType]
    failover: FailoverConfig
    cross_check: CrossCheckConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutingConfig":
        """Parse full routing_rules.yaml content."""
        tiers = {
            name: Tier.from_dict(name, tier_data)
            for name, tier_data in data.get("tiers", {}).items()
        }
        task_types = [TaskType.from_dict(tt) for tt in data.get("task_types", [])]
        failover = FailoverConfig.from_dict(data.get("failover", {}))
        cross_check = CrossCheckConfig.from_dict(data.get("cross_check", {}))
        return cls(
            tiers=tiers,
            task_types=task_types,
            failover=failover,
            cross_check=cross_check,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RoutingConfig":
        """Load routing config from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
