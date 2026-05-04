"""
Clawrity — Protocol Adapter (OpenClaw Pattern)

Normalises messages from any channel into a unified NormalisedMessage.
Maps workspace/team IDs → client_id. Strips bot mentions.
Interface: any channel handler produces NormalisedMessage — adding Teams,
WhatsApp, etc. requires zero pipeline changes.
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from config.client_loader import ClientConfig

logger = logging.getLogger(__name__)


@dataclass
class NormalisedMessage:
    """Unified message format — channel-agnostic."""
    text: str
    channel: str  # Channel/conversation ID
    user_id: str
    client_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = "unknown"  # "slack", "teams", "api"
    raw_event: Optional[Dict] = None


# Pattern to match Slack bot mentions like <@U1234567890>
SLACK_MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>\s*")


class ProtocolAdapter:
    """Normalises raw channel events into NormalisedMessages."""

    def __init__(self, client_configs: Dict[str, ClientConfig]):
        """
        Args:
            client_configs: Dict of client_id → ClientConfig
        """
        self.client_configs = client_configs
        # Build workspace → client_id lookup
        self._workspace_map: Dict[str, str] = {}
        for cid, config in client_configs.items():
            for ws_id in config.slack_workspace_ids:
                self._workspace_map[ws_id] = cid
        # If only one client, use it as default
        self._default_client_id = (
            list(client_configs.keys())[0] if len(client_configs) == 1 else None
        )

    def normalise_slack(self, event: dict, team_id: Optional[str] = None) -> NormalisedMessage:
        """
        Normalise a Slack event into a NormalisedMessage.

        Args:
            event: Raw Slack event dict (from Bolt SDK)
            team_id: Slack workspace/team ID

        Returns:
            NormalisedMessage
        """
        text = event.get("text", "")
        # Strip bot mention tags
        text = SLACK_MENTION_PATTERN.sub("", text).strip()

        channel = event.get("channel", "")
        user_id = event.get("user", "")

        # Map workspace to client
        client_id = self._resolve_client_id(team_id)

        return NormalisedMessage(
            text=text,
            channel=channel,
            user_id=user_id,
            client_id=client_id,
            source="slack",
            raw_event=event,
        )

    def normalise_api(self, client_id: str, message: str) -> NormalisedMessage:
        """Normalise a direct API call (POST /chat)."""
        return NormalisedMessage(
            text=message,
            channel="api",
            user_id="api_user",
            client_id=client_id,
            source="api",
        )

    def normalise_teams(self, activity: dict) -> NormalisedMessage:
        """
        Normalise a Microsoft Teams Bot Framework activity.
        # TODO: Implement full Teams normalisation when Teams handler is wired up.
        """
        text = activity.get("text", "")
        # Strip Teams bot mention (usually <at>BotName</at>)
        text = re.sub(r"<at>.*?</at>\s*", "", text).strip()

        return NormalisedMessage(
            text=text,
            channel=activity.get("channelId", "teams"),
            user_id=activity.get("from", {}).get("id", ""),
            client_id=self._default_client_id or "unknown",
            source="teams",
            raw_event=activity,
        )

    def _resolve_client_id(self, workspace_id: Optional[str]) -> str:
        """Resolve workspace/team ID to client_id."""
        if workspace_id and workspace_id in self._workspace_map:
            return self._workspace_map[workspace_id]
        if self._default_client_id:
            return self._default_client_id
        logger.warning(f"Could not resolve client for workspace: {workspace_id}")
        return "unknown"
