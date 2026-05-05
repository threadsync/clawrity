"""
Clawrity — Scout Agent

Fetches real-time competitor updates and sector-specific news.
Runs inside HEARTBEAT digest job ONLY — never on ad-hoc /chat queries.
Appends "Market Intelligence" section to morning digest.

If nothing relevant is found, the section is omitted entirely — no filler.
"""

import logging
from datetime import datetime
from typing import Optional

from config.llm_client import get_llm_client, get_model_name, chat_with_retry
from config.client_loader import ClientConfig
from config.settings import get_settings
from skills.web_search import web_search

logger = logging.getLogger(__name__)

SCOUT_PROMPT = """You are a business intelligence scout for {client_name}.
Their sector: {sector}
Their competitors: {competitors}

Below are web search results from the last {lookback} day(s).
Extract ONLY what is directly relevant to this client's business.
Ignore anything generic or unrelated to their sector.
If nothing is relevant, respond with exactly: NO_RELEVANT_NEWS

Format relevant findings as a clean "Market Intelligence" section with bullet points.
Each bullet should summarize one key finding with its source.

Results:
{search_results}"""

QUERY_PROMPT = """You are a business intelligence scout for {client_name}.
Sector: {sector}
Competitors: {competitors}

The user asked: "{query}"

Below are web search results. Extract ONLY what is directly relevant to the
user's question and this client's business context. Ignore generic or unrelated content.
If nothing is relevant, respond with exactly: NO_RELEVANT_NEWS

Format findings as concise bullet points with sources.

Results:
{search_results}"""


class ScoutAgent:
    """Competitor and sector intelligence agent."""

    def __init__(self):
        self.client = get_llm_client()
        self.model = get_model_name()

    async def gather_intelligence(
        self,
        client_config: ClientConfig,
    ) -> Optional[str]:
        """
        Fetch and summarize competitor/sector news for digest.

        Args:
            client_config: Client config with scout section

        Returns:
            Formatted "Market Intelligence" markdown section, or None if nothing relevant
        """
        scout_config = client_config.scout
        if not scout_config.sector and not scout_config.competitors:
            logger.info(f"[{client_config.client_id}] No scout config — skipping")
            return None

        lookback = scout_config.news_lookback_days
        today = datetime.now().strftime("%Y-%m-%d")

        # Gather search results
        all_results = []

        # Search for each competitor
        for competitor in scout_config.competitors:
            query = f"{competitor} latest news"
            results = web_search(query, max_results=3, lookback_days=lookback)
            all_results.extend(results)

        # Search for sector keywords
        for keyword in scout_config.keywords[:3]:  # Limit to 3 keywords
            query = f"{keyword} news {today}"
            results = web_search(query, max_results=3, lookback_days=lookback)
            all_results.extend(results)

        if not all_results:
            logger.info(f"[{client_config.client_id}] No search results found")
            return None

        # Format results for LLM
        results_text = "\n\n".join(
            f"**{r['title']}** ({r['url']})\n{r['content']}" for r in all_results
        )

        # Summarize with Groq
        prompt = SCOUT_PROMPT.format(
            client_name=client_config.client_name,
            sector=scout_config.sector,
            competitors=", ".join(scout_config.competitors),
            lookback=lookback,
            search_results=results_text,
        )

        try:
            response = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a business intelligence scout.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            result = response.choices[0].message.content.strip()

            if result == "NO_RELEVANT_NEWS":
                logger.info(
                    f"[{client_config.client_id}] Scout: no relevant news found"
                )
                return None

            section = f"## 🔭 Market Intelligence\n\n{result}"
            logger.info(
                f"[{client_config.client_id}] Scout: generated intelligence section"
            )
            return section

        except Exception as e:
            logger.error(f"Scout Agent failed: {e}")
            return None

    async def search_query(
        self,
        client_config: ClientConfig,
        query: str,
    ) -> Optional[str]:
        """
        Run a targeted scout search for a specific user query.

        Used by the /scout endpoint for ad-hoc competitor/news queries.

        Args:
            client_config: Client config with scout section
            query: User's specific question about competitors/market

        Returns:
            Formatted intelligence summary, or None if nothing relevant
        """
        scout_config = client_config.scout

        # Search with the user's query directly
        results = web_search(
            query, max_results=5, lookback_days=scout_config.news_lookback_days
        )

        # Also search with competitor names if they appear in the query
        for competitor in scout_config.competitors:
            if competitor.lower() in query.lower():
                extra = web_search(
                    f"{competitor} latest news",
                    max_results=3,
                    lookback_days=scout_config.news_lookback_days,
                )
                results.extend(extra)

        if not results:
            logger.info(f"[{client_config.client_id}] Scout query returned no results")
            return None

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                unique_results.append(r)

        # Format results for LLM
        results_text = "\n\n".join(
            f"**{r['title']}** ({r['url']})\n{r['content']}" for r in unique_results
        )

        prompt = QUERY_PROMPT.format(
            client_name=client_config.client_name,
            sector=scout_config.sector,
            competitors=", ".join(scout_config.competitors),
            query=query,
            search_results=results_text,
        )

        try:
            response = chat_with_retry(
                self.client,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a business intelligence scout.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            result = response.choices[0].message.content.strip()

            if result == "NO_RELEVANT_NEWS":
                return None

            return result

        except Exception as e:
            logger.error(f"Scout query failed: {e}")
            return None
