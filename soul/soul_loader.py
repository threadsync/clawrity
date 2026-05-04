"""
Clawrity — SOUL Loader

Reads the SOUL.md file for a client and returns raw text for prompt injection.
SOUL.md defines the AI's personality, business context, and rules per client.
"""

import logging
from pathlib import Path
from typing import Optional

from config.client_loader import ClientConfig

logger = logging.getLogger(__name__)


def load_soul(client_config: ClientConfig) -> str:
    """
    Load the SOUL.md content for a client.

    Args:
        client_config: The client's configuration containing soul_file path.

    Returns:
        Raw markdown text of the SOUL file, or a default prompt if file not found.
    """
    soul_path = Path(client_config.soul_file)

    if not soul_path.exists():
        logger.warning(
            f"SOUL file not found at {soul_path} for client {client_config.client_id}. "
            f"Using default personality."
        )
        return _default_soul(client_config)

    try:
        content = soul_path.read_text(encoding="utf-8")
        logger.info(f"Loaded SOUL for {client_config.client_id} from {soul_path}")
        return content
    except Exception as e:
        logger.error(f"Error reading SOUL file {soul_path}: {e}")
        return _default_soul(client_config)


def _default_soul(client_config: ClientConfig) -> str:
    """Generate a minimal default SOUL if the file is missing."""
    return f"""# SOUL — {client_config.client_name}

## Identity
You are Clawrity, {client_config.client_name}'s business intelligence assistant.
Speak professionally. Always ground answers in data. Never speculate.

## Rules
- If data unavailable, say "I don't have that data right now"
- Always cite specific data points in your responses
"""
