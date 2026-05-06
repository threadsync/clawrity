"""
Clawrity — Application Settings

Loads environment variables via pydantic-settings.
All secrets read from .env file — nothing is hardcoded.
"""

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Database ---
    database_url: str = "postgresql://user:pass@localhost:5432/clawrity"

    # --- LLM Providers ---
    groq_api_key: str = ""
    nvidia_api_key: str = ""
    xiaomi_api_key: str = ""
    xiaomi_base_url: str = "https://api.xiaomi.com/v1"
    xiaomi_region: str = "sg"
    mistral_api_key: str = ""

    # --- Ollama (local LLM — no API key needed) ---
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1:8b"

    # --- Slack (Socket Mode) ---
    # Bot Token (xoxb-...) — OAuth & Permissions → Install to Workspace
    slack_bot_token: str = ""
    # App-Level Token (xapp-...) — Socket Mode → Generate Token
    slack_app_token: str = ""
    # Signing Secret — Basic Information → App Credentials
    slack_signing_secret: str = ""

    # --- Tavily Web Search ---
    tavily_api_key: str = ""

    # --- Slack Webhook for digest delivery ---
    acme_slack_webhook: str = ""

    # --- Paths ---
    data_raw_dir: str = "data/raw"
    data_processed_dir: str = "data/processed"
    logs_dir: str = "logs"
    clients_config_dir: str = "config/clients"

    # --- Model Defaults ---
    llm_model: str = "meta/llama-3.3-70b-instruct"
    llm_provider: str = ""  # auto-detected: "ollama", "nvidia", "groq", "xiaomi", or "mistral"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    @property
    def active_llm_provider(self) -> str:
        """Auto-detect which LLM provider to use based on available keys.
        Priority: explicit setting > ollama > mistral > xiaomi > nvidia > groq."""
        if self.llm_provider:
            return self.llm_provider
        # Prefer Ollama if available (local, fast, no rate limits)
        if self._ollama_available():
            return "ollama"
        if self.mistral_api_key:
            return "mistral"
        if self.xiaomi_api_key:
            return "xiaomi"
        if self.nvidia_api_key:
            return "nvidia"
        if self.groq_api_key:
            return "groq"
        return "ollama"  # default to ollama (user can install it)

    def _ollama_available(self) -> bool:
        """Quick check if Ollama is likely running locally."""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.ollama_base_url.rstrip('/').replace('/v1', '')}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except Exception:
            return False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """Singleton settings instance. Cached after first call."""
    return Settings()
