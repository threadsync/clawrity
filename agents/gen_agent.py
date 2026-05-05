"""
Clawrity — Gen Agent

Generates newsletter-style, data-grounded responses using LLM.
Supports NVIDIA NIM and Groq via OpenAI-compatible API.
Temperature 0.7 (reduced by 0.2 on each retry).
Augmented with SOUL.md + live query results + RAG chunks (Phase 2).
"""

import logging
from typing import List, Optional, Dict

import pandas as pd

from config.llm_client import get_llm_client, get_model_name, chat_with_retry

logger = logging.getLogger(__name__)


class GenAgent:
    """Response generation agent using LLM (NVIDIA NIM or Groq)."""

    def __init__(self):
        self.client = get_llm_client()
        self.model = get_model_name()
        self.base_temperature = 0.7

    def generate(
        self,
        question: str,
        soul_content: str,
        data_context: Optional[pd.DataFrame] = None,
        rag_chunks: Optional[List[Dict]] = None,
        retry_issues: Optional[List[str]] = None,
        retry_count: int = 0,
        strict_data_instruction: Optional[str] = None,
        supplementary_context: Optional[pd.DataFrame] = None,
        sql: Optional[str] = None,
    ) -> str:
        """
        Generate a data-grounded response.

        Args:
            question: User's original question
            soul_content: SOUL.md content for personality/rules
            data_context: DataFrame from PostgreSQL query results
            rag_chunks: Retrieved chunks with similarity scores (Phase 2)
            retry_issues: QA Agent issues from previous attempt
            retry_count: Current retry number (0-2)
            sql: The SQL query that produced the data context

        Returns:
            Markdown-formatted response string
        """
        temperature = max(0.1, self.base_temperature - (retry_count * 0.2))

        prompt = self._build_prompt(
            question,
            soul_content,
            data_context,
            rag_chunks,
            retry_issues,
            strict_data_instruction,
            supplementary_context,
            sql,
        )

        try:
            response = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {"role": "system", "content": soul_content},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=2048,
            )
            result = response.choices[0].message.content.strip()
            logger.info(
                f"Gen Agent produced {len(result)} chars "
                f"(temp={temperature}, retry={retry_count})"
            )
            return result

        except Exception as e:
            logger.error(f"Gen Agent failed: {e}")
            return f"I encountered an error generating your response. Please try again."

    def generate_digest(
        self,
        soul_content: str,
        data_context: pd.DataFrame,
        rag_chunks: Optional[List[Dict]] = None,
    ) -> str:
        """Generate a daily digest newsletter."""
        prompt = f"""Generate a professional daily business intelligence digest.

## Performance Data (Last 7 Days)
{data_context.to_markdown(index=False) if data_context is not None and len(data_context) > 0 else "No data available."}

"""
        if rag_chunks:
            prompt += "## Historical Context\n"
            for i, chunk in enumerate(rag_chunks, 1):
                sim = chunk.get("similarity", 0)
                prompt += f"{i}. {chunk['text']} (relevance: {sim:.2f})\n"
            prompt += "\n"

        prompt += """Format as a newsletter with:
1. **Executive Summary** — key highlights in 2-3 sentences
2. **Top Performers** — best performing branches
3. **Attention Required** — bottom 3 branches by revenue (ALWAYS include this)
4. **Channel Insights** — spending efficiency across channels
5. **Recommendations** — specific, data-backed suggestions

Use bullet points, bold key numbers, and keep it concise."""

        try:
            response = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {"role": "system", "content": soul_content},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=3000,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Digest generation failed: {e}")
            return "Daily digest generation encountered an error."

    def _build_prompt(
        self,
        question: str,
        soul_content: str,
        data_context: Optional[pd.DataFrame],
        rag_chunks: Optional[List[Dict]],
        retry_issues: Optional[List[str]],
        strict_data_instruction: Optional[str] = None,
        supplementary_context: Optional[pd.DataFrame] = None,
        sql: Optional[str] = None,
    ) -> str:
        """Build the augmented prompt for response generation."""
        parts = []

        # Strict data instruction — prevents hallucination
        if strict_data_instruction:
            parts.append(f"## ⚠️ STRICT REQUIREMENT\n{strict_data_instruction}\n")

        # SQL query that produced the data (so the model knows what filters were applied)
        if sql:
            parts.append(f"## SQL Query Used\n```sql\n{sql}\n```\n")

        # Data context with computed summaries
        if data_context is not None and len(data_context) > 0:
            parts.append("## Data Context (query results for the user's question)")
            parts.append(data_context.to_markdown(index=False))

            # Compute summary statistics to help the LLM cite precise numbers
            summary = self._compute_summary(data_context)
            if summary:
                parts.append(f"\n### Computed Summary\n{summary}")
        else:
            parts.append("## Data Context\nNo query results available.")

        # Supplementary context (top performers for comparison)
        if supplementary_context is not None and len(supplementary_context) > 0:
            parts.append("\n## Benchmark Data (top-performing branches for comparison)")
            parts.append(supplementary_context.to_markdown(index=False))

            bench_summary = self._compute_summary(supplementary_context)
            if bench_summary:
                parts.append(f"\n### Benchmark Summary\n{bench_summary}")

            parts.append(
                "\n### How to use benchmark data\n"
                "Compare the queried branch's metrics against these top performers:\n"
                "- If the queried branch's ROI is lower than benchmarks, recommend shifting budget to higher-ROI channels\n"
                "- If a channel underperforms vs benchmarks, suggest reducing spend or optimizing it\n"
                "- Cite SPECIFIC numbers: 'Your Email ROI is 2.29 vs the top performer's 2.50'\n"
                "- Be concrete: 'Shift $X from Facebook to Email based on the ROI difference'"
            )

        # RAG chunks (Phase 2)
        if rag_chunks:
            parts.append(
                "\n## Historical Business Context (retrieved from intelligence layer)"
            )
            parts.append(
                "⚠️ ONLY use historical context that is about branches/entities in the Data Context above. IGNORE any historical context about other branches."
            )
            for i, chunk in enumerate(rag_chunks, 1):
                sim = chunk.get("similarity", 0)
                parts.append(f"{i}. {chunk['text']} (relevance: {sim:.2f})")

        # Retry instructions
        if retry_issues:
            parts.append("\n## IMPORTANT — Previous Response Issues")
            parts.append("Your previous response had these problems. Fix them:")
            for issue in retry_issues:
                parts.append(f"- {issue}")
            parts.append(
                "Be more precise. Only state facts supported by the data above."
            )
            parts.append(
                "Do NOT introduce any new branches, cities, or figures that are not in the Data Context."
            )

        # User question
        parts.append(f"\n## User Question\n{question}")

        # Response quality instructions
        parts.append(
            "\n## Response Quality Rules\n"
            "1. ALWAYS cite specific numbers from the Data Context (e.g., '$29,941 revenue', 'ROI of 2.29')\n"
            "2. When comparing channels or branches, use EXACT figures from the data — never round unless using ~\n"
            "3. For recommendations, reference specific metrics: 'Email has ROI 2.29 vs Facebook's 2.06 — consider reallocating budget'\n"
            "4. Structure your answer with clear sections: Data Summary → Analysis → Recommendations\n"
            "5. Do NOT give generic advice — every recommendation must tie to a specific data point\n"
            "6. Do NOT mention branches, cities, or figures that are not in the Data Context above\n"
            "7. Keep the response concise but data-dense — prefer bullet points over paragraphs"
        )

        return "\n".join(parts)

    def _compute_summary(self, df: pd.DataFrame) -> str:
        """Compute summary statistics from a DataFrame to help the LLM cite precise numbers."""
        if df is None or len(df) == 0:
            return ""

        lines = []
        numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()

        # Total row
        totals = {}
        for col in numeric_cols:
            total = df[col].sum()
            if total != 0:
                totals[col] = total

        if totals:
            total_parts = []
            for col, val in totals.items():
                if val >= 1_000_000:
                    total_parts.append(f"Total {col}: ${val / 1_000_000:.2f}M")
                elif val >= 1_000:
                    total_parts.append(f"Total {col}: ${val:,.2f}")
                else:
                    total_parts.append(f"Total {col}: {val:,.0f}")
            lines.append(" | ".join(total_parts))

        # ROI if revenue and spend columns exist
        rev_col = next((c for c in numeric_cols if "revenue" in c.lower()), None)
        spend_col = next((c for c in numeric_cols if "spend" in c.lower()), None)
        if rev_col and spend_col:
            total_rev = df[rev_col].sum()
            total_spend = df[spend_col].sum()
            if total_spend > 0:
                lines.append(f"Overall ROI: {total_rev / total_spend:.2f}")

        # Per-row highlights (top/bottom)
        if rev_col and len(df) > 1:
            idx_max = df[rev_col].idxmax()
            idx_min = df[rev_col].idxmin()
            label_col = None
            for candidate in ["branch", "channel", "country", "name"]:
                if candidate in df.columns:
                    label_col = candidate
                    break
            if label_col:
                top = df.loc[idx_max]
                bot = df.loc[idx_min]
                lines.append(
                    f"Highest {rev_col}: {top[label_col]} (${top[rev_col]:,.2f})"
                )
                lines.append(
                    f"Lowest {rev_col}: {bot[label_col]} (${bot[rev_col]:,.2f})"
                )

        return "\n".join(lines) if lines else ""
