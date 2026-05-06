"""
Clawrity — NL-to-SQL Engine

Converts natural language questions into valid PostgreSQL SELECT queries.
Uses LLM at temperature 0.1 for deterministic SQL generation.
Safety: Only SELECT queries allowed. INSERT/UPDATE/DELETE/DROP rejected.
"""

import re
import logging
from typing import Optional

from config.llm_client import get_llm_client, get_fast_model_name, chat_with_retry

logger = logging.getLogger(__name__)

# Dangerous SQL patterns — reject anything that isn't a SELECT
UNSAFE_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are a PostgreSQL SQL generator. Generate ONLY a valid SELECT query.
Return ONLY the raw SQL — no markdown, no explanation, no code fences.

Table: spend_data
Columns:
  - id: SERIAL PRIMARY KEY
  - date: DATE
  - country: VARCHAR(100)
  - branch: VARCHAR(100)
  - channel: VARCHAR(100)
  - spend: FLOAT
  - revenue: FLOAT
  - leads: INT
  - conversions: INT
  - client_id: VARCHAR(100)

Available countries: {countries}
Available branches (sample): {branches}
Available channels: {channels}
Date range: {date_min} to {date_max}

RULES:
1. ALWAYS include WHERE client_id = '{client_id}' in your queries
2. Use standard PostgreSQL syntax
3. For date ranges, use DATE type comparisons
4. For "last N days", use: date >= CURRENT_DATE - INTERVAL '{n} days'
5. For "last month", use: date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
6. Return meaningful aggregations with GROUP BY when appropriate
7. Use aliases for computed columns (e.g., SUM(revenue) AS total_revenue)
8. LIMIT results to 50 rows maximum unless the user asks for all
9. For "bottom N" use ASC ordering, for "top N" use DESC ordering
"""


class NLToSQL:
    """Natural language to SQL converter using LLM."""

    def __init__(self):
        self.client = get_llm_client()
        self.model = get_fast_model_name()

    def generate_sql(
        self,
        question: str,
        client_id: str,
        schema_metadata: dict,
    ) -> Optional[str]:
        """
        Convert a natural language question to a PostgreSQL SELECT query.

        Args:
            question: User's natural language question
            client_id: Client ID for filtering
            schema_metadata: Dict with countries, branches, channels, date_min, date_max

        Returns:
            Valid SQL SELECT string, or None on failure
        """
        # Build the system prompt with schema context
        system = SYSTEM_PROMPT.format(
            countries=", ".join(schema_metadata.get("countries", [])[:20]),
            branches=", ".join(schema_metadata.get("branches", [])[:20]),
            channels=", ".join(schema_metadata.get("channels", [])),
            date_min=schema_metadata.get("date_min", "unknown"),
            date_max=schema_metadata.get("date_max", "unknown"),
            client_id=client_id,
            n="7",  # Default for interval template
        )

        try:
            response = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                temperature=0.1,
                max_tokens=1024,
            )

            raw_sql = response.choices[0].message.content.strip()
            sql = self._clean_sql(raw_sql)

            if not self._validate_sql(sql):
                logger.warning(f"Generated SQL failed validation: {sql}")
                return None

            logger.info(f"Generated SQL: {sql}")
            return sql

        except Exception as e:
            logger.error(f"NL-to-SQL generation failed: {e}")
            return None

    def _clean_sql(self, raw: str) -> str:
        """Extract SQL from LLM response, stripping markdown code fences."""
        # Remove markdown code blocks
        cleaned = re.sub(r"```(?:sql)?\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip().rstrip(";") + ";"
        return cleaned

    def _validate_sql(self, sql: str) -> bool:
        """Validate that the SQL is a safe SELECT query."""
        if not sql or len(sql) < 10:
            return False

        # Must start with SELECT
        if not sql.strip().upper().startswith("SELECT"):
            logger.warning("SQL does not start with SELECT")
            return False

        # Must not contain dangerous operations
        if UNSAFE_PATTERNS.search(sql):
            logger.warning("SQL contains unsafe operations")
            return False

        return True
