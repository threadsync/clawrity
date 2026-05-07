"""
Clawrity — HEARTBEAT Scheduler

APScheduler AsyncIOScheduler fires digest jobs per client at configured times.
Schedule: ETL at 02:00 --> RAG re-index at 03:00 --> Digest at configured time.
Retry: on failure, retry after N minutes, max retries from HEARTBEAT.md.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.orchestrator import Orchestrator
from channels.protocol_adapter import NormalisedMessage
from config.client_loader import ClientConfig
from config.settings import get_settings
from heartbeat.heartbeat_loader import load_heartbeat
from skills.postgres_connector import get_connector
from soul.soul_loader import load_soul

logger = logging.getLogger(__name__)


async def run_digest(
    client_config: ClientConfig,
    orchestrator: Orchestrator,
    retry_count: int = 0,
) -> Optional[str]:
    """
    Run the daily digest for a client.

    Steps:
    1. Query bottom 3 branches by revenue (last 7 days)
    2. Gen Agent → QA Agent pipeline for digest
    3. Scout Agent for competitor/sector news
    4. Push to Slack webhook
    5. Log success/failure to JSONL

    Returns:
        Full digest text if successful, None on failure
    """
    from agents.gen_agent import GenAgent
    from agents.qa_agent import QAAgent

    client_id = client_config.client_id
    logger.info(f"[{client_id}] Running daily digest (attempt {retry_count + 1})")

    db = get_connector()

    try:
        # Step 1: Get bottom 3 branches by revenue with ROI
        bottom_sql = """
            SELECT branch, country, 
                   SUM(revenue) as total_revenue,
                   SUM(spend) as total_spend,
                   SUM(leads) as total_leads,
                   ROUND((SUM(revenue)/NULLIF(SUM(spend),0))::numeric, 2) as roi
            FROM spend_data
            WHERE client_id = %s
              AND date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY branch, country
            ORDER BY total_revenue ASC
            LIMIT 3
        """
        data = db.execute_query(bottom_sql, (client_id,))

        # Step 2: Generate digest via Gen Agent with specific prompt
        soul_content = load_soul(client_config)
        gen_agent = GenAgent()
        qa_agent = QAAgent()

        # Retrieve RAG chunks for digest context
        rag_chunks = None
        if orchestrator.retriever:
            try:
                rag_chunks = orchestrator.retriever.retrieve(
                    query="weekly performance bottom performers budget recommendations",
                    client_id=client_id,
                )
            except Exception as e:
                logger.warning(f"RAG retrieval for digest failed: {e}")

        # Generate digest with explicit prompt
        digest = gen_agent.generate(
            question="Generate morning business digest. Highlight bottom 3 branches. Suggest where to focus budget. Newsletter style.",
            soul_content=soul_content,
            data_context=data,
            rag_chunks=rag_chunks,
        )

        # Step 2b: QA pass on digest (more lenient threshold for digest)
        qa_result = qa_agent.evaluate(
            response=digest,
            data_context=data,
            threshold=0.6,  # More lenient for digest
        )

        if not qa_result["passed"]:
            logger.warning(
                f"[{client_id}] Digest QA failed (score={qa_result['score']:.2f}), "
                f"retrying with strict instruction"
            )
            # Retry digest generation with strict instruction
            digest = gen_agent.generate(
                question="Generate morning business digest. Highlight bottom 3 branches. Suggest where to focus budget. Newsletter style.",
                soul_content=soul_content,
                data_context=data,
                rag_chunks=rag_chunks,
                retry_issues=qa_result["issues"],
                retry_count=1,
                strict_data_instruction=(
                    "CRITICAL: Only mention branches and figures that appear in the "
                    "Data Context. Do not reference any other branches or historical data."
                ),
            )


        # Step 3: Assemble full digest
        full_digest = f"**Clawrity Daily Digest -- {client_config.client_name}**\n"
        full_digest += f"*{datetime.now().strftime('%B %d, %Y')}*\n\n"
        full_digest += digest

        # Step 5: Push to Slack webhook
        webhook_url = client_config.channels.get("slack_webhook", "")
        if webhook_url and webhook_url.startswith(("http://", "https://")):
            await _push_to_slack(webhook_url, full_digest)
        elif webhook_url:
            logger.warning(
                f"[{client_id}] Slack webhook URL is malformed (missing http/https protocol): "
                f"{webhook_url[:50]}..."
            )
        else:
            logger.warning(f"[{client_id}] No Slack webhook configured")

        # Step 6: Log success to JSONL
        _log_digest_event(
            client_id,
            "success",
            {
                "qa_score": qa_result["score"],
                "qa_passed": qa_result["passed"],
                "digest_length": len(full_digest),
            },
        )

        logger.info(f"[{client_id}] Digest completed successfully")
        return full_digest

    except Exception as e:
        logger.error(f"[{client_id}] Digest failed: {e}", exc_info=True)
        _log_digest_event(
            client_id, "failure", {"error": str(e), "attempt": retry_count + 1}
        )

        heartbeat = load_heartbeat(client_config)

        if retry_count < heartbeat.max_retries:
            delay_minutes = heartbeat.retry_delay_minutes
            logger.info(
                f"[{client_id}] Scheduling digest retry in {delay_minutes} minutes "
                f"(attempt {retry_count + 2}/{heartbeat.max_retries + 1})"
            )
            await asyncio.sleep(delay_minutes * 60)
            return await run_digest(client_config, orchestrator, retry_count + 1)
        else:
            logger.error(
                f"[{client_id}] Digest failed after {heartbeat.max_retries + 1} attempts"
            )
            # Post failure notification to Slack
            webhook_url = client_config.channels.get("slack_webhook", "")
            if webhook_url and webhook_url.startswith(("http://", "https://")):
                await _push_to_slack(
                    webhook_url, "Clawrity digest unavailable. Backend may be offline."
                )
            return None


async def _push_to_slack(webhook_url: str, message: str):
    """Push a message to a Slack incoming webhook."""
    if not webhook_url or not webhook_url.startswith(("http://", "https://")):
        logger.error(
            f"Invalid Slack webhook URL: {webhook_url[:50] if webhook_url else '(empty)'}"
        )
        return
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json={"text": message},
                timeout=30,
            )
            if response.status_code == 200:
                logger.info("Digest pushed to Slack successfully")
            else:
                logger.error(
                    f"Slack webhook returned {response.status_code}: {response.text}"
                )
    except Exception as e:
        logger.error(f"Failed to push digest to Slack: {e}")


def _log_digest_event(client_id: str, status: str, details: dict):
    """Log digest event to JSONL monitoring file."""
    settings = get_settings()
    logs_dir = settings.logs_dir
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"{client_id}_digest.jsonl")

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "client_id": client_id,
        "event": "digest",
        "status": status,
        **details,
    }

    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to log digest event: {e}")


def start_scheduler(
    client_configs: Dict[str, ClientConfig],
    orchestrator: Orchestrator,
) -> AsyncIOScheduler:
    """
    Start the APScheduler with digest jobs for all clients.

    Schedule per client:
    - Digest at configured time (from HEARTBEAT.md)
    - ETL sync at 02:00 (placeholder)
    - RAG re-index at 03:00 (placeholder)
    """
    scheduler = AsyncIOScheduler()

    for client_id, config in client_configs.items():
        heartbeat = load_heartbeat(config)

        # Daily digest at configured time
        scheduler.add_job(
            run_digest,
            CronTrigger(
                hour=heartbeat.hour,
                minute=heartbeat.minute,
                timezone=heartbeat.timezone,
            ),
            args=[config, orchestrator],
            id=f"digest_{client_id}",
            name=f"Daily Digest — {config.client_name}",
            replace_existing=True,
        )
        logger.info(
            f"Scheduled digest for {client_id}: {heartbeat.time} {heartbeat.timezone}"
        )

        # ETL sync at 02:00 (placeholder)
        scheduler.add_job(
            _etl_sync_placeholder,
            CronTrigger(hour=2, minute=0, timezone=heartbeat.timezone),
            args=[client_id],
            id=f"etl_{client_id}",
            name=f"ETL Sync — {config.client_name}",
            replace_existing=True,
        )

        # RAG re-index at 03:00 (placeholder)
        scheduler.add_job(
            _rag_reindex_placeholder,
            CronTrigger(hour=3, minute=0, timezone=heartbeat.timezone),
            args=[client_id],
            id=f"rag_reindex_{client_id}",
            name=f"RAG Re-index — {config.client_name}",
            replace_existing=True,
        )

    scheduler.start()
    return scheduler



async def _etl_sync_placeholder(client_id: str):
    """Placeholder for nightly ETL data sync."""
    logger.info(f"[{client_id}] ETL sync triggered (placeholder)")


async def _rag_reindex_placeholder(client_id: str):
    """Placeholder for nightly RAG re-indexing."""
    logger.info(f"[{client_id}] RAG re-index triggered (placeholder)")
    try:
        from scripts.run_rag_pipeline import run_pipeline

        run_pipeline(client_id)
    except Exception as e:
        logger.warning(f"RAG re-index failed: {e}")
