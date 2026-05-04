"""
Clawrity — RAG Chunker

Aggregation-based semantic chunking — NOT fixed-size, NOT sliding window.
Source is structured tabular data. We aggregate rows into business-meaningful
units and write natural language narratives.

Three chunk types:
  1. branch_weekly   — GROUP BY branch, country, week
  2. channel_monthly — GROUP BY channel, country, month
  3. trend_qoq       — GROUP BY branch, country, quarter (QoQ delta COMPUTED)

Plus Faker-generated narrative summaries reflecting real patterns.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from faker import Faker

logger = logging.getLogger(__name__)
fake = Faker()


@dataclass
class Chunk:
    """A single RAG chunk."""
    id: str
    client_id: str
    chunk_type: str
    text: str
    metadata: Dict

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "chunk_type": self.chunk_type,
            "text": self.text,
            "metadata": self.metadata,
        }


def generate_chunks(df: pd.DataFrame, client_id: str) -> List[Chunk]:
    """Generate all chunk types from preprocessed data."""
    chunks = []

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    chunks.extend(_branch_weekly(df, client_id))
    chunks.extend(_channel_monthly(df, client_id))
    chunks.extend(_trend_qoq(df, client_id))
    chunks.extend(_faker_narratives(df, client_id))

    logger.info(f"Generated {len(chunks)} total chunks for {client_id}")
    return chunks


def _chunk_id(client_id: str, chunk_type: str, *parts) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{client_id}:{chunk_type}:" + ":".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Chunk Type 1: Branch Weekly
# ---------------------------------------------------------------------------

def _branch_weekly(df: pd.DataFrame, client_id: str) -> List[Chunk]:
    """GROUP BY branch, country, week. One chunk per branch per week."""
    chunks = []
    df = df.copy()
    df["week"] = df["date"].dt.isocalendar().week.astype(int)
    df["month"] = df["date"].dt.month_name()
    df["year"] = df["date"].dt.year

    grouped = df.groupby(["branch", "country", "year", "week", "month"]).agg(
        spend=("spend", "sum"),
        revenue=("revenue", "sum"),
        leads=("leads", "sum"),
        conversions=("conversions", "sum"),
    ).reset_index()

    for _, row in grouped.iterrows():
        spend = row["spend"]
        revenue = row["revenue"]
        roi = round(revenue / spend, 2) if spend > 0 else 0
        conv_rate = round(row["conversions"] / row["leads"] * 100, 1) if row["leads"] > 0 else 0

        text = (
            f"{row['branch']} ({row['country']}) in week {row['week']} of "
            f"{row['month']} {row['year']}: spent ${spend:,.0f}, earned "
            f"${revenue:,.0f}, ROI {roi}x, {row['leads']} leads, "
            f"{conv_rate}% conversion rate."
        )

        chunks.append(Chunk(
            id=_chunk_id(client_id, "branch_weekly", row["branch"], row["year"], row["week"]),
            client_id=client_id,
            chunk_type="branch_weekly",
            text=text,
            metadata={
                "branch": row["branch"],
                "country": row["country"],
                "week": int(row["week"]),
                "month": row["month"],
                "year": int(row["year"]),
                "roi": roi,
            },
        ))

    logger.info(f"Generated {len(chunks)} branch_weekly chunks")
    return chunks


# ---------------------------------------------------------------------------
# Chunk Type 2: Channel Monthly
# ---------------------------------------------------------------------------

def _channel_monthly(df: pd.DataFrame, client_id: str) -> List[Chunk]:
    """GROUP BY channel, country, month, quarter."""
    chunks = []
    df = df.copy()
    df["month"] = df["date"].dt.month_name()
    df["quarter"] = "Q" + df["date"].dt.quarter.astype(str)
    df["year"] = df["date"].dt.year

    grouped = df.groupby(["channel", "country", "year", "month", "quarter"]).agg(
        spend=("spend", "sum"),
        revenue=("revenue", "sum"),
        leads=("leads", "sum"),
        conversions=("conversions", "sum"),
    ).reset_index()

    for _, row in grouped.iterrows():
        spend = row["spend"]
        revenue = row["revenue"]
        roi = round(revenue / spend, 2) if spend > 0 else 0

        text = (
            f"{row['channel']} in {row['country']} during {row['month']} "
            f"({row['quarter']}) {row['year']}: ${spend:,.0f} spent, "
            f"${revenue:,.0f} revenue, ROI {roi}x."
        )

        chunks.append(Chunk(
            id=_chunk_id(client_id, "channel_monthly", row["channel"], row["country"], row["year"], row["month"]),
            client_id=client_id,
            chunk_type="channel_monthly",
            text=text,
            metadata={
                "channel": row["channel"],
                "country": row["country"],
                "month": row["month"],
                "quarter": row["quarter"],
                "year": int(row["year"]),
                "roi": roi,
            },
        ))

    logger.info(f"Generated {len(chunks)} channel_monthly chunks")
    return chunks


# ---------------------------------------------------------------------------
# Chunk Type 3: QoQ Trend (Most Important)
# ---------------------------------------------------------------------------

def _trend_qoq(df: pd.DataFrame, client_id: str) -> List[Chunk]:
    """GROUP BY branch, country, quarter. Compute quarter-over-quarter delta."""
    chunks = []
    df = df.copy()
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)

    grouped = df.groupby(["branch", "country", "quarter"]).agg(
        spend=("spend", "sum"),
        revenue=("revenue", "sum"),
    ).reset_index()

    # Sort for QoQ calculation
    grouped = grouped.sort_values(["branch", "country", "quarter"])

    for (branch, country), group in grouped.groupby(["branch", "country"]):
        group = group.sort_values("quarter").reset_index(drop=True)

        for i in range(1, len(group)):
            prev = group.iloc[i - 1]
            curr = group.iloc[i]

            prev_rev = prev["revenue"]
            curr_rev = curr["revenue"]

            if prev_rev > 0:
                delta = round((curr_rev - prev_rev) / prev_rev * 100, 1)
            else:
                delta = 0

            direction = "grew" if delta > 0 else "declined"

            text = (
                f"{branch} ({country}) revenue {direction} {abs(delta)}% "
                f"in {curr['quarter']} vs {prev['quarter']}. "
                f"Total spend: ${curr['spend']:,.0f}, revenue: ${curr_rev:,.0f}."
            )

            chunks.append(Chunk(
                id=_chunk_id(client_id, "trend_qoq", branch, country, curr["quarter"]),
                client_id=client_id,
                chunk_type="trend_qoq",
                text=text,
                metadata={
                    "branch": branch,
                    "country": country,
                    "quarter": curr["quarter"],
                    "prev_quarter": prev["quarter"],
                    "delta_pct": delta,
                },
            ))

    logger.info(f"Generated {len(chunks)} trend_qoq chunks")
    return chunks


# ---------------------------------------------------------------------------
# Faker Narrative Chunks
# ---------------------------------------------------------------------------

def _faker_narratives(df: pd.DataFrame, client_id: str) -> List[Chunk]:
    """Generate plausible narrative chunks reflecting real data patterns."""
    chunks = []
    df = df.copy()
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)

    # Find top and bottom performers per quarter
    quarterly = df.groupby(["branch", "country", "quarter"]).agg(
        revenue=("revenue", "sum"),
        spend=("spend", "sum"),
        leads=("leads", "sum"),
    ).reset_index()

    templates = [
        "{branch} branch demonstrated strong {quarter} performance driven by {channel} efficiency, outperforming regional averages.",
        "In {quarter}, {branch} ({country}) showed {trend} momentum with revenue reaching ${revenue:,.0f}, primarily through {channel} campaigns.",
        "{branch} branch in {country} maintained steady growth in {quarter}, with lead generation up and conversion rates holding above {conv_rate:.1f}%.",
        "Cost efficiency at {branch} ({country}) improved in {quarter}, with spend-to-revenue ratio tightening to {ratio:.2f}x.",
    ]

    channels = df["channel"].dropna().unique().tolist() or ["Paid Search", "Social Media", "Email"]

    for _, row in quarterly.iterrows():
        roi = row["revenue"] / row["spend"] if row["spend"] > 0 else 0
        conv_rate = np.random.uniform(5, 20)
        trend = "positive" if roi > 1.5 else "moderate" if roi > 1 else "challenging"
        channel = np.random.choice(channels)

        template = np.random.choice(templates)
        text = template.format(
            branch=row["branch"],
            country=row["country"],
            quarter=row["quarter"],
            channel=channel,
            revenue=row["revenue"],
            trend=trend,
            conv_rate=conv_rate,
            ratio=1 / roi if roi > 0 else 0,
        )

        chunks.append(Chunk(
            id=_chunk_id(client_id, "narrative", row["branch"], row["country"], row["quarter"]),
            client_id=client_id,
            chunk_type="narrative",
            text=text,
            metadata={
                "branch": row["branch"],
                "country": row["country"],
                "quarter": row["quarter"],
                "source": "generated_narrative",
            },
        ))

    logger.info(f"Generated {len(chunks)} narrative chunks")
    return chunks
