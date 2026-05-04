"""
Clawrity — RAG Vector Store

Embeds chunks using sentence-transformers all-MiniLM-L6-v2 (CPU, 384 dims).
Stores and searches via pgvector in PostgreSQL.
"""

import logging
from typing import List, Optional

import numpy as np

from rag.chunker import Chunk
from skills.postgres_connector import get_connector

logger = logging.getLogger(__name__)

_model = None


def _get_embedding_model():
    """Lazy-load the embedding model (CPU only, ~90MB)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded embedding model: all-MiniLM-L6-v2 (384 dims)")
    return _model


def embed_texts(texts: List[str], batch_size: int = 100) -> np.ndarray:
    """
    Embed a list of texts using MiniLM.

    Args:
        texts: List of text strings to embed
        batch_size: Batch size for encoding (default 100)

    Returns:
        numpy array of shape (len(texts), 384)
    """
    model = _get_embedding_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=len(texts) > 100,
        normalize_embeddings=True,
    )
    logger.info(f"Embedded {len(texts)} texts → shape {embeddings.shape}")
    return embeddings


def embed_query(query: str) -> np.ndarray:
    """Embed a single query string."""
    model = _get_embedding_model()
    return model.encode(query, normalize_embeddings=True)


def store_chunks(chunks: List[Chunk], embeddings: np.ndarray):
    """
    Upsert chunks + embeddings into pgvector.
    Uses ON CONFLICT DO UPDATE for safe nightly re-indexing.
    """
    seen = set()
    unique_chunks = []
    unique_embeddings = []
    for chunk, emb in zip(chunks, embeddings):
        if chunk.id not in seen:
            seen.add(chunk.id)
            unique_chunks.append(chunk)
            unique_embeddings.append(emb)
    chunks = unique_chunks
    embeddings = unique_embeddings

    db = get_connector()

    data = []
    for chunk, embedding in zip(chunks, embeddings):
        data.append({
            "id": chunk.id,
            "client_id": chunk.client_id,
            "chunk_type": chunk.chunk_type,
            "text": chunk.text,
            "metadata": chunk.metadata,
            "embedding": embedding.tolist(),
        })

    # Batch upsert
    batch_size = 100
    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        db.upsert_embeddings(batch)

    logger.info(f"Stored {len(data)} chunks in pgvector")

    # Try to create IVFFlat index (needs enough rows)
    try:
        db.create_vector_index()
    except Exception:
        pass


def search(
    query: str,
    client_id: str,
    chunk_type: Optional[str] = None,
    top_k: int = 5,
) -> List[dict]:
    """
    Search pgvector for similar chunks.

    Args:
        query: Natural language query
        client_id: Client to search within
        chunk_type: Optional filter (branch_weekly, channel_monthly, trend_qoq)
        top_k: Number of results

    Returns:
        List of dicts with text, metadata, similarity
    """
    query_embedding = embed_query(query)
    db = get_connector()

    results = db.search_embeddings(
        query_embedding=query_embedding,
        client_id=client_id,
        chunk_type=chunk_type,
        top_k=top_k,
    )

    logger.info(
        f"Vector search: query='{query[:50]}...', "
        f"chunk_type={chunk_type}, results={len(results)}"
    )
    return results
