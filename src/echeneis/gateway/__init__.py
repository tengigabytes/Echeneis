"""Gateway module — task classification, tiered routing, and health checks."""

from echeneis.gateway.app import create_app

__all__ = ["create_app"]
