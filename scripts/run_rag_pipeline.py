"""
Clawrity — RAG Pipeline Script

CLI to run the full RAG pipeline: preprocess → chunk → embed → store in pgvector.

Usage:
    python scripts/run_rag_pipeline.py --client_id acme_corp
"""

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.preprocessor import preprocess_for_rag
from rag.chunker import generate_chunks
from rag.vector_store import embed_texts, store_chunks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_pipeline(client_id: str, days: int = 365):
    """Run the full RAG pipeline for a client."""
    logger.info(f"=== RAG Pipeline: {client_id} ===")

    # Step 1: Preprocess
    logger.info("Step 1/4: Preprocessing data...")
    df = preprocess_for_rag(client_id, days=days)
    if df.empty:
        logger.error("No data to process. Run seed_demo_data.py first.")
        return

    # Step 2: Generate chunks
    logger.info("Step 2/4: Generating chunks...")
    chunks = generate_chunks(df, client_id)
    logger.info(f"Generated {len(chunks)} chunks")

    if not chunks:
        logger.error("No chunks generated.")
        return

    # Step 3: Embed
    logger.info("Step 3/4: Embedding chunks (CPU, batch_size=100)...")
    texts = [c.text for c in chunks]
    embeddings = embed_texts(texts, batch_size=100)

    # Step 4: Store in pgvector
    logger.info("Step 4/4: Upserting into pgvector...")
    store_chunks(chunks, embeddings)

    logger.info(f"=== RAG Pipeline complete: {len(chunks)} chunks indexed ===")


def main():
    parser = argparse.ArgumentParser(description="Run RAG pipeline")
    parser.add_argument("--client_id", required=True, help="Client ID")
    parser.add_argument("--days", type=int, default=365, help="Days of data to process")
    args = parser.parse_args()

    run_pipeline(args.client_id, args.days)


if __name__ == "__main__":
    main()
