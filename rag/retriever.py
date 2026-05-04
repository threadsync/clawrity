"""
Clawrity — RAG Retriever

Detects query intent → selects chunk_type → searches pgvector.
Intent detection based on keywords:
  - "should/recommend/allocate/shift" → trend_qoq
  - "channel/paid/email/social" → channel_monthly
  - everything else → branch_weekly
"""

import logging
import re
from typing import List, Dict, Optional

from rag.vector_store import search

logger = logging.getLogger(__name__)

# Intent → chunk_type mapping based on keywords
INTENT_PATTERNS = {
    "trend_qoq": [
        "should", "recommend", "allocate", "shift", "increase", "decrease",
        "budget", "realloc", "invest", "optimize", "growth", "trend",
        "quarter", "qoq", "forecast", "predict",
    ],
    "channel_monthly": [
        "channel", "paid", "email", "social", "search", "display",
        "organic", "referral", "campaign", "marketing", "roi",
        "spend", "advertising",
    ],
}


class Retriever:
    """RAG retriever with intent-based chunk type filtering."""

    def retrieve(
        self,
        query: str,
        client_id: str,
        top_k: int = 5,
        chunk_type_override: Optional[str] = None,
    ) -> List[Dict]:
        """
        Retrieve relevant chunks based on query intent.

        Args:
            query: User's natural language query
            client_id: Client to search within
            top_k: Number of chunks to retrieve
            chunk_type_override: Force a specific chunk type

        Returns:
            List of dicts with text, metadata, similarity
        """
        if chunk_type_override:
            chunk_type = chunk_type_override
        else:
            chunk_type = self._detect_intent(query)

        logger.info(f"Detected intent → chunk_type: {chunk_type}")

        results = search(
            query=query,
            client_id=client_id,
            chunk_type=chunk_type,
            top_k=top_k,
        )

        # If no results with the detected type, fall back to all types
        if not results:
            logger.info(f"No results for {chunk_type}, falling back to all types")
            results = search(
                query=query,
                client_id=client_id,
                chunk_type=None,
                top_k=top_k,
            )

        return results

    def _detect_intent(self, query: str) -> str:
        """Detect query intent from keywords."""
        query_lower = query.lower()

        scores = {}
        for chunk_type, keywords in INTENT_PATTERNS.items():
            score = sum(1 for kw in keywords if kw in query_lower)
            scores[chunk_type] = score

        # Return the chunk type with highest score, default to branch_weekly
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return "branch_weekly"
