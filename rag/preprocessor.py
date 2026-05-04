"""
Clawrity — RAG Preprocessor

Fetches data from PostgreSQL, cleans it for RAG chunking:
  - Removes nulls, outliers > 3 std devs, duplicates
  - Normalises string columns
"""

import logging
from typing import Optional

import pandas as pd

from etl.normaliser import remove_outliers
from skills.postgres_connector import get_connector

logger = logging.getLogger(__name__)


def preprocess_for_rag(
    client_id: str,
    days: int = 365,
) -> pd.DataFrame:
    """
    Fetch and preprocess data for RAG chunking.

    Args:
        client_id: Client to fetch data for
        days: Number of days of data to fetch (default 365)

    Returns:
        Clean DataFrame ready for chunking
    """
    db = get_connector()

    sql = """
        SELECT date, country, branch, channel, spend, revenue, leads, conversions
        FROM spend_data
        WHERE client_id = %s AND date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY date
    """
    # Can't parameterise interval directly, use string formatting for days
    safe_sql = f"""
        SELECT date, country, branch, channel, spend, revenue, leads, conversions
        FROM spend_data
        WHERE client_id = %s AND date >= CURRENT_DATE - INTERVAL '{int(days)} days'
        ORDER BY date
    """
    df = db.execute_query(safe_sql, (client_id,))
    logger.info(f"Fetched {len(df)} rows for RAG preprocessing")

    if df.empty:
        logger.warning(f"No data found for client {client_id}")
        return df

    # Remove rows with critical nulls
    critical_cols = ["date", "branch", "country", "revenue"]
    df = df.dropna(subset=[c for c in critical_cols if c in df.columns])

    # Remove outliers on numeric columns
    df = remove_outliers(df, ["spend", "revenue", "leads", "conversions"])

    # Clean strings
    for col in ["country", "branch", "channel"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.title()

    # Remove duplicates
    df = df.drop_duplicates()

    logger.info(f"Preprocessed: {len(df)} rows ready for chunking")
    return df
