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

from config.llm_client import get_llm_client, get_model_name

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

        Returns:
            Markdown-formatted response string
        """
        temperature = max(0.1, self.base_temperature - (retry_count * 0.2))

        prompt = self._build_prompt(
            question, soul_content, data_context, rag_chunks, retry_issues,
            strict_data_instruction, supplementary_context,
        )

        try:
            response = self.client.chat.completions.create(
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
            response = self.client.chat.completions.create(
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
    ) -> str:
        """Build the augmented prompt for response generation."""
        parts = []

        # Strict data instruction (on retry — prevents hallucination)
        if strict_data_instruction:
            parts.append(f"## ⚠️ STRICT REQUIREMENT\n{strict_data_instruction}\n")

        # Data context
        if data_context is not None and len(data_context) > 0:
            parts.append("## Data Context (query results for the user's question)")
            parts.append(data_context.to_markdown(index=False))
        else:
            parts.append("## Data Context\nNo query results available.")

        # Supplementary context (top performers for comparison)
        if supplementary_context is not None and len(supplementary_context) > 0:
            parts.append("\n## Benchmark Data (top-performing branches for comparison)")
            parts.append(supplementary_context.to_markdown(index=False))
            parts.append(
                "\nUse this benchmark data to compare the queried branch's performance "
                "against top performers. Identify which channels and strategies work "
                "best, and recommend specific, actionable improvements based on what "
                "top-performing branches are doing differently."
            )

        # RAG chunks (Phase 2)
        if rag_chunks:
            parts.append("\n## Historical Business Context (retrieved from intelligence layer)")
            if strict_data_instruction:
                parts.append("⚠️ ONLY use historical context that is about branches/entities in the Data Context above. IGNORE any historical context about other branches.")
            for i, chunk in enumerate(rag_chunks, 1):
                sim = chunk.get("similarity", 0)
                parts.append(f"{i}. {chunk['text']} (relevance: {sim:.2f})")
            parts.append("\nBase suggestions on historical context. Cite specific data points.")

        # Retry instructions
        if retry_issues:
            parts.append("\n## IMPORTANT — Previous Response Issues")
            parts.append("Your previous response had these problems. Fix them:")
            for issue in retry_issues:
                parts.append(f"- {issue}")
            parts.append("Be more precise. Only state facts supported by the data above.")
            parts.append("Do NOT introduce any new branches, cities, or figures that are not in the Data Context.")

        # User question
        parts.append(f"\n## User Question\n{question}")

        parts.append("\nProvide a professional, data-grounded response. Cite specific numbers from the data.")

        return "\n".join(parts)
