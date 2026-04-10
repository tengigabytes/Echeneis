"""FastAPI application — the Echeneis gateway.

Exposes an OpenAI-compatible /chat/completions endpoint that
classifies requests and routes them through the tier system.
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from echeneis.gateway.classifier import TaskClassifier
from echeneis.gateway.config import RoutingConfig
from echeneis.gateway.health import HealthTracker
from echeneis.gateway.router import Router, build_model_registry
from echeneis.gateway.usage import UsageTracker

logger = logging.getLogger(__name__)

# Resolve config paths relative to project root
_CONFIG_DIR = Path(os.environ.get("ECHENEIS_CONFIG_DIR", "/app/config"))
_ROUTING_RULES = _CONFIG_DIR / "routing_rules.yaml"
_LITELLM_CONFIG = _CONFIG_DIR / "litellm_config.yaml"


def create_app(
    routing_rules_path: str | Path | None = None,
    litellm_config_path: str | Path | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        routing_rules_path: Override path to routing_rules.yaml.
        litellm_config_path: Override path to litellm_config.yaml.

    Returns:
        Configured FastAPI app.
    """
    routing_path = Path(routing_rules_path or _ROUTING_RULES)
    litellm_path = Path(litellm_config_path or _LITELLM_CONFIG)

    routing_config = RoutingConfig.from_yaml(routing_path)
    usage_tracker = UsageTracker()
    model_registry = build_model_registry(str(litellm_path), usage_tracker)
    health_tracker = HealthTracker(routing_config.failover)
    classifier = TaskClassifier(routing_config)
    router = Router(routing_config, model_registry, health_tracker, usage_tracker)

    app = FastAPI(title="Echeneis Gateway", version="0.1.0")

    # API key authentication
    master_key = os.environ.get("LITELLM_MASTER_KEY", "")

    def _check_auth(request: Request) -> None:
        """Verify Bearer token matches LITELLM_MASTER_KEY."""
        if not master_key:
            return  # No key set — skip auth (local dev)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {master_key}":
            raise HTTPException(status_code=401, detail="Invalid API key")

    # Store components on app state for access in routes
    app.state.classifier = classifier
    app.state.router = router
    app.state.health_tracker = health_tracker
    app.state.routing_config = routing_config
    app.state.model_registry = model_registry

    @app.post("/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        """OpenAI-compatible chat completions endpoint."""
        _check_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        messages: list[dict[str, Any]] = body.get("messages", [])
        if not messages:
            raise HTTPException(status_code=400, detail="No messages provided")

        # Extract classification hints from the request
        last_message = messages[-1]
        text = ""
        has_image = False
        command = body.get("command")  # Custom field for /think, /fast

        # Handle content that may be a string or list (vision format)
        content = last_message.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    text += part.get("text", "")
                elif part.get("type") == "image_url":
                    has_image = True

        classification = classifier.classify(text, has_image=has_image, command=command)
        logger.info(
            "Request classified: %s/%s", classification.tier, classification.task_type
        )

        # Forward supported params to litellm
        extra_params: dict[str, Any] = {}
        for key in ("temperature", "max_tokens", "top_p", "stream", "stop"):
            if key in body:
                extra_params[key] = body[key]

        # If a specific model is requested, bypass classification
        requested_model = body.get("model")

        try:
            if requested_model:
                logger.info("Direct model request: %s", requested_model)
                response = await router.route_direct(
                    requested_model, messages, **extra_params
                )
            else:
                response = await router.route(classification, messages, **extra_params)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            logger.error("Routing failed: %s", e)
            raise HTTPException(status_code=502, detail=str(e))

        # litellm returns a ModelResponse; convert to dict for JSON
        return JSONResponse(content=response.model_dump())

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "providers": health_tracker.get_status(),
        }

    @app.get("/routes")
    async def routes(request: Request) -> dict[str, Any]:
        """Show current routing configuration."""
        _check_auth(request)
        return {
            "tiers": {
                name: {
                    "description": tier.description,
                    "models": tier.models,
                }
                for name, tier in routing_config.tiers.items()
            },
            "task_types": [
                {"name": tt.name, "tier": tt.default_tier}
                for tt in routing_config.task_types
            ],
        }

    @app.get("/models")
    async def models(request: Request) -> dict[str, Any]:
        """List all registered models with availability and usage."""
        _check_auth(request)
        model_list = router.list_models()
        all_usage = usage_tracker.get_all_usage()
        for m in model_list:
            m["usage"] = all_usage.get(m["name"], {})
        return {"models": model_list}

    return app
