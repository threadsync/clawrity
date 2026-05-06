"""
Clawrity — RAG Evaluator

Lightweight evaluation using the unified LLM client (supports Groq, NVIDIA, Xiaomi, Mistral).
Four metrics: faithfulness, answer_relevancy, context_precision, context_recall.
Single LLM call with structured JSON output.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from config.settings import get_settings
from config.llm_client import get_llm_client, get_model_name, chat_with_retry

logger = logging.getLogger(__name__)

EVAL_PROMPT = """Evaluate this RAG-augmented response on four criteria.

## User Query
{query}

## Retrieved Context Chunks
{chunks}

## Generated Response
{response}

## Evaluation Criteria (score each 0.0 to 1.0)

1. **Faithfulness**: Does the response ONLY contain information from the retrieved chunks? No hallucination?
2. **Answer Relevancy**: Does the response directly address the user's question?
3. **Context Precision**: Were the retrieved chunks actually relevant to the question?
4. **Context Recall**: Did the retrieval capture enough context to answer the question fully?

Return ONLY a JSON object:
{{
    "faithfulness": <float>,
    "answer_relevancy": <float>,
    "context_precision": <float>,
    "context_recall": <float>,
    "overall": <float (average of all four)>,
    "notes": "<brief explanation>"
}}"""


@dataclass
class EvalResult:
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0
    overall: float = 0.0
    notes: str = ""


class RAGEvaluator:
    """Evaluates RAG pipeline quality using the configured LLM provider."""

    def __init__(self):
        self.client = get_llm_client()
        self.model = get_model_name()

    def evaluate(
        self,
        query: str,
        chunks: List[Dict],
        response: str,
    ) -> EvalResult:
        """Evaluate a RAG response."""
        chunks_text = (
            "\n".join(
                f"{i + 1}. {c.get('text', '')} (similarity: {c.get('similarity', 0):.2f})"
                for i, c in enumerate(chunks)
            )
            if chunks
            else "No chunks retrieved."
        )

        prompt = EVAL_PROMPT.format(
            query=query,
            chunks=chunks_text,
            response=response,
        )

        try:
            result = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a RAG evaluation expert. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            )

            raw = result.choices[0].message.content.strip()
            return self._parse(raw)

        except Exception as e:
            logger.error(f"RAG evaluation failed: {e}")
            return EvalResult(notes=f"Evaluation error: {str(e)}")

    def _parse(self, raw: str) -> EvalResult:
        """Parse JSON evaluation response."""
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

            data = json.loads(cleaned.strip())
            return EvalResult(
                faithfulness=float(data.get("faithfulness", 0)),
                answer_relevancy=float(data.get("answer_relevancy", 0)),
                context_precision=float(data.get("context_precision", 0)),
                context_recall=float(data.get("context_recall", 0)),
                overall=float(data.get("overall", 0)),
                notes=data.get("notes", ""),
            )
        except Exception as e:
            logger.warning(f"Could not parse evaluation: {e}")
            return EvalResult(notes="Parse error")
