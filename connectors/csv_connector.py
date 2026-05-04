"""
Clawrity — CSV/Excel Data Connector

Auto-detects file format based on extension:
  .csv → pandas read_csv
  .xlsx / .xls → pandas read_excel (via openpyxl)

Supports both formats since Kaggle datasets vary by download version.
"""

import logging
from pathlib import Path

import pandas as pd

from connectors.base_connector import BaseConnector

logger = logging.getLogger(__name__)


class CSVConnector(BaseConnector):
    """Connector for CSV and Excel files with auto-detection."""

    def load(self, path: str, **kwargs) -> pd.DataFrame:
        """
        Load data from a CSV or Excel file.
        Auto-detects format based on file extension.

        Args:
            path: Path to the file (.csv, .xlsx, .xls)
            **kwargs: Passed through to pandas read function.
                      Useful kwargs: sheet_name, encoding, sep

        Returns:
            pandas DataFrame
        """
        file_path = Path(path)

        if not file_path.exists():
            raise FileNotFoundError(f"Data file not found: {path}")

        ext = file_path.suffix.lower()

        if ext == ".csv":
            logger.info(f"Loading CSV: {path}")
            df = pd.read_csv(path, encoding='latin-1', **kwargs)
        elif ext in (".xlsx", ".xls"):
            logger.info(f"Loading Excel ({ext}): {path}")
            # Default to first sheet unless specified
            sheet_name = kwargs.pop("sheet_name", 0)
            df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl", **kwargs)

        else:
            raise ValueError(
                f"Unsupported file format: {ext}. "
                f"Supported: .csv, .xlsx, .xls"
            )

        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns from {file_path.name}")
        return df

    def validate(self, df: pd.DataFrame, required_columns: list) -> bool:
        """
        Validate that the DataFrame has all required columns.
        Uses case-insensitive matching.

        Args:
            df: DataFrame to validate
            required_columns: List of column names that must be present

        Returns:
            True if all required columns found
        """
        df_cols_lower = {col.lower().strip() for col in df.columns}
        missing = []

        for col in required_columns:
            if col.lower().strip() not in df_cols_lower:
                missing.append(col)

        if missing:
            logger.error(
                f"Missing required columns: {missing}. "
                f"Available: {list(df.columns)}"
            )
            return False

        return True
