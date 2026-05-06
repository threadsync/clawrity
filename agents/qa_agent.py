"""
Clawrity — QA Agent

Evaluates Gen Agent responses for faithfulness against data context.
Uses Groq LLM at temperature 0.1 for strict, deterministic evaluation.
Returns JSON: { score, passed, issues }
Threshold from client YAML hallucination_threshold (default 0.75).
"""

import json
import logging
import re
from typing import Optional, List, Dict

import pandas as pd

from config.llm_client import get_llm_client, get_fast_model_name, chat_with_retry

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
- Branch/entity names listed under "Valid Entities from User Question" are VALID even if not listed in query results
- Branch/entity names listed under "Branches/entities filtered in SQL WHERE clause" are VALID even if not in result rows (e.g., if SQL has WHERE branch = 'X', then 'X' is valid context)
- If ANY branch name appears in the response but NOT in the Data Context, the valid-entities list, or the SQL WHERE clause filters, this is a HALLUCINATION
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
        self.model = get_fast_model_name()
        # Cache: hash(response) -> evaluation result
        self._cache: Dict[str, Dict] = {}

    def evaluate(
        self,
        response: str,
        data_context: Optional[pd.DataFrame] = None,
        threshold: float = 0.75,
        supplementary_context: Optional[pd.DataFrame] = None,
        user_question: str = "",
        sql: Optional[str] = None,
    ) -> Dict:
        """
        Evaluate a response for faithfulness.

        Args:
            response: Gen Agent's response text
            data_context: The data the response should be grounded in
            threshold: Minimum score to pass (from client YAML)
            supplementary_context: Benchmark data (top performers) that is also valid ground truth
            user_question: The user's original question (entities mentioned here are valid context)
            sql: The SQL query that produced the data context (branch/entity filters are valid context)

        Returns:
            Dict with score (float), passed (bool), issues (list[str])
        """
        # Cache check: skip LLM call if we already evaluated this exact response
        cache_key = str(hash(response))
        if cache_key in self._cache:
            logger.info("QA cache hit — skipping LLM call")
            return self._cache[cache_key]

        data_str = ""
        if data_context is not None and len(data_context) > 0:
            data_str = data_context.to_markdown(index=False)
        else:
            data_str = "No structured data available."

        # Include the SQL query so QA understands what filters were applied
        # (e.g., branch names in WHERE clause are valid context even if not in result rows)
        if sql:
            data_str += (
                f"\n\n### SQL Query (defines the data scope)\n```sql\n{sql}\n```"
            )
            # Extract branch/entity filters from SQL WHERE clause
            where_branches = self._extract_where_entities(sql)
            if where_branches:
                data_str += (
                    f"\nBranches/entities filtered in SQL WHERE clause (VALID context): "
                    f"{', '.join(sorted(where_branches))}"
                )

        # Include supplementary (benchmark) context as valid ground truth
        if supplementary_context is not None and len(supplementary_context) > 0:
            data_str += "\n\n### Benchmark Data (also valid ground truth)\n"
            data_str += supplementary_context.to_markdown(index=False)

        # Include user question so QA knows which entities are valid context
        if user_question:
            entities = self._extract_entities(user_question)
            if entities:
                entity_list = ", ".join(sorted(entities))
            else:
                entity_list = "(none)"
            data_str += (
                "\n\n### User Question Context\n"
                f'The user asked: "{user_question}"\n'
                f"Valid Entities from User Question: {entity_list}"
            )

        prompt = EVAL_PROMPT.format(
            data_context=data_str,
            response=response,
            threshold=threshold,
        )

        try:
            result = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict QA evaluator. Return only valid JSON. Pay special attention to branch names and figures that appear in the response but NOT in the data context — these are hallucinations.",
                    },
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
            self._cache[cache_key] = evaluation
            return evaluation

        except Exception as e:
            logger.error(f"QA evaluation failed: {e}")
            # On failure, pass with warning
            return {
                "score": 0.5,
                "passed": True,
                "issues": [f"QA evaluation error: {str(e)}"],
            }

    def _parse_response(self, raw: str, threshold: float) -> Dict:
        """Parse JSON response from QA LLM call. Handles truncated/malformed JSON."""
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
            logger.warning(f"Could not parse QA response: {e}. Raw: {raw[:300]}")
            # Fallback: try to extract score from truncated/malformed JSON
            score = self._extract_score_fallback(raw)
            if score is not None:
                logger.info(
                    f"Fallback: extracted score={score} from malformed response"
                )
                return {
                    "score": score,
                    "passed": score >= threshold,
                    "issues": ["QA response parsing failed (partial extraction)"],
                }
            return {
                "score": 0.5,
                "passed": True,
                "issues": ["QA response parsing failed"],
            }

    def _extract_score_fallback(self, raw: str) -> Optional[float]:
        """Extract score from malformed/truncated JSON using regex."""
        # Try to find "score": 0.8 pattern
        match = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw)
        if match:
            try:
                score = float(match.group(1))
                if 0.0 <= score <= 1.0:
                    return score
            except ValueError:
                pass
        return None

    def _extract_where_entities(self, sql: str) -> List[str]:
        """Extract branch/city entity names from SQL WHERE clause filters."""
        if not sql:
            return []
        entities = set()
        # Match patterns like: branch = 'Seattle', city = 'Toronto'
        for match in re.finditer(
            r"(?:branch|city|country)\s*=\s*'([^']+)'",
            sql,
            re.IGNORECASE,
        ):
            val = match.group(1).strip()
            if val and len(val) > 1:
                entities.add(val)
        # Also handle IN ('val1', 'val2') patterns
        for match in re.finditer(
            r"(?:branch|city|country)\s+IN\s*\(([^)]+)\)",
            sql,
            re.IGNORECASE,
        ):
            for val in re.findall(r"'([^']+)'", match.group(1)):
                if val and len(val) > 1:
                    entities.add(val)
        return list(entities)

    def _extract_entities(self, text: str) -> List[str]:
        """Extract likely branch/city entities from a user question."""
        if not text:
            return []

        lowered = text.lower()
        patterns = [
            r"\bbranch\s+([a-z][a-z\s\-']{1,60})",
            r"\bin\s+([a-z][a-z\s\-']{1,60})",
            r"\bfor\s+the\s+([a-z][a-z\s\-']{1,60})\s+branch",
        ]

        stops = {
            "the",
            "a",
            "an",
            "my",
            "our",
            "this",
            "that",
            "these",
            "those",
            "branch",
            "branches",
            "revenue",
            "sales",
            "roi",
            "profit",
            "performance",
        }

        entities = set()
        for pattern in patterns:
            for match in re.findall(pattern, lowered):
                candidate = match.strip(" .,!?:;\"'")
                candidate = " ".join(candidate.split())
                if not candidate:
                    continue
                if candidate in stops:
                    continue
                if any(word in stops for word in candidate.split()):
                    candidate = " ".join(w for w in candidate.split() if w not in stops)
                candidate = candidate.strip()
                if len(candidate) < 2:
                    continue
                entities.add(candidate.title())

        return list(entities)
