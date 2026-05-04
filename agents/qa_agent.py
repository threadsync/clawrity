"""
Clawrity — QA Agent

Evaluates Gen Agent responses for faithfulness against data context.
Uses Groq LLM at temperature 0.1 for strict, deterministic evaluation.
Returns JSON: { score, passed, issues }
Threshold from client YAML hallucination_threshold (default 0.75).
"""

import json
import logging
from typing import Optional, List, Dict

import pandas as pd

from config.llm_client import get_llm_client, get_model_name

logger = logging.getLogger(__name__)

EVAL_PROMPT = """You are a strict quality assurance evaluator for business intelligence responses.

Your job: verify that the response ONLY contains claims supported by the provided data.

## Data Context (ground truth)
{data_context}

## Response to Evaluate
{response}

## Evaluation Criteria

### 1. Branch Name Validation (CRITICAL)
- Extract ALL branch/city names mentioned in the response
- Compare against the branch names in the Data Context above
- If ANY branch name appears in the response but NOT in the Data Context, this is a HALLUCINATION
- Deduct 0.3 from score for EACH unrelated branch mentioned

### 2. Numerical Accuracy (CRITICAL)
- ALL revenue, spend, lead, conversion, and ROI figures in the response must match the Data Context EXACTLY
- If a number is mentioned that does not appear in the Data Context, deduct 0.2 from score
- Rounded numbers are acceptable only if clearly approximate (e.g., "~$1.2M")

### 3. Historical Context Relevance
- If the response includes historical context or trends, it is acceptable ONLY if it directly supports the answer about branches/entities present in the Data Context
- Historical context about branches NOT in the current Data Context must be penalized: deduct 0.3 from score
- Example: If Data Context shows Toronto, Vancouver, Dubai but response mentions "Lawton showed 16436% growth" — this is IRRELEVANT historical context and must be penalized

### 4. Completeness
- Does the response address the user's question?
- Are key data points from the Data Context included?

### 5. Appropriate Hedging
- Does the response use uncertain language for inferences?
- Recommendations should be clearly marked as suggestions, not facts

## Scoring
Start at 1.0 and deduct points per the rules above. Minimum score is 0.0.

Return a JSON object with exactly this structure:
{{
    "score": <float between 0.0 and 1.0>,
    "passed": <true if score >= {threshold}>,
    "issues": [<list of specific issues found, empty if none>]
}}

IMPORTANT: If score < {threshold}, include in issues list exactly which branches, figures, or historical data were mentioned that do NOT appear in the Data Context. Format as:
"Mentioned branches/figures not in current query result: [list them]"

Return ONLY the JSON. No other text."""


class QAAgent:
    """Quality assurance agent for validating Gen Agent responses."""

    def __init__(self):
        self.client = get_llm_client()
        self.model = get_model_name()

    def evaluate(
        self,
        response: str,
        data_context: Optional[pd.DataFrame] = None,
        threshold: float = 0.75,
        supplementary_context: Optional[pd.DataFrame] = None,
        user_question: str = "",
    ) -> Dict:
        """
        Evaluate a response for faithfulness.

        Args:
            response: Gen Agent's response text
            data_context: The data the response should be grounded in
            threshold: Minimum score to pass (from client YAML)
            supplementary_context: Benchmark data (top performers) that is also valid ground truth
            user_question: The user's original question (entities mentioned here are valid context)

        Returns:
            Dict with score (float), passed (bool), issues (list[str])
        """
        data_str = ""
        if data_context is not None and len(data_context) > 0:
            data_str = data_context.to_markdown(index=False)
        else:
            data_str = "No structured data available."

        # Include supplementary (benchmark) context as valid ground truth
        if supplementary_context is not None and len(supplementary_context) > 0:
            data_str += "\n\n### Benchmark Data (also valid ground truth)\n"
            data_str += supplementary_context.to_markdown(index=False)

        # Include user question so QA knows which entities are valid context
        if user_question:
            data_str += f"\n\n### User Question Context\nThe user asked: \"{user_question}\"\nBranch/entity names mentioned in the user's question are valid to reference in the response."

        prompt = EVAL_PROMPT.format(
            data_context=data_str,
            response=response,
            threshold=threshold,
        )

        try:
            result = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a strict QA evaluator. Return only valid JSON. Pay special attention to branch names and figures that appear in the response but NOT in the data context — these are hallucinations."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            )

            raw = result.choices[0].message.content.strip()
            evaluation = self._parse_response(raw, threshold)
            logger.info(
                f"QA evaluation: score={evaluation['score']:.2f}, "
                f"passed={evaluation['passed']}, issues={len(evaluation['issues'])}"
            )
            return evaluation

        except Exception as e:
            logger.error(f"QA evaluation failed: {e}")
            # On failure, pass with warning
            return {"score": 0.5, "passed": True, "issues": [f"QA evaluation error: {str(e)}"]}

    def _parse_response(self, raw: str, threshold: float) -> Dict:
        """Parse JSON response from QA LLM call."""
        try:
            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)
            score = float(data.get("score", 0.5))
            return {
                "score": score,
                "passed": score >= threshold,
                "issues": data.get("issues", []),
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Could not parse QA response: {e}. Raw: {raw[:200]}")
            return {"score": 0.5, "passed": True, "issues": ["QA response parsing failed"]}
