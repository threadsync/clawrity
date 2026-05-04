"""
Clawrity — Microsoft Teams Handler (STUB)

Skeleton implementation of the Bot Framework adapter for Microsoft Teams.
Proves the multi-channel architecture is real — any channel handler produces
NormalisedMessage via ProtocolAdapter, so the entire pipeline works unchanged.

# TODO: Wire up Azure Bot credentials when ready for Teams demo.
#       Required: MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD
#       Package: botbuilder-core, botbuilder-schema

Status: NOT IMPLEMENTED — Slack is the priority for development.
"""

import logging
from typing import Dict, Optional

from channels.protocol_adapter import ProtocolAdapter, NormalisedMessage
from config.client_loader import ClientConfig

logger = logging.getLogger(__name__)


class TeamsHandler:
    """
    Microsoft Teams bot handler stub.

    Architecture:
        Teams Activity → ProtocolAdapter.normalise_teams() → Orchestrator → Response

    The same pipeline used by Slack — zero business logic in this layer.
    """

    def __init__(
        self,
        protocol_adapter: ProtocolAdapter,
        client_configs: Dict[str, ClientConfig],
        orchestrator,  # agents.orchestrator.Orchestrator
    ):
        self.adapter = protocol_adapter
        self.client_configs = client_configs
        self.orchestrator = orchestrator

        # TODO: Wire up Azure Bot credentials from .env
        # self.app_id = settings.microsoft_app_id
        # self.app_password = settings.microsoft_app_password

    async def handle_activity(self, activity: dict) -> str:
        """
        Process an incoming Teams Bot Framework activity.

        # TODO: Implement when ready for Teams demo.

        Expected flow:
        1. Receive activity from Bot Framework webhook
        2. Normalise via ProtocolAdapter.normalise_teams(activity)
        3. Route to Orchestrator.process(message, client_config)
        4. Return response via Bot Framework turn context

        Args:
            activity: Raw Bot Framework activity dict

        Returns:
            Response text to send back to Teams
        """
        # --- Stub implementation ---
        message = self.adapter.normalise_teams(activity)

        client_config = self.client_configs.get(message.client_id)
        if not client_config:
            return f"No configuration found for client: {message.client_id}"

        result = await self.orchestrator.process(message, client_config)
        return result["response"]

    def setup_routes(self, app):
        """
        Register Teams webhook endpoint with FastAPI.

        # TODO: Implement Bot Framework adapter integration.

        Expected endpoint:
            POST /api/teams/messages → Bot Framework webhook

        Requires:
            - botbuilder-core package
            - BotFrameworkAdapter with app_id + app_password
            - CloudAdapter or BotFrameworkHttpClient
        """
        logger.info(
            "Teams handler stub loaded. "
            "To enable Teams: install botbuilder-core, set Azure Bot credentials."
        )

        # TODO: Uncomment and implement when ready
        #
        # from botbuilder.core import (
        #     BotFrameworkAdapter,
        #     BotFrameworkAdapterSettings,
        #     TurnContext,
        # )
        #
        # settings = BotFrameworkAdapterSettings(
        #     app_id=self.app_id,
        #     app_password=self.app_password,
        # )
        # adapter = BotFrameworkAdapter(settings)
        #
        # @app.post("/api/teams/messages")
        # async def teams_webhook(request: Request):
        #     body = await request.json()
        #     activity = Activity().deserialize(body)
        #     auth_header = request.headers.get("Authorization", "")
        #     response = await adapter.process_activity(
        #         activity, auth_header, self._on_turn
        #     )
        #     return response
        #
        # async def _on_turn(turn_context: TurnContext):
        #     activity = turn_context.activity
        #     response = await self.handle_activity(activity.__dict__)
        #     await turn_context.send_activity(response)

        pass
