"""
Clawrity — LLM Client Factory

Provides a unified LLM client that works with both NVIDIA NIM and Groq.
Both are OpenAI-compatible APIs, so we use the OpenAI client with different
base URLs and API keys.

Auto-detects provider from settings:
  - NVIDIA NIM: base_url="https://integrate.api.nvidia.com/v1"
  - Groq: base_url="https://api.groq.com/openai/v1"
"""

import logging
from functools import lru_cache

from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)

# Provider configs
_PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
}


def get_llm_client() -> OpenAI:
    """Get the configured LLM client (NVIDIA NIM or Groq)."""
    settings = get_settings()
    provider = settings.active_llm_provider

    if provider == "nvidia":
        api_key = settings.nvidia_api_key
    elif provider == "groq":
        api_key = settings.groq_api_key
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    if not api_key:
        raise ValueError(
            f"No API key configured for LLM provider '{provider}'. "
            f"Set {'NVIDIA_API_KEY' if provider == 'nvidia' else 'GROQ_API_KEY'} in .env"
        )

    config = _PROVIDERS[provider]
    client = OpenAI(
        api_key=api_key,
        base_url=config["base_url"],
    )

    logger.info(f"LLM client: {provider} ({config['base_url']})")
    return client


def get_model_name() -> str:
    """Get the model name for the active provider."""
    settings = get_settings()
    provider = settings.active_llm_provider

    # If user specified a model in settings, use it
    # Otherwise use the provider default
    model = settings.llm_model
    if model == "meta/llama-3.3-70b-instruct" and provider == "groq":
        model = _PROVIDERS["groq"]["default_model"]
    elif model == "llama-3.3-70b-versatile" and provider == "nvidia":
        model = _PROVIDERS["nvidia"]["default_model"]

    return model
