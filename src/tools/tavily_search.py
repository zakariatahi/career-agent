import os
from datetime import date, timedelta

from dotenv import load_dotenv
from tavily import TavilyClient


load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

client = TavilyClient(
    api_key=TAVILY_API_KEY
)


def search_web(
    query: str,
    max_results: int = 5,
    days: int | None = None,
    include_domains: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    if not query.strip():
        raise ValueError("query must not be empty.")
    if not 1 <= max_results <= 20:
        raise ValueError("max_results must be between 1 and 20.")
    if days is not None and days <= 0:
        raise ValueError("days must be positive.")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must not be after end_date.")

    kwargs = {
        "query": query,
        "max_results": max_results,
        "topic": "general",
        "search_depth": "basic",
    }

    if start_date is not None:
        kwargs["start_date"] = start_date.isoformat()
    elif days is not None:
        # Tavily's `days` option only applies to news searches. LinkedIn posts
        # use general search, so apply an explicit publication/update cutoff.
        kwargs["start_date"] = (
            date.today() - timedelta(days=days)
        ).isoformat()

    if end_date is not None:
        kwargs["end_date"] = end_date.isoformat()

    if include_domains:
        kwargs["include_domains"] = include_domains

    response = client.search(**kwargs)

    return response.get("results", [])
