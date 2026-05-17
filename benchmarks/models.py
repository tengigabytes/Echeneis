"""Model capability metadata.

Declares which models support vision, and per-provider rate-limit
delays so the benchmark harness can throttle requests appropriately.
"""

from __future__ import annotations

# Models that accept image_url content (vision-capable).
# Derived from CLAUDE.md benchmark results — Groq/Cerebras are text-only.
VISION_MODELS: set[str] = {
    "gemma-4-31b",
    "gemma-4-26b",
    "mistral-large-3",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "github-gpt-4o",
}

# Per-provider inter-request delay (seconds).
# Conservative defaults to stay within free-tier rate limits.
PROVIDER_DELAYS: dict[str, float] = {
    "gemini": 5.0,  # Google AI Studio: 15 RPM = 4s + margin for request time
    "mistral": 1.5,  # Mistral: 1 req/s hard limit
    "openrouter": 3.0,  # OpenRouter: 20 RPM, 50 RPD shared
    "groq": 2.0,  # Groq: 30 RPM
    "cerebras": 2.0,  # Cerebras: 30 RPM
    "cloudflare": 1.0,  # Cloudflare: 300 RPM
    "github": 4.0,  # GitHub Models: 15 RPM
}

DEFAULT_DELAY: float = 2.0


def get_provider(litellm_model: str) -> str:
    """Extract provider prefix from a litellm model string.

    Args:
        litellm_model: Full model string, e.g. "groq/llama-3.3-70b-versatile".

    Returns:
        Provider prefix, e.g. "groq".
    """
    return litellm_model.split("/", 1)[0] if "/" in litellm_model else "unknown"


def get_delay(provider: str) -> float:
    """Get the inter-request delay for a provider.

    Args:
        provider: Provider name, e.g. "groq".

    Returns:
        Delay in seconds.
    """
    return PROVIDER_DELAYS.get(provider, DEFAULT_DELAY)


def supports_vision(model_name: str) -> bool:
    """Check if a model supports vision (image input).

    Args:
        model_name: Short model name, e.g. "gemma-4-31b".

    Returns:
        True if the model can process images.
    """
    return model_name in VISION_MODELS
