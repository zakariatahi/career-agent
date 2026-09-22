import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field
from langchain_groq import ChatGroq

from src.models.job import Job
from src.tools.post_dates import normalize_post_date
from src.tools.tavily_search import search_web


llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
)


class JobPostExtraction(BaseModel):
    is_job_offer: bool

    job_title: str | None = None
    company: str | None = None
    location: str | None = None

    email: str | None = None
    application_url: str | None = None

    confidence: float = Field(
        ge=0,
        le=1,
    )


def normalize_linkedin_post_url(url: str) -> str | None:
    """Return a stable LinkedIn post URL, or None for non-post pages."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None

    path = parsed.path.rstrip("/")
    if not (
        path.startswith("/posts/")
        or path.startswith("/feed/update/")
    ):
        return None

    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def get_linkedin_post_datetime(url: str) -> datetime | None:
    """Decode a LinkedIn activity ID (a Snowflake ID) into its UTC time."""
    match = re.search(r"activity[-:](\d{15,22})", url)
    if match is None:
        return None

    timestamp_ms = int(match.group(1)) >> 22
    try:
        published_at = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        )
    except (OSError, OverflowError, ValueError):
        return None

    now = datetime.now(timezone.utc)
    if published_at.year < 2010 or published_at > now + timedelta(days=1):
        return None
    return published_at


def classify_search_result_as_job(
    title: str,
    content: str,
    url: str,
) -> JobPostExtraction:

    prompt = f"""
You are analyzing a web search result.

Determine whether this result represents a REAL CURRENT
job, internship, PFE, graduate, or hiring opportunity.

Do NOT consider these job offers:
- career advice
- hiring statistics
- general company pages
- someone announcing they started a new job
- training courses
- webinars
- conferences
- old unrelated discussions
- generic recruitment marketing without an actual opportunity

Extract information ONLY if it is explicitly present.

Never invent:
- company
- job title
- location
- email
- application URL

Search result title:
{title}

Search result URL:
{url}

Content:
{content[:5000]}

Return ONLY valid JSON:

{{
    "is_job_offer": true,
    "job_title": null,
    "company": null,
    "location": null,
    "email": null,
    "application_url": null,
    "confidence": 0.0
}}
"""

    # GPT-OSS on Groq can emit correct JSON without making the tool call that
    # LangChain's default structured-output mode expects. JSON mode matches the
    # format explicitly requested in the prompt and still validates with the
    # Pydantic schema.
    structured_llm = llm.with_structured_output(
        JobPostExtraction,
        method="json_mode",
    )
    return structured_llm.invoke(prompt)


def search_linkedin_job_posts(
    query: str,
    location: str = "Morocco",
    max_results: int = 10,
    minimum_confidence: float = 0.7,
    days: int = 1,
    post_date: date | str | None = None,
) -> list[Job]:
    """Find hiring posts on or after post_date (UTC), overriding days."""

    post_date = normalize_post_date(post_date)
    if post_date is None and days <= 0:
        raise ValueError("days must be positive.")

    if post_date is not None:
        cutoff = datetime.combine(post_date, time.min, tzinfo=timezone.utc)
        search_dates = {"start_date": post_date}
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        search_dates = {"days": days}

    search_queries = [
        f'site:linkedin.com/posts "{query}" "{location}"',
        f'site:linkedin.com/posts "{query}" hiring "{location}"',
        f'site:linkedin.com/posts "{query}" recrutement "{location}"',
        f'site:linkedin.com/posts "{query}" stage "{location}"',
        f'site:linkedin.com/posts "{query}" internship "{location}"',
    ]

    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for search_query in search_queries:

        print(
            f"\nSearching: {search_query}"
        )

        try:
            results = search_web(
                query=search_query,
                max_results=min(max_results, 20),
                include_domains=["linkedin.com/posts"],
                **search_dates,
            )

        except Exception as error:
            print(
                f"[Tavily error] {error}"
            )
            continue

        print(
            f"Results returned: {len(results)}"
        )

        valid_posts = 0
        recent_posts = 0

        for result in results:

            url = normalize_linkedin_post_url(
                result.get("url", "")
            )
            title = result.get("title", "")
            content = result.get("content", "")

            if not url:
                continue

            valid_posts += 1

            published_at = get_linkedin_post_datetime(url)
            if published_at is None or published_at < cutoff:
                continue

            recent_posts += 1

            if url in seen_urls:
                continue

            seen_urls.add(url)

            if not content:
                continue

            try:
                extraction = (
                    classify_search_result_as_job(
                        title=title,
                        content=content,
                        url=url,
                    )
                )

            except Exception as error:
                print(
                    f"[Classification error] "
                    f"{url}: {error}"
                )
                continue

            print(
                f"{title[:60]} "
                f"-> job={extraction.is_job_offer} "
                f"confidence={extraction.confidence}"
            )

            if not extraction.is_job_offer:
                continue

            if (
                extraction.confidence
                < minimum_confidence
            ):
                continue

            if not extraction.job_title:
                continue

            job = Job(
                title=extraction.job_title,
                company=(
                    extraction.company
                    or "Unknown"
                ),
                location=extraction.location,
                description=content,
                requirements=[],
                url=(
                    extraction.application_url
                    or url
                ),
                source="linkedin_post_search",
                application_email=extraction.email if extraction.email and extraction.email in content else None,
                application_url=extraction.application_url if extraction.application_url and extraction.application_url in content else None,
                original_post_url=url,
                remote=False,
                published_at=published_at,
            )

            jobs.append(job)

            if len(jobs) >= max_results:
                return jobs

        print(
            f"Recent LinkedIn posts: {recent_posts}/{valid_posts}"
        )

    return jobs
