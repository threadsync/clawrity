"""
Clawrity — PostgreSQL + pgvector Connector

Connection pool management, schema initialization, and query execution.
Single database handles both structured queries (NL-to-SQL) and vector search (pgvector).
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

INIT_SCHEMA_SQL = """
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Structured business data (replaces BigQuery)
CREATE TABLE IF NOT EXISTS spend_data (
    id          SERIAL PRIMARY KEY,
    date        DATE,
    country     VARCHAR(100),
    branch      VARCHAR(100),
    channel     VARCHAR(100),
    spend       FLOAT,
    revenue     FLOAT,
    leads       INT,
    conversions INT,
    client_id   VARCHAR(100)
);

-- Vector embeddings (replaces ChromaDB)
CREATE TABLE IF NOT EXISTS embeddings (
    id          VARCHAR(200) PRIMARY KEY,
    client_id   VARCHAR(100),
    chunk_type  VARCHAR(50),
    text        TEXT,
    metadata    JSONB,
    embedding   vector(384)
);

-- Forecast cache
CREATE TABLE IF NOT EXISTS forecasts (
    id              SERIAL PRIMARY KEY,
    client_id       VARCHAR(100),
    branch          VARCHAR(100),
    country         VARCHAR(100),
    horizon_months  INT,
    forecast_data   JSONB,
    computed_at     TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_spend_data_client
    ON spend_data (client_id);
CREATE INDEX IF NOT EXISTS idx_spend_data_date
    ON spend_data (client_id, date);
CREATE INDEX IF NOT EXISTS idx_embeddings_client_type
    ON embeddings (client_id, chunk_type);
CREATE INDEX IF NOT EXISTS idx_forecasts_client
    ON forecasts (client_id, branch, country);
"""

# IVFFlat index requires rows to exist — created separately after data load
IVFFLAT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_embeddings_cosine
    ON embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
"""


class PostgresConnector:
    """PostgreSQL + pgvector connection manager."""

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or get_settings().database_url
        self._conn: Optional[psycopg2.extensions.connection] = None

    def _get_connection(self) -> psycopg2.extensions.connection:
        """Get or create a database connection with retry logic."""
        if self._conn is None or self._conn.closed:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self._conn = psycopg2.connect(self.database_url)
                    register_vector(self._conn)
                    logger.info("Connected to PostgreSQL with pgvector support")
                    return self._conn
                except psycopg2.OperationalError as e:
                    wait = 2**attempt
                    logger.warning(
                        f"DB connection attempt {attempt + 1}/{max_retries} failed: {e}. "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)
            raise ConnectionError("Failed to connect to PostgreSQL after 3 attempts")
        return self._conn

    def close(self):
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("PostgreSQL connection closed")

    def init_schema(self):
        """Create tables and extensions if they don't exist."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(INIT_SCHEMA_SQL)
            conn.commit()
            logger.info("Database schema initialized successfully")
        except Exception as e:
            conn.rollback()
            logger.error(f"Schema initialization failed: {e}")
            raise

    def create_vector_index(self):
        """Create IVFFlat index — call AFTER data has been loaded into embeddings."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(IVFFLAT_INDEX_SQL)
            conn.commit()
            logger.info("IVFFlat vector index created")
        except Exception as e:
            conn.rollback()
            logger.warning(f"Could not create IVFFlat index (may need more rows): {e}")

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(self, sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
        """
        Execute a SELECT query and return results as a DataFrame.

        Args:
            sql: SQL query string (must be SELECT only)
            params: Query parameters for parameterised queries

        Returns:
            pandas DataFrame with query results
        """
        conn = self._get_connection()
        try:
            df = pd.read_sql_query(sql, conn, params=params)
            conn.rollback()
            logger.debug(f"Query returned {len(df)} rows")
            return df
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            conn.rollback()
            raise

    def execute_raw(self, sql: str, params: Optional[tuple] = None) -> List[Dict]:
        """Execute a query and return raw dictionaries."""
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if cur.description:
                    results = [dict(row) for row in cur.fetchall()]
                    conn.rollback()
                    return results
                conn.commit()
                return []
        except Exception as e:
            conn.rollback()
            logger.error(f"Raw query execution failed: {e}")
            raise

    def execute_write(self, sql: str, params: Optional[tuple] = None):
        """Execute an INSERT/UPDATE/DELETE statement."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Write execution failed: {e}")
            raise

    def execute_batch(self, sql: str, data: List[tuple], page_size: int = 1000):
        """Execute a batch INSERT using execute_values for performance."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, data, page_size=page_size)
            conn.commit()
            logger.info(f"Batch insert: {len(data)} rows")
        except Exception as e:
            conn.rollback()
            logger.error(f"Batch execution failed: {e}")
            raise

    # ------------------------------------------------------------------
    # pgvector operations
    # ------------------------------------------------------------------

    def upsert_embeddings(self, embeddings_data: List[Dict[str, Any]]):
        """
        Upsert embedding records into the embeddings table.

        Args:
            embeddings_data: List of dicts with keys:
                id, client_id, chunk_type, text, metadata, embedding
        """
        conn = self._get_connection()
        sql = """
            INSERT INTO embeddings (id, client_id, chunk_type, text, metadata, embedding)
            VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                text = EXCLUDED.text,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding
        """
        data = [
            (
                d["id"],
                d["client_id"],
                d["chunk_type"],
                d["text"],
                psycopg2.extras.Json(d["metadata"]),
                np.array(d["embedding"]),
            )
            for d in embeddings_data
        ]
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, data, page_size=100)
            conn.commit()
            logger.info(f"Upserted {len(data)} embeddings")
        except Exception as e:
            conn.rollback()
            logger.error(f"Embedding upsert failed: {e}")
            raise

    def search_embeddings(
        self,
        query_embedding: np.ndarray,
        client_id: str,
        chunk_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        """
        Search for similar embeddings using pgvector cosine similarity.

        Args:
            query_embedding: Query vector (384 dims)
            client_id: Filter by client
            chunk_type: Optional filter by chunk type
            top_k: Number of results to return

        Returns:
            List of dicts with text, metadata, and similarity score
        """
        conn = self._get_connection()
        query_vec = np.array(query_embedding)

        if chunk_type:
            sql = """
                SELECT text, metadata, 1 - (embedding <=> %s) AS similarity
                FROM embeddings
                WHERE client_id = %s AND chunk_type = %s
                ORDER BY embedding <=> %s
                LIMIT %s
            """
            params = (query_vec, client_id, chunk_type, query_vec, top_k)
        else:
            sql = """
                SELECT text, metadata, 1 - (embedding <=> %s) AS similarity
                FROM embeddings
                WHERE client_id = %s
                ORDER BY embedding <=> %s
                LIMIT %s
            """
            params = (query_vec, client_id, query_vec, top_k)

        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                results = [dict(row) for row in cur.fetchall()]
            logger.debug(f"Vector search returned {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            raise

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_table_count(self, table: str, client_id: Optional[str] = None) -> int:
        """Get row count for a table, optionally filtered by client_id."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                if client_id:
                    cur.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE client_id = %s",
                        (client_id,),
                    )
                else:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"Count query failed: {e}")
            return 0

    def get_spend_data_schema(self, client_id: str) -> Dict:
        """Get metadata about available data for a client — used by NL-to-SQL."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT country FROM spend_data WHERE client_id = %s ORDER BY country",
                    (client_id,),
                )
                countries = [row[0] for row in cur.fetchall()]

                cur.execute(
                    "SELECT DISTINCT branch FROM spend_data WHERE client_id = %s ORDER BY branch",
                    (client_id,),
                )
                branches = [row[0] for row in cur.fetchall()]

                cur.execute(
                    "SELECT DISTINCT channel FROM spend_data WHERE client_id = %s ORDER BY channel",
                    (client_id,),
                )
                channels = [row[0] for row in cur.fetchall()]

                cur.execute(
                    "SELECT MIN(date), MAX(date) FROM spend_data WHERE client_id = %s",
                    (client_id,),
                )
                date_range = cur.fetchone()

            return {
                "countries": countries,
                "branches": branches,
                "channels": channels,
                "date_min": str(date_range[0]) if date_range[0] else None,
                "date_max": str(date_range[1]) if date_range[1] else None,
            }
        except Exception as e:
            logger.error(f"Schema metadata query failed: {e}")
            return {
                "countries": [],
                "branches": [],
                "channels": [],
                "date_min": None,
                "date_max": None,
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_connector: Optional[PostgresConnector] = None


def get_connector() -> PostgresConnector:
    """Get the shared PostgresConnector singleton."""
    global _connector
    if _connector is None:
        _connector = PostgresConnector()
    return _connector
