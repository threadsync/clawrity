"""
Clawrity — Prophet Forecasting Engine

Trains Prophet models on branch-level monthly revenue time series.
Forecasts 6 months ahead. Caches results in PostgreSQL forecasts table.

Limitations (be explicit):
- Predicts revenue TRENDS only
- Does NOT claim ROI-per-dollar forecasting (spend→revenue is approximate)
- Requires minimum 2 years of data per branch
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from skills.postgres_connector import get_connector

logger = logging.getLogger(__name__)

MIN_MONTHS = 24  # Minimum 2 years of data
FORECAST_MONTHS = 6


class ProphetEngine:
    """Time series forecasting using Facebook Prophet."""

    def train_and_forecast(self, client_id: str) -> List[Dict]:
        """
        Train Prophet models for each branch and cache forecasts.

        Args:
            client_id: Client to forecast for

        Returns:
            List of forecast result dicts (one per branch)
        """
        from prophet import Prophet

        db = get_connector()

        # Get monthly revenue per branch
        sql = """
            SELECT branch, country,
                   DATE_TRUNC('month', date) AS month,
                   SUM(revenue) AS monthly_revenue
            FROM spend_data
            WHERE client_id = %s
            GROUP BY branch, country, DATE_TRUNC('month', date)
            ORDER BY branch, month
        """
        df = db.execute_query(sql, (client_id,))

        if df.empty:
            logger.warning(f"No data for forecasting: {client_id}")
            return []

        results = []
        branches = df.groupby(["branch", "country"])

        for (branch, country), group in branches:
            group = group.sort_values("month").reset_index(drop=True)

            if len(group) < MIN_MONTHS:
                logger.info(
                    f"Skipping {branch} ({country}): only {len(group)} months "
                    f"(need {MIN_MONTHS})"
                )
                continue

            try:
                # Prepare Prophet format: ds (date), y (value)
                prophet_df = pd.DataFrame({
                    "ds": pd.to_datetime(group["month"]),
                    "y": group["monthly_revenue"].astype(float),
                })

                # Train
                model = Prophet(
                    yearly_seasonality=True,
                    weekly_seasonality=False,
                    daily_seasonality=False,
                )
                model.fit(prophet_df)

                # Forecast
                future = model.make_future_dataframe(
                    periods=FORECAST_MONTHS, freq="MS"
                )
                forecast = model.predict(future)

                # Extract forecast period only
                forecast_only = forecast.tail(FORECAST_MONTHS)

                forecast_data = {
                    "branch": branch,
                    "country": country,
                    "horizon_months": FORECAST_MONTHS,
                    "dates": forecast_only["ds"].dt.strftime("%Y-%m-%d").tolist(),
                    "forecast_revenue": forecast_only["yhat"].round(2).tolist(),
                    "lower_bound": forecast_only["yhat_lower"].round(2).tolist(),
                    "upper_bound": forecast_only["yhat_upper"].round(2).tolist(),
                    "computed_at": datetime.utcnow().isoformat(),
                }

                # Cache in PostgreSQL
                self._cache_forecast(client_id, forecast_data)
                results.append(forecast_data)

                logger.info(
                    f"Forecast generated for {branch} ({country}): "
                    f"{FORECAST_MONTHS} months ahead"
                )

            except Exception as e:
                logger.error(f"Prophet failed for {branch} ({country}): {e}")

        logger.info(f"Forecasting complete: {len(results)} branches forecast")
        return results

    def get_cached_forecast(
        self,
        client_id: str,
        branch: str,
    ) -> Optional[Dict]:
        """Get the most recent cached forecast for a branch."""
        db = get_connector()

        sql = """
            SELECT forecast_data, computed_at
            FROM forecasts
            WHERE client_id = %s AND branch = %s
            ORDER BY computed_at DESC
            LIMIT 1
        """
        rows = db.execute_raw(sql, (client_id, branch))

        if not rows:
            return None

        row = rows[0]
        data = row["forecast_data"]
        if isinstance(data, str):
            data = json.loads(data)

        data["computed_at"] = str(row["computed_at"])
        return data

    def _cache_forecast(self, client_id: str, forecast_data: Dict):
        """Store forecast in PostgreSQL."""
        db = get_connector()

        # Delete old forecast for this branch
        db.execute_write(
            "DELETE FROM forecasts WHERE client_id = %s AND branch = %s AND country = %s",
            (client_id, forecast_data["branch"], forecast_data["country"]),
        )

        # Insert new
        db.execute_write(
            """INSERT INTO forecasts (client_id, branch, country, horizon_months, forecast_data)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                client_id,
                forecast_data["branch"],
                forecast_data["country"],
                forecast_data["horizon_months"],
                json.dumps(forecast_data),
            ),
        )
