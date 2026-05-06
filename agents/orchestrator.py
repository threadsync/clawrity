"""
Clawrity — Orchestrator

Coordinates the full message pipeline:
  NormalisedMessage → NL-to-SQL → PostgreSQL → (RAG Retriever) → Gen Agent → QA Agent → Response

Max 2 retries per query. Returns best attempt with confidence warning after max retries.

Context enrichment: when a query returns sparse data (≤3 rows) and the question
asks for recommendations, automatically pulls top-performing branches as comparison
context so the Gen Agent can give actionable suggestions.
"""

import asyncio
import re
import logging
import time
from typing import Dict, Optional, List

import pandas as pd

from agents.gen_agent import GenAgent
from agents.qa_agent import QAAgent
from channels.protocol_adapter import NormalisedMessage
from config.client_loader import ClientConfig
from skills.nl_to_sql import NLToSQL
from skills.postgres_connector import get_connector
from soul.soul_loader import load_soul

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

# Keywords that signal the user wants recommendations, not just raw data
_RECOMMENDATION_KEYWORDS = re.compile(
    r"\b(improve|increase|boost|grow|fix|help|recommend|suggest|advice|strategy|"
    r"what (should|can|do)|how (to|can|do|should))\b",
    re.IGNORECASE,
)


class Orchestrator:
    """Pipeline orchestrator — the central brain of Clawrity."""

    def __init__(self):
        self.nl_to_sql = NLToSQL()
        self.gen_agent = GenAgent()
        self.qa_agent = QAAgent()
        self.retriever = None  # Set in Phase 2 via set_retriever()

    def set_retriever(self, retriever):
        """Attach the RAG retriever (Phase 2)."""
        self.retriever = retriever

    async def process(
        self,
        message: NormalisedMessage,
        client_config: ClientConfig,
    ) -> Dict:
        """
        Process a user message through the full pipeline (async version for API endpoints).
        Runs the synchronous pipeline in a thread pool so it doesn't block the event loop.

        Returns:
            Dict with: response, qa_score, qa_passed, retries, metadata
        """
        return await asyncio.to_thread(self.process_sync, message, client_config)

    def process_sync(
        self,
        message: NormalisedMessage,
        client_config: ClientConfig,
    ) -> Dict:
        """
        Process a user message through the full pipeline (synchronous version).

        Returns:
            Dict with: response, qa_score, qa_passed, retries, metadata
        """
        start_time = time.time()
        db = get_connector()

        # Load SOUL
        soul_content = load_soul(client_config)

        # Step 1: NL-to-SQL
        schema_meta = db.get_spend_data_schema(client_config.client_id)
        sql = self.nl_to_sql.generate_sql(
            question=message.text,
            client_id=client_config.client_id,
            schema_metadata=schema_meta,
        )

        # Step 2: Execute SQL
        data_context = None
        if sql:
            try:
                data_context = db.execute_query(sql)
                logger.info(f"SQL returned {len(data_context)} rows")
            except Exception as e:
                logger.error(f"SQL execution failed: {e}")
                data_context = pd.DataFrame()
        else:
            data_context = pd.DataFrame()

        # Step 2b: Context enrichment for sparse results
        # When data is sparse and the user wants recommendations, pull
        # top performers and channel benchmarks as supplementary context
        supplementary_context = None
        if self._needs_enrichment(message.text, data_context):
            supplementary_context = self._enrich_context(
                db, client_config.client_id, message.text, data_context
            )
            if supplementary_context is not None:
                logger.info(
                    f"Context enriched: {len(supplementary_context)} supplementary rows"
                )

        # Step 3: RAG Retrieval (Phase 2)
        rag_chunks = None
        if self.retriever:
            try:
                rag_chunks = self.retriever.retrieve(
                    query=message.text,
                    client_id=client_config.client_id,
                )
            except Exception as e:
                logger.warning(f"RAG retrieval failed: {e}")

        # Step 4: Gen Agent → QA Agent loop (max 2 retries)
        # When supplementary context is provided (enrichment mode), use a relaxed
        # QA threshold since the response naturally references broader benchmark data
        qa_threshold = client_config.hallucination_threshold
        if supplementary_context is not None and len(supplementary_context) > 0:
            qa_threshold = min(qa_threshold, 0.5)
            logger.info(
                f"Using relaxed QA threshold ({qa_threshold}) for enriched context"
            )

        best_response = None
        best_score = 0.0
        qa_result = {"score": 0, "passed": False, "issues": []}
        retries = 0

        for attempt in range(MAX_RETRIES + 1):
            retry_issues = qa_result["issues"] if attempt > 0 else None

            # Always provide strict data grounding instruction to prevent
            # the Gen Agent from hallucinating branch/figure data from RAG
            # chunks that don't match the actual SQL query results.
            if supplementary_context is not None and len(supplementary_context) > 0:
                strict_data_instruction = (
                    "CRITICAL: Only use data from the Data Context and Benchmark Data "
                    "sections provided. Do NOT invent figures or branch names that are "
                    "not present in either of those sections. You MAY reference benchmark "
                    "branches for comparison and recommendations."
                )
            else:
                strict_data_instruction = (
                    "CRITICAL: Do NOT mention any branches, figures, or historical data "
                    "that are not in the SQL query result provided. Stick strictly to the "
                    "data. If historical context from RAG is about different branches than "
                    "what the query returned, IGNORE that context entirely."
                )

            response = self.gen_agent.generate(
                question=message.text,
                soul_content=soul_content,
                data_context=data_context,
                rag_chunks=rag_chunks,
                retry_issues=retry_issues,
                retry_count=attempt,
                strict_data_instruction=strict_data_instruction,
                supplementary_context=supplementary_context,
                sql=sql,
            )

            qa_result = self.qa_agent.evaluate(
                response=response,
                data_context=data_context,
                threshold=qa_threshold,
                supplementary_context=supplementary_context,
                user_question=message.text,
                sql=sql,
            )

            # Track best response (prefer longer, richer responses over "no data" stubs)
            if qa_result["score"] > best_score or (
                qa_result["score"] == best_score
                and best_response is not None
                and len(response) > len(best_response)
            ):
                best_score = qa_result["score"]
                best_response = response

            if qa_result["passed"]:
                logger.info(f"QA passed on attempt {attempt + 1}")
                break
            else:
                retries += 1
                logger.warning(
                    f"QA failed on attempt {attempt + 1}: "
                    f"score={qa_result['score']:.2f}, issues={qa_result['issues']}"
                )

        # If max retries exceeded, use best response with confidence warning
        final_response = best_response or response
        if not qa_result["passed"] and retries >= MAX_RETRIES:
            final_response += (
                "\n\n---\n"
                f"⚠️ *Confidence: {best_score:.0%} — "
                f"This response may contain approximations. "
                f"Please verify critical numbers against your source data.*"
            )

        elapsed = time.time() - start_time

        result = {
            "response": final_response,
            "qa_score": best_score,
            "qa_passed": qa_result["passed"],
            "retries": retries,
            "sql": sql,
            "data_rows": len(data_context) if data_context is not None else 0,
            "rag_chunks_used": len(rag_chunks) if rag_chunks else 0,
            "elapsed_seconds": round(elapsed, 2),
        }

        # Log interaction
        self._log_interaction(message, client_config, result)

        return result

    def _needs_enrichment(
        self,
        question: str,
        data_context: Optional[pd.DataFrame],
    ) -> bool:
        """Check if the query result is too sparse for a recommendation question."""
        # Only enrich if data is sparse
        if data_context is not None and len(data_context) > 3:
            return False

        # Only enrich if user is asking for recommendations/improvement
        return bool(_RECOMMENDATION_KEYWORDS.search(question))

    def _enrich_context(
        self,
        db,
        client_id: str,
        question: str,
        data_context: Optional[pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        """
        Pull supplementary context: top-performing branches and channel
        benchmarks to help Gen Agent give actionable recommendations.
        """
        try:
            # Get top 5 branches by ROI for comparison
            enrichment_sql = """
                SELECT branch, country, channel,
                       SUM(spend) as total_spend,
                       SUM(revenue) as total_revenue,
                       SUM(leads) as total_leads,
                       SUM(conversions) as total_conversions,
                       ROUND((SUM(revenue)/NULLIF(SUM(spend),0))::numeric, 2) as roi
                FROM spend_data
                WHERE client_id = %s
                  AND date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY branch, country, channel
                HAVING SUM(spend) > 0
                ORDER BY roi DESC
                LIMIT 10
            """
            top_performers = db.execute_query(enrichment_sql, (client_id,))

            if top_performers is not None and len(top_performers) > 0:
                logger.info(
                    f"Enrichment: fetched {len(top_performers)} top performer rows"
                )
                return top_performers

        except Exception as e:
            logger.warning(f"Context enrichment failed: {e}")

        return None

    def _log_interaction(
        self,
        message: NormalisedMessage,
        client_config: ClientConfig,
        result: Dict,
    ):
        """Log interaction for monitoring."""
        try:
            from rag.monitoring import log_interaction

            log_interaction(
                client_id=client_config.client_id,
                query=message.text,
                num_chunks=result.get("rag_chunks_used", 0),
                chunk_types_used=[],  # Populated when retriever provides this info
                qa_score=result.get("qa_score", 0),
                qa_passed=result.get("qa_passed", False),
                retries=result.get("retries", 0),
                response_length=len(result.get("response", "")),
                elapsed_seconds=result.get("elapsed_seconds", 0),
            )
        except Exception as e:
            logger.debug(f"Monitoring log failed: {e}")

        logger.info(
            f"[{client_config.client_id}] Query processed: "
            f"score={result['qa_score']:.2f}, passed={result['qa_passed']}, "
            f"retries={result['retries']}, time={result['elapsed_seconds']}s"
        )
