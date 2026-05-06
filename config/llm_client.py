"""
Clawrity — LLM Client Factory

Provides a unified LLM client that works with Ollama (local), NVIDIA NIM, Groq, Xiaomi, and Mistral.
All are OpenAI-compatible APIs, so we use the OpenAI client with different
base URLs and API keys.

Auto-detects provider from settings:
  - Ollama (local): base_url="http://localhost:11434/v1"
  - NVIDIA NIM: base_url="https://integrate.api.nvidia.com/v1"
  - Groq: base_url="https://api.groq.com/openai/v1"
  - Xiaomi (Singapore): base_url="https://api.xiaomi.com/v1"
  - Mistral: base_url="https://api.mistral.ai/v1"
"""

import asyncio
import logging
import time
import threading
from functools import lru_cache

from openai import OpenAI, RateLimitError, APIStatusError
from config.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proactive rate limiter — only for providers with strict free-tier limits
# ---------------------------------------------------------------------------
# Mistral free tier: 4 req/min = 1 request every 15 seconds.
# Ollama, Groq, NVIDIA have generous or no rate limits — skip throttling.
_rate_lock = threading.Lock()
_last_call_time: float = 0.0

# Providers that need proactive throttling (free-tier limited)
_THROTTLED_PROVIDERS = {"mistral"}

# Seconds between calls for throttled providers
_PROVIDER_CALL_GAPS = {
    "mistral": 15.5,  # 60s / 4 calls + margin
}


def _rate_limit_wait(provider: str):
    """Wait if needed to respect the minimum gap between API calls.
    Only applies to providers in _THROTTLED_PROVIDERS."""
    if provider not in _THROTTLED_PROVIDERS:
        return  # No throttling for local/generous-tier providers

    global _last_call_time
    min_gap = _PROVIDER_CALL_GAPS.get(provider, 0)
    if min_gap <= 0:
        return

    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < min_gap:
            wait = min_gap - elapsed
            logger.info(
                f"Rate limiter ({provider}): waiting {wait:.1f}s "
                f"(gap since last call: {elapsed:.1f}s)"
            )
            time.sleep(wait)
        _last_call_time = time.monotonic()


# Provider configs
_PROVIDERS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1:8b",
        "fast_model": "llama3.1:8b",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
        "fast_model": "meta/llama-3.1-8b-instruct",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "fast_model": "llama-3.3-70b-versatile",
    },
    "xiaomi": {
        "base_url": "https://api.xiaomi.com/v1",
        "default_model": "xiaomi/mimo-v2.5-pro",
        "fast_model": "xiaomi/mimo-v2.5-pro",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "fast_model": "mistral-small-latest",
    },
}

MAX_RETRIES = 6
BASE_DELAY = 2.0  # seconds


@lru_cache()
def get_llm_client() -> OpenAI:
    """Get the configured LLM client (Ollama, NVIDIA NIM, Groq, Xiaomi, or Mistral)."""
    settings = get_settings()
    provider = settings.active_llm_provider

    if provider == "ollama":
        # Ollama doesn't need a real API key
        api_key = "ollama"
    elif provider == "nvidia":
        api_key = settings.nvidia_api_key
    elif provider == "groq":
        api_key = settings.groq_api_key
    elif provider == "xiaomi":
        api_key = settings.xiaomi_api_key
    elif provider == "mistral":
        api_key = settings.mistral_api_key
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    if not api_key and provider != "ollama":
        key_name = {
            "xiaomi": "XIAOMI_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }.get(provider, "API_KEY")
        raise ValueError(
            f"No API key configured for LLM provider '{provider}'. "
            f"Set {key_name} in .env"
        )

    config = _PROVIDERS[provider]
    base_url = config["base_url"]

    # Use custom base URLs from settings if available
    if provider == "xiaomi" and settings.xiaomi_base_url:
        base_url = settings.xiaomi_base_url
    elif provider == "ollama" and settings.ollama_base_url:
        base_url = settings.ollama_base_url

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,  # We handle retries ourselves for better control
    )

    logger.info(f"LLM client: {provider} ({base_url})")
    return client


def chat_with_retry(client: OpenAI, **kwargs):
    """
    Call client.chat.completions.create with exponential backoff on 429 errors.
    Proactively rate-limits only for throttled providers (e.g., Mistral free tier).

    Args:
        client: OpenAI client instance
        **kwargs: Arguments passed to chat.completions.create

    Returns:
        The completion response

    Raises:
        RateLimitError: After all retries exhausted
        APIStatusError: For non-429 API errors
    """
    settings = get_settings()
    provider = settings.active_llm_provider

    for attempt in range(MAX_RETRIES + 1):
        try:
            _rate_limit_wait(provider)
            result = client.chat.completions.create(**kwargs)
            return result
        except RateLimitError as e:
            if attempt == MAX_RETRIES:
                logger.error(f"Rate limit: all {MAX_RETRIES} retries exhausted")
                raise
            delay = BASE_DELAY * (2**attempt)
            logger.warning(
                f"Rate limited (429), retrying in {delay:.1f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES})"
            )
            time.sleep(delay)
        except APIStatusError as e:
            if e.status_code == 429 and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2**attempt)
                logger.warning(
                    f"Rate limited (429), retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
            else:
                raise


async def async_chat_with_retry(client: OpenAI, **kwargs):
    """
    Async wrapper for chat_with_retry.
    Runs the synchronous OpenAI call in a thread pool so it doesn't block
    the asyncio event loop (important for FastAPI).
    """
    return await asyncio.to_thread(chat_with_retry, client, **kwargs)


def get_model_name() -> str:
    """Get the model name for the active provider."""
    settings = get_settings()
    provider = settings.active_llm_provider

    # Allow override from settings
    if provider == "ollama" and settings.ollama_model:
        return settings.ollama_model

    return _PROVIDERS[provider]["default_model"]


def get_fast_model_name() -> str:
    """Get a lighter/faster model for the active provider (used for QA evaluation)."""
    settings = get_settings()
    provider = settings.active_llm_provider

    # For Ollama, use the same model (running locally is already fast)
    if provider == "ollama" and settings.ollama_model:
        return settings.ollama_model

    return _PROVIDERS[provider].get("fast_model", _PROVIDERS[provider]["default_model"])
