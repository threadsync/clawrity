"""
Clawrity — RAG Monitoring

Logs every interaction to JSONL and provides aggregated stats.
Exposes data for /admin/stats/{client_id} endpoint.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)


def _log_path(client_id: str) -> str:
    """Get the JSONL log file path for a client."""
    logs_dir = get_settings().logs_dir
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, f"{client_id}_interactions.jsonl")


def log_interaction(
    client_id: str,
    query: str,
    num_chunks: int,
    chunk_types_used: list,
    qa_score: float,
    qa_passed: bool,
    retries: int,
    response_length: int,
    elapsed_seconds: float = 0.0,
):
    """Log an interaction to JSONL."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "client_id": client_id,
        "query": query,
        "num_chunks": num_chunks,
        "chunk_types_used": chunk_types_used,
        "qa_score": qa_score,
        "qa_passed": qa_passed,
        "retries": retries,
        "response_length": response_length,
        "elapsed_seconds": elapsed_seconds,
    }

    try:
        path = _log_path(client_id)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to log interaction: {e}")


def get_stats(client_id: str) -> Dict:
    """
    Get aggregated monitoring stats for a client.

    Returns:
        Dict with: total_queries, pass_rate, avg_qa_score, avg_retries,
                   queries_needing_retry
    """
    path = _log_path(client_id)
    if not os.path.exists(path):
        return {
            "client_id": client_id,
            "total_queries": 0,
            "pass_rate": 0.0,
            "avg_qa_score": 0.0,
            "avg_retries": 0.0,
            "queries_needing_retry": 0,
        }

    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return {"error": str(e)}

    if not entries:
        return {"client_id": client_id, "total_queries": 0}

    total = len(entries)
    passed = sum(1 for e in entries if e.get("qa_passed", False))
    scores = [e.get("qa_score", 0) for e in entries]
    retries = [e.get("retries", 0) for e in entries]
    retry_queries = sum(1 for r in retries if r > 0)

    return {
        "client_id": client_id,
        "total_queries": total,
        "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
        "avg_qa_score": round(sum(scores) / total, 3) if total > 0 else 0,
        "avg_retries": round(sum(retries) / total, 2) if total > 0 else 0,
        "queries_needing_retry": retry_queries,
    }
