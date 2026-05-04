"""
Clawrity — Web Search Skill

Primary: Tavily API (clean, summarised results built for LLM agents)
Fallback: duckduckgo-search (no API key, no rate limits, free)

Auto-fallback: if Tavily errors or quota exceeded, silently switch to DuckDuckGo.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)


def web_search(
    query: str,
    max_results: int = 5,
    lookback_days: int = 1,
) -> List[Dict]:
    """
    Search the web using Tavily (primary) or DuckDuckGo (fallback).

    Args:
        query: Search query string
        max_results: Maximum number of results
        lookback_days: Only keep results from the last N days

    Returns:
        List of dicts with: title, url, content, date
    """
    results = _tavily_search(query, max_results)

    if not results:
        logger.info("Tavily returned no results, falling back to DuckDuckGo")
        results = _ddg_search(query, max_results)

    # Filter by recency
    if lookback_days > 0:
        results = _filter_recent(results, lookback_days)

    return results


def _tavily_search(query: str, max_results: int = 5) -> List[Dict]:
    """Search using Tavily API."""
    settings = get_settings()

    if not settings.tavily_api_key:
        logger.info("Tavily API key not configured, skipping")
        return []

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "date": item.get("published_date", ""),
                "source": "tavily",
            })

        logger.info(f"Tavily returned {len(results)} results for: {query[:50]}")
        return results

    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return []


def _ddg_search(query: str, max_results: int = 5) -> List[Dict]:
    """Search using DuckDuckGo (fallback — no API key needed)."""
    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", ""),
                    "date": "",
                    "source": "duckduckgo",
                })

        logger.info(f"DuckDuckGo returned {len(results)} results for: {query[:50]}")
        return results

    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return []


def _filter_recent(results: List[Dict], lookback_days: int) -> List[Dict]:
    """Filter results to only include items from the last N days."""
    if not results:
        return results

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    filtered = []

    for r in results:
        date_str = r.get("date", "")
        if not date_str:
            # No date info — include it (benefit of the doubt)
            filtered.append(r)
            continue

        try:
            # Try common date formats
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%B %d, %Y"):
                try:
                    dt = datetime.strptime(date_str[:19], fmt)
                    if dt >= cutoff:
                        filtered.append(r)
                    break
                except ValueError:
                    continue
            else:
                # Can't parse date, include it
                filtered.append(r)
        except Exception:
            filtered.append(r)

    return filtered
