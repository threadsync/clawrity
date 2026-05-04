"""
Clawrity — Slack Handler (Socket Mode)

Listens for app_mention and message events via Slack Bolt SDK.
Runs in a background thread to not block FastAPI.

=== SETUP REQUIRED ===
Before running, configure these in your .env file:

  SLACK_BOT_TOKEN=xoxb-...    ← OAuth & Permissions → Install to Workspace
  SLACK_APP_TOKEN=xapp-...    ← Socket Mode → Generate App-Level Token
  SLACK_SIGNING_SECRET=...    ← Basic Information → App Credentials

See README.md for detailed Slack app setup instructions.
=======================
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set

from config.settings import get_settings
from config.client_loader import ClientConfig
from channels.protocol_adapter import ProtocolAdapter, NormalisedMessage

logger = logging.getLogger(__name__)

# Thread pool for processing LLM pipeline without blocking event handlers
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="clawrity-slack")

# Module-level guard: only one SlackHandler should be active at a time
_active_handler: Optional["SlackHandler"] = None


class SlackHandler:
    """Slack Bot using Socket Mode via Bolt SDK."""

    def __init__(
        self,
        protocol_adapter: ProtocolAdapter,
        client_configs: Dict[str, ClientConfig],
        orchestrator,  # agents.orchestrator.Orchestrator
    ):
        self.adapter = protocol_adapter
        self.client_configs = client_configs
        self.orchestrator = orchestrator
        self._thread: Optional[threading.Thread] = None

        settings = get_settings()

        # ---------------------------------------------------------------
        # Bot Token (xoxb-...) — from .env SLACK_BOT_TOKEN
        # This is the OAuth token installed to your workspace.
        # ---------------------------------------------------------------
        self.bot_token = settings.slack_bot_token

        # ---------------------------------------------------------------
        # App-Level Token (xapp-...) — from .env SLACK_APP_TOKEN
        # Required for Socket Mode. Generated in Slack app settings.
        # ---------------------------------------------------------------
        self.app_token = settings.slack_app_token

        # ---------------------------------------------------------------
        # Signing Secret — from .env SLACK_SIGNING_SECRET
        # Used to verify incoming requests from Slack.
        # ---------------------------------------------------------------
        self.signing_secret = settings.slack_signing_secret

        self.app = None
        self.handler = None

        # Deduplication: track recently processed event timestamps
        # Slack retries events if handler is slow — this prevents duplicates
        self._processed_events: Set[str] = set()
        self._processed_lock = threading.Lock()

    def _validate_tokens(self) -> bool:
        """Check that all required Slack tokens are configured."""
        if not self.bot_token:
            logger.warning(
                "SLACK_BOT_TOKEN not set. Slack bot will not start. "
                "See README.md → Slack Bot Setup for instructions."
            )
            return False
        if not self.app_token:
            logger.warning(
                "SLACK_APP_TOKEN not set. Socket Mode requires an app-level token. "
                "Go to your Slack app → Socket Mode → Generate Token."
            )
            return False
        return True

    def _is_duplicate_event(self, event: dict) -> bool:
        """Check if we've already processed this event (Slack retry dedup)."""
        # Use multiple fields to build a robust dedup key.
        # client_msg_id is unique per user message (present on message events,
        # but NOT on app_mention events). event_ts is present on both.
        # We store keys for all strategies so cross-event-type dedup works.
        msg_id = event.get("client_msg_id")
        event_ts = event.get("event_ts") or event.get("ts", "")
        user = event.get("user", "")

        # Build candidate keys
        keys = set()
        if msg_id:
            keys.add(f"msg:{msg_id}")
        if event_ts:
            keys.add(f"ts:{event_ts}")
        # Fallback: combine event type + ts + user for events without client_msg_id
        event_type = event.get("type", "")
        if event_ts and user:
            keys.add(f"evt:{event_type}:{event_ts}:{user}")

        if not keys:
            return False

        with self._processed_lock:
            # Check ALL keys — if any match, it's a duplicate
            for key in keys:
                if key in self._processed_events:
                    logger.debug(f"Skipping duplicate event (matched key: {key})")
                    return True

            # Register ALL keys so cross-event-type dedup works
            # (app_mention and message for the same user message share event_ts)
            self._processed_events.update(keys)

            # Prune old entries (keep set from growing indefinitely)
            if len(self._processed_events) > 500:
                self._processed_events = set(list(self._processed_events)[-200:])

        return False

    def _setup_app(self):
        """Initialize Slack Bolt App and register event handlers."""
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        self.app = App(
            token=self.bot_token,
            signing_secret=self.signing_secret if self.signing_secret else None,
        )

        # Track bot's own user ID to prevent self-response loops
        self._bot_user_id = None
        try:
            auth = self.app.client.auth_test()
            self._bot_user_id = auth.get("user_id", "")
            logger.info(f"Bot user ID: {self._bot_user_id}")
        except Exception as e:
            logger.warning(f"Could not fetch bot user ID: {e}")

        # --- Event: Bot mentioned in a channel ---
        @self.app.event("app_mention")
        def handle_mention(event, say, context):
            # Return IMMEDIATELY so Slack gets ack — process in background
            if self._is_duplicate_event(event):
                return
            _executor.submit(self._handle_event, event, say, context)

        # --- Event: Direct message to bot ---
        @self.app.event("message")
        def handle_message(event, say, context):
            # Ignore bot's own messages and message_changed events
            if event.get("subtype") in (
                "bot_message",
                "message_changed",
                "message_deleted",
            ):
                return
            if event.get("bot_id"):
                return
            # Ignore if this is from the bot itself
            if self._bot_user_id and event.get("user") == self._bot_user_id:
                return
            # Skip channel messages that contain a bot mention —
            # those are handled by the app_mention handler above.
            # Only process DMs here (channel_type == "im").
            channel_type = event.get("channel_type", "")
            if channel_type != "im":
                return
            if self._is_duplicate_event(event):
                return
            # Return IMMEDIATELY — process in background
            _executor.submit(self._handle_event, event, say, context)

        self.handler = SocketModeHandler(self.app, self.app_token)

    def _handle_event(self, event: dict, say, context):
        """Process an incoming Slack event (runs in background thread)."""
        try:
            team_id = context.get("team_id", None) if context else None
            message = self.adapter.normalise_slack(event, team_id=team_id)

            if not message.text:
                return

            if message.client_id == "unknown":
                say("⚠️ Could not identify your workspace. Please contact support.")
                return

            client_config = self.client_configs.get(message.client_id)
            if not client_config:
                say(f"⚠️ No configuration found for client: {message.client_id}")
                return

            # Run the orchestrator pipeline (async in sync context)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    self.orchestrator.process(message, client_config)
                )
                say(result["response"])
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Slack event handler error: {e}", exc_info=True)
            say(
                "❌ I encountered an error processing your request. "
                "Please try again or contact support."
            )

    def start(self):
        """Start the Slack bot in a background thread."""
        global _active_handler

        if not self._validate_tokens():
            logger.info("Slack bot not started — missing tokens")
            return

        # Stop any existing handler to prevent duplicate Socket Mode connections
        if _active_handler is not None:
            logger.info("Stopping previous Slack handler before starting new one")
            _active_handler.stop()
            _active_handler = None

        try:
            self._setup_app()

            def _run():
                logger.info("Starting Slack bot (Socket Mode)...")
                self.handler.start()

            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
            _active_handler = self
            logger.info("Slack bot started in background thread")

        except Exception as e:
            logger.error(f"Failed to start Slack bot: {e}")

    def stop(self):
        """Stop the Slack bot."""
        if self.handler:
            try:
                self.handler.close()
                logger.info("Slack bot stopped")
            except Exception as e:
                logger.warning(f"Error stopping Slack bot: {e}")
