"""
Clawrity — ETL Normaliser

Applies column mappings from client YAML, normalises data types,
cleans strings, and handles nulls.
"""

import logging
from typing import Dict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def normalise_dataframe(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    date_column: str = "date",
) -> pd.DataFrame:
    """Normalise a DataFrame using the client's column mapping."""
    df = df.copy()
    original_len = len(df)

    # Step 1: Apply column mapping (case-insensitive)
    df_cols_map = {col.strip(): col for col in df.columns}
    rename_map = {}
    for source, target in column_mapping.items():
        if source in df_cols_map:
            rename_map[df_cols_map[source]] = target
        else:
            for orig_col, actual_col in df_cols_map.items():
                if orig_col.lower() == source.lower():
                    rename_map[actual_col] = target
                    break
    if rename_map:
        df = df.rename(columns=rename_map)
        logger.info(f"Renamed columns: {rename_map}")

    # Step 2: Parse dates
    if date_column in df.columns:
        df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
        df = df.dropna(subset=[date_column])
        df[date_column] = df[date_column].dt.date

    # Step 3: Clean string columns
    for col in ["country", "branch", "channel"]:
        if col in df.columns:
            df[col] = (
                df[col].astype(str).str.strip().str.title()
                .replace({"Nan": None, "None": None, "": None})
            )

    # Step 4: Handle numeric nulls
    for col in ["spend", "revenue", "profit", "leads", "conversions"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Step 5: Remove duplicates
    df = df.drop_duplicates()
    dropped = original_len - len(df)
    if dropped > 0:
        logger.info(f"Removed {dropped} duplicate rows")

    logger.info(f"Normalisation complete: {len(df)} rows")
    return df


def remove_outliers(df: pd.DataFrame, columns: list, n_std: float = 3.0) -> pd.DataFrame:
    """Remove rows with values > n_std standard deviations from mean."""
    df = df.copy()
    original_len = len(df)
    for col in columns:
        if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
            mean, std = df[col].mean(), df[col].std()
            if std > 0:
                df = df[(df[col] - mean).abs() <= n_std * std]
    removed = original_len - len(df)
    if removed > 0:
        logger.info(f"Removed {removed} outlier rows (>{n_std} std devs)")
    return df
