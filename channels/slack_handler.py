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

# Module-level reference to prevent multiple handlers
_active_handler = None


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

        self.bot_token = settings.slack_bot_token
        self.app_token = settings.slack_app_token
        self.signing_secret = settings.slack_signing_secret

        self.app = None
        self.handler = None

        # Deduplication: track recently processed message timestamps.
        # Slack Socket Mode retries deliver different envelope_ids but
        # the underlying message "ts" stays the same.
        self._processed_ts: Set[str] = set()
        self._processed_lock = threading.Lock()

        # Per-user processing lock: prevents duplicate responses when
        # Slack delivers the same event multiple times before dedup catches it.
        # Only one message per user is processed at a time.
        self._busy_users: Set[str] = set()
        self._busy_lock = threading.Lock()

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

    def _is_duplicate(self, event: dict) -> bool:
        """
        De-duplicate events using the message 'ts' field.

        When Slack retries an event via Socket Mode, it delivers a new
        envelope with a different envelope_id/event_ts, but the underlying
        message timestamp ('ts') is identical. We key on 'ts' to catch retries.
        """
        ts = event.get("ts", "")
        if not ts:
            logger.info(f"DEDUP: No ts in event, skipping dedup check")
            return False

        with self._processed_lock:
            if ts in self._processed_ts:
                logger.info(f"DEDUP: Duplicate detected ts={ts}")
                return True
            self._processed_ts.add(ts)
            logger.info(f"DEDUP: New event registered ts={ts}")

            # Prune old entries
            if len(self._processed_ts) > 500:
                self._processed_ts = set(list(self._processed_ts)[-200:])

        return False

    def _acquire_user(self, user_id: str) -> bool:
        """
        Try to acquire the per-user processing lock.
        Returns True if acquired (caller should process), False if already busy.
        """
        with self._busy_lock:
            if user_id in self._busy_users:
                logger.info(f"DEDUP: User {user_id} already being processed, skipping")
                return False
            self._busy_users.add(user_id)
            logger.info(f"DEDUP: Acquired user {user_id}")
            return True

    def _release_user(self, user_id: str):
        """Release the per-user processing lock."""
        with self._busy_lock:
            self._busy_users.discard(user_id)
            logger.info(f"DEDUP: Released user {user_id}")

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
            user_id = event.get("user", "")
            ts = event.get("ts", "")
            text = event.get("text", "")[:120]
            channel = event.get("channel", "")
            logger.info(
                f"[app_mention] ts={ts} user={user_id} channel={channel} text={text}"
            )
            if self._is_duplicate(event):
                return
            if not self._acquire_user(user_id):
                return
            logger.info(f"[app_mention] Submitting to thread pool for user={user_id}")
            _executor.submit(self._handle_event_safe, event, say, context)

        # --- Event: Direct message to bot ---
        @self.app.event("message")
        def handle_message(event, say, context):
            # Ignore bot's own messages and subtypes
            if event.get("subtype") in (
                "bot_message",
                "message_changed",
                "message_deleted",
            ):
                return
            if event.get("bot_id"):
                return
            if self._bot_user_id and event.get("user") == self._bot_user_id:
                return
            # Only DMs — channel mentions are handled by app_mention
            if event.get("channel_type", "") != "im":
                return

            user_id = event.get("user", "")
            ts = event.get("ts", "")
            text = event.get("text", "")[:120]
            logger.info(f"[message/DM] ts={ts} user={user_id} text={text}")
            if self._is_duplicate(event):
                return
            if not self._acquire_user(user_id):
                return
            logger.info(f"[message/DM] Submitting to thread pool for user={user_id}")
            _executor.submit(self._handle_event_safe, event, say, context)

        self.handler = SocketModeHandler(self.app, self.app_token)

    def _handle_event_safe(self, event: dict, say, context):
        """Wrapper that catches all exceptions and releases user lock."""
        user_id = event.get("user", "")
        event_ts = event.get("ts", "")
        text_preview = event.get("text", "")[:80]
        logger.info(
            f"[handle_event_safe] START user={user_id} ts={event_ts} text={text_preview}"
        )
        try:
            self._handle_event(event, say, context)
            logger.info(f"[handle_event_safe] DONE user={user_id} ts={event_ts}")
        except Exception as e:
            logger.error(
                f"[handle_event_safe] UNHANDLED ERROR user={user_id}: {e}",
                exc_info=True,
            )
            try:
                say(
                    "❌ I encountered an error processing your request. Please try again."
                )
            except Exception as say_err:
                logger.error(
                    f"[handle_event_safe] Failed to send error to Slack: {say_err}"
                )
        finally:
            self._release_user(user_id)

    def _handle_event(self, event: dict, say, context):
        """Process an incoming Slack event (runs in background thread)."""
        team_id = context.get("team_id", None) if context else None
        message = self.adapter.normalise_slack(event, team_id=team_id)
        logger.info(
            f"[handle_event] normalised: client_id={message.client_id} "
            f"text={message.text[:60] if message.text else '(empty)'}"
        )

        if not message.text:
            logger.info("[handle_event] empty text, returning")
            return

        if message.client_id == "unknown":
            say("⚠️ Could not identify your workspace. Please contact support.")
            return

        client_config = self.client_configs.get(message.client_id)
        if not client_config:
            say(f"⚠️ No configuration found for client: {message.client_id}")
            return

        logger.info("[handle_event] calling orchestrator...")
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self.orchestrator.process(message, client_config)
            )
            response_text = result.get("response", "")
            if not response_text:
                response_text = "I wasn't able to generate a response. Please try rephrasing your question."

            logger.info(
                f"[handle_event] orchestrator done, response={len(response_text)} chars, "
                f"qa_score={result.get('qa_score', 0):.2f}, retries={result.get('retries', 0)}"
            )

            # Slack has a 4000 char limit for messages; split if needed
            if len(response_text) > 3900:
                chunks = [
                    response_text[i : i + 3900]
                    for i in range(0, len(response_text), 3900)
                ]
                for i, chunk in enumerate(chunks):
                    say(chunk)
                    logger.info(f"[handle_event] sent chunk {i + 1}/{len(chunks)}")
            else:
                say(response_text)
            logger.info("[handle_event] say() called successfully")
        except Exception as e:
            logger.error(f"Slack event handler error: {e}", exc_info=True)
            error_msg = (
                "❌ I encountered an error processing your request. "
                "Please try again or contact support."
            )
            try:
                say(error_msg)
            except Exception as say_err:
                logger.error(f"Failed to send error message to Slack: {say_err}")
        finally:
            loop.close()

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
