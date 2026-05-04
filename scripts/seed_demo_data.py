"""
Clawrity — Demo Data Seeder

Merges Global Superstore + Marketing Campaign datasets with Faker gap-filling.
Inserts into PostgreSQL spend_data table.

Usage:
    python scripts/seed_demo_data.py --client_id acme_corp \
        --superstore data/raw/Global_Superstore2.csv \
        --marketing data/raw/marketing_campaign_dataset.csv
"""

import argparse
import logging
import random
import sys
import os

import numpy as np
import pandas as pd
from faker import Faker

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.csv_connector import CSVConnector
from etl.normaliser import normalise_dataframe
from skills.postgres_connector import PostgresConnector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

fake = Faker()
Faker.seed(42)
random.seed(42)
np.random.seed(42)

# Marketing channels to assign
CHANNELS = ["Paid Search", "Social Media", "Email", "Display", "Organic", "Referral"]

# Column mapping for Global Superstore
SUPERSTORE_MAPPING = {
    "Order Date": "date",
    "Country": "country",
    "City": "branch",
    "Sales": "revenue",
    "Profit": "profit",
}


def load_superstore(path: str) -> pd.DataFrame:
    """Load and normalize the Global Superstore dataset."""
    connector = CSVConnector()
    df = connector.load(path)
    logger.info(f"Superstore columns: {list(df.columns)}")

    # Apply column mapping
    df = normalise_dataframe(df, SUPERSTORE_MAPPING)

    # Keep only needed columns
    keep = ["date", "country", "branch", "revenue", "profit"]
    available = [c for c in keep if c in df.columns]
    df = df[available].copy()

    logger.info(f"Superstore: {len(df)} rows after normalisation")
    return df


def load_marketing(path: str) -> pd.DataFrame:
    """Load the Marketing Campaign Performance dataset."""
    connector = CSVConnector()
    df = connector.load(path)
    logger.info(f"Marketing columns: {list(df.columns)}")

    # Standardize column names
    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if "channel" in cl:
            col_map[col] = "channel"
        elif "spend" in cl or "budget" in cl:
            col_map[col] = "spend"
        elif "click" in cl:
            col_map[col] = "leads"
        elif "conversion" in cl:
            col_map[col] = "conversions"
        elif "roi" in cl:
            col_map[col] = "roi_raw"
        elif "impression" in cl:
            col_map[col] = "impressions"

    df = df.rename(columns=col_map)
    logger.info(f"Marketing: {len(df)} rows, mapped columns: {list(df.columns)}")
    return df


def merge_datasets(superstore: pd.DataFrame, marketing: pd.DataFrame) -> pd.DataFrame:
    """
    Merge superstore (base) with marketing channel metrics.
    Each superstore row gets a channel + spend/leads/conversions.
    """
    df = superstore.copy()

    # Assign channels proportionally from marketing data
    if "channel" in marketing.columns:
        channel_list = marketing["channel"].dropna().unique().tolist()
        if not channel_list:
            channel_list = CHANNELS
    else:
        channel_list = CHANNELS

    # Assign channel to each row (deterministic based on index)
    df["channel"] = [channel_list[i % len(channel_list)] for i in range(len(df))]

    # Build channel-level spend/leads/conversions stats from marketing data
    channel_stats = {}
    if "spend" in marketing.columns and "channel" in marketing.columns:
        for ch in channel_list:
            ch_data = marketing[marketing["channel"] == ch] if "channel" in marketing.columns else marketing
            channel_stats[ch] = {
                "avg_spend": ch_data["spend"].mean() if "spend" in ch_data.columns and len(ch_data) > 0 else 500,
                "avg_leads": ch_data["leads"].mean() if "leads" in ch_data.columns and len(ch_data) > 0 else 50,
                "avg_conv": ch_data["conversions"].mean() if "conversions" in ch_data.columns and len(ch_data) > 0 else 5,
            }

    # Fill spend, leads, conversions using marketing stats + Faker variation
    spends, leads_list, conv_list = [], [], []
    for _, row in df.iterrows():
        ch = row["channel"]
        stats = channel_stats.get(ch, {"avg_spend": 500, "avg_leads": 50, "avg_conv": 5})

        rev = row.get("revenue", 1000)
        # Spend: proportion of revenue with channel-based variation
        spend = max(10, rev * random.uniform(0.3, 0.6) + random.gauss(0, stats["avg_spend"] * 0.1))
        leads = max(1, int(spend / random.uniform(15, 40)))
        conversions = max(0, int(leads * random.uniform(0.05, 0.20)))

        spends.append(round(spend, 2))
        leads_list.append(leads)
        conv_list.append(conversions)

    df["spend"] = spends
    df["leads"] = leads_list
    df["conversions"] = conv_list

    # Drop profit column (not in spend_data schema)
    if "profit" in df.columns:
        df = df.drop(columns=["profit"])

    logger.info(f"Merged dataset: {len(df)} rows, columns: {list(df.columns)}")
    return df


def seed_to_postgres(df: pd.DataFrame, client_id: str):
    """Insert merged data into PostgreSQL spend_data table."""
    connector = PostgresConnector()
    connector.init_schema()

    # Clear existing data for this client
    connector.execute_write(
        "DELETE FROM spend_data WHERE client_id = %s", (client_id,)
    )
    logger.info(f"Cleared existing data for client: {client_id}")

    # Add client_id column
    df["client_id"] = client_id

    # Prepare batch insert
    sql = """
        INSERT INTO spend_data (date, country, branch, channel, spend, revenue, leads, conversions, client_id)
        VALUES %s
    """
    data = [
        (
            row["date"], row["country"], row["branch"], row["channel"],
            row["spend"], row["revenue"], row["leads"], row["conversions"],
            row["client_id"]
        )
        for _, row in df.iterrows()
    ]

    connector.execute_batch(sql, data, page_size=2000)

    count = connector.get_table_count("spend_data", client_id)
    logger.info(f"Seeded {count} rows into spend_data for client: {client_id}")

    # Save processed CSV
    os.makedirs("data/processed", exist_ok=True)
    output_path = f"data/processed/{client_id}_merged.csv"
    df.to_csv(output_path, index=False)
    logger.info(f"Saved processed data to {output_path}")

    connector.close()


def main():
    parser = argparse.ArgumentParser(description="Seed demo data into PostgreSQL")
    parser.add_argument("--client_id", default="acme_corp", help="Client ID")
    parser.add_argument("--superstore", required=True, help="Path to Global Superstore CSV/XLSX")
    parser.add_argument("--marketing", required=True, help="Path to Marketing Campaign CSV")
    args = parser.parse_args()

    logger.info(f"=== Seeding data for client: {args.client_id} ===")

    superstore = load_superstore(args.superstore)
    marketing = load_marketing(args.marketing)
    merged = merge_datasets(superstore, marketing)
    seed_to_postgres(merged, args.client_id)

    logger.info("=== Seeding complete ===")


if __name__ == "__main__":
    main()
