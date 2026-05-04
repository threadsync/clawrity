"""
Clawrity — Base Data Connector

Abstract interface for data connectors.
All connectors implement load() → pd.DataFrame.
"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseConnector(ABC):
    """Abstract base class for data source connectors."""

    @abstractmethod
    def load(self, path: str, **kwargs) -> pd.DataFrame:
        """
        Load data from the source.

        Args:
            path: Path to the data source
            **kwargs: Additional arguments specific to the connector

        Returns:
            pandas DataFrame with loaded data
        """
        pass

    @abstractmethod
    def validate(self, df: pd.DataFrame, required_columns: list) -> bool:
        """
        Validate that the DataFrame has expected columns.

        Args:
            df: DataFrame to validate
            required_columns: List of column names that must be present

        Returns:
            True if valid
        """
        pass
