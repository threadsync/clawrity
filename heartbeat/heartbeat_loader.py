"""
Clawrity — HEARTBEAT Loader

Parses HEARTBEAT.md files to extract schedule, digest tasks, and retry config.
HEARTBEAT.md drives autonomous daily digest generation per client.
"""

import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from config.client_loader import ClientConfig

logger = logging.getLogger(__name__)


class HeartbeatConfig:
    """Parsed heartbeat configuration."""

    def __init__(self):
        self.trigger: str = "daily"
        self.time: str = "08:00"
        self.timezone: str = "UTC"
        self.retry_delay_minutes: int = 15
        self.max_retries: int = 3
        self.tasks: list = []
        self.raw_content: str = ""

    @property
    def hour(self) -> int:
        """Extract hour from time string."""
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        """Extract minute from time string."""
        return int(self.time.split(":")[1])


def load_heartbeat(client_config: ClientConfig) -> HeartbeatConfig:
    """
    Load and parse the HEARTBEAT.md file for a client.

    Args:
        client_config: The client's configuration containing heartbeat_file path.

    Returns:
        Parsed HeartbeatConfig with schedule, tasks, and retry settings.
    """
    config = HeartbeatConfig()
    heartbeat_path = Path(client_config.heartbeat_file)

    # Use client YAML timezone as fallback
    config.timezone = client_config.timezone

    if not heartbeat_path.exists():
        logger.warning(
            f"HEARTBEAT file not found at {heartbeat_path} for client "
            f"{client_config.client_id}. Using defaults from client YAML."
        )
        config.time = client_config.digest_schedule
        return config

    try:
        content = heartbeat_path.read_text(encoding="utf-8")
        config.raw_content = content
        _parse_heartbeat(content, config)
        logger.info(
            f"Loaded HEARTBEAT for {client_config.client_id}: "
            f"{config.trigger} at {config.time} {config.timezone}"
        )
    except Exception as e:
        logger.error(f"Error parsing HEARTBEAT file {heartbeat_path}: {e}")
        config.time = client_config.digest_schedule

    return config


def _parse_heartbeat(content: str, config: HeartbeatConfig) -> None:
    """Parse markdown content and extract structured config."""
    lines = content.split("\n")

    current_section = None
    task_lines = []

    for line in lines:
        stripped = line.strip()

        # Detect section headers
        if stripped.startswith("## "):
            current_section = stripped[3:].strip().lower()
            continue

        if current_section == "schedule":
            # Parse key-value pairs like "- trigger: daily"
            match = re.match(r"-\s*(\w+):\s*\"?([^\"]+)\"?", stripped)
            if match:
                key, value = match.group(1).strip(), match.group(2).strip()
                if key == "trigger":
                    config.trigger = value
                elif key == "time":
                    config.time = value
                elif key == "timezone":
                    config.timezone = value

        elif current_section == "digest tasks":
            # Parse numbered list items
            match = re.match(r"\d+\.\s+(.*)", stripped)
            if match:
                config.tasks.append(match.group(1).strip())

        elif current_section == "retry":
            # Parse retry config
            match = re.match(r"-\s*(\w+):\s*(.+)", stripped)
            if match:
                key, value = match.group(1).strip(), match.group(2).strip()
                if "retry" in key and "after" in value:
                    # Extract minutes from "retry after 15 minutes"
                    mins = re.search(r"(\d+)", value)
                    if mins:
                        config.retry_delay_minutes = int(mins.group(1))
                elif key == "max_retries":
                    config.max_retries = int(value)
