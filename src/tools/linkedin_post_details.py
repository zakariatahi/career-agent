"""Discover LinkedIn job posts and extract trustworthy structured details."""


import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from src.tools.linkedin_post_search import (
    get_linkedin_post_datetime,
    normalize_linkedin_post_url,
)
from src.tools.tavily_search import client as default_tavily_client


class JobOfferExtraction(BaseModel):
    is_job_offer: bool
    confidence: float = Field(default=0, ge=0, le=1)
    job_title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    post_text: str | None = None
    emails: list[str] = Field(default_factory=list)
    application_links: list[str] = Field(default_factory=list)
    linkedin_short_links: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    experience: str | None = None
    application_method: str | None = None


class LinkedInJobPost(JobOfferExtraction):
    linkedin_post_url: str
    published_at: datetime | None = None
    tavily_score: float | None = None


class LinkedInPostRequest(BaseModel):
    url: str
    title: str = ""
    fallback_content: str = ""
    tavily_score: float | None = None


def build_linkedin_job_query(role: str, location: str) -> str:
    role = role.strip()
    location = location.strip()
    if not role:
        raise ValueError("role must not be empty.")
    if not location:
        raise ValueError("location must not be empty.")

    return (
        f'"{role}" (hiring OR recrutement OR "nous recrutons" OR '
        f'"offre d\'emploi" OR internship OR stage OR candidature) '
        f'("{location}" OR Morocco OR Maroc OR Casablanca OR Rabat '
        f'OR Tanger OR Tangier)'
    )


def _get_llm() -> ChatGroq:
    return ChatGroq(
        model=os.getenv("GROQ_POST_EXTRACTION_MODEL", "openai/gpt-oss-120b"),
        temperature=0,
    )


def _extract_page_content(
    urls: list[str],
    *,
    tavily_client: Any,
) -> dict[str, str]:
    if not urls:
        return {}

    try:
        response = tavily_client.extract(
            urls=urls,
            extract_depth="advanced",
            format="markdown",
        )
    except TypeError:
        # Compatibility with older Tavily SDK versions.
        response = tavily_client.extract(
            urls=urls,
            extract_depth="advanced",
        )

    extracted: dict[str, str] = {}
    for result in response.get("results", []):
        normalized = normalize_linkedin_post_url(result.get("url", ""))
        content = result.get("raw_content", "") or ""
        if normalized and content:
            extracted[normalized] = content
    return extracted


def _invoke_extractor(
    *,
    title: str,
    source_text: str,
    url: str,
    llm: Any,
) -> JobOfferExtraction:
    prompt = f"""
Analyze the content extracted from one LinkedIn post URL.

Return is_job_offer=true only for a current, concrete job, internship, PFE,
graduate, or hiring opportunity. Reject career advice, courses, events,
new-job announcements, generic employer branding, and posts without a role.

Extract facts only from the ORIGINAL post. Ignore comments, replies,
recommended posts, navigation, and related content. Never combine separate
offers visible elsewhere on the page. Never invent missing values.

For application_links, return external application/careers URLs. Put lnkd.in
URLs in linkedin_short_links. Return emails only when they belong to the
original offer. Preserve the original post body in post_text when identifiable.

LinkedIn post URL: {url}
Search title: {title}

Extracted page content:
{source_text[:16000]}

Return ONLY one JSON object. Include every key below even when its value is
null or an empty list:
{{
  "is_job_offer": false,
  "confidence": 0.0,
  "job_title": null,
  "company": null,
  "location": null,
  "employment_type": null,
  "post_text": null,
  "emails": [],
  "application_links": [],
  "linkedin_short_links": [],
  "skills": [],
  "experience": null,
  "application_method": null
}}
"""

    structured = llm.with_structured_output(
        JobOfferExtraction,
        method="json_mode",
    )
    last_error: Exception | None = None
    for _ in range(2):
        try:
            result = structured.invoke(prompt)
            if isinstance(result, JobOfferExtraction):
                return result
            return JobOfferExtraction.model_validate(result)
        except Exception as error:
            last_error = error
    assert last_error is not None
    raise last_error


def _value_appears_in_source(value: str, source_text: str) -> bool:
    value_lower = unquote(value).lower().rstrip("/")
    source_lower = unquote(source_text).lower()
    return (
        value_lower in source_lower
        or quote(value, safe="").lower() in source_text.lower()
    )


def _clean_link(url: str, source_text: str) -> str | None:
    candidate = url.strip().rstrip(".,;)")
    if not candidate:
        return None
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"

    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return None

    if host.endswith("linkedin.com") and parsed.path.startswith(
        ("/safety/go", "/redir/redirect")
    ):
        destination = parse_qs(parsed.query).get("url", [None])[0]
        if destination:
            candidate = unquote(destination)
            parsed = urlsplit(candidate)
            host = (parsed.hostname or "").lower()

    if not _value_appears_in_source(url, source_text) and not _value_appears_in_source(
        candidate, source_text
    ):
        return None

    return urlunsplit(
        (parsed.scheme or "https", parsed.netloc, parsed.path, parsed.query, "")
    )


def _validate_contact_fields(
    extraction: JobOfferExtraction,
    source_text: str,
) -> JobOfferExtraction:
    # The model isolates the original post in post_text. Using it as the
    # validation scope prevents links/emails from comments and page navigation
    # from being accepted as application details.
    contact_source = extraction.post_text or source_text
    extraction.emails = list(
        dict.fromkeys(
            email.lower()
            for email in extraction.emails
            if _value_appears_in_source(email, contact_source)
        )
    )

    application_links: list[str] = []
    short_links: list[str] = []
    for value in extraction.application_links + extraction.linkedin_short_links:
        cleaned = _clean_link(value, contact_source)
        if cleaned is None:
            continue
        host = (urlsplit(cleaned).hostname or "").lower()
        target = short_links if host == "lnkd.in" else application_links
        if cleaned not in target:
            target.append(cleaned)

    extraction.application_links = application_links
    extraction.linkedin_short_links = short_links
    extraction.skills = list(dict.fromkeys(skill.strip() for skill in extraction.skills if skill.strip()))
    return extraction


def fetch_linkedin_post_info(
    url: str,
    *,
    title: str = "",
    fallback_content: str = "",
    tavily_score: float | None = None,
    tavily_client: Any | None = None,
    llm: Any | None = None,
) -> LinkedInJobPost:
    normalized_url = normalize_linkedin_post_url(url)
    if normalized_url is None:
        raise ValueError("url must be a LinkedIn post URL.")

    tavily_client = tavily_client or default_tavily_client
    extracted = _extract_page_content(
        [normalized_url],
        tavily_client=tavily_client,
    )
    source_text = extracted.get(normalized_url) or fallback_content.strip()
    if not source_text:
        raise ValueError("Tavily returned no content for the LinkedIn post.")

    extraction = _invoke_extractor(
        title=title,
        source_text=source_text,
        url=normalized_url,
        llm=llm or _get_llm(),
    )
    extraction = _validate_contact_fields(extraction, source_text)

    return LinkedInJobPost(
        **extraction.model_dump(),
        linkedin_post_url=normalized_url,
        published_at=get_linkedin_post_datetime(normalized_url),
        tavily_score=tavily_score,
    )


def fetch_linkedin_posts_info(
    posts: list[LinkedInPostRequest],
    *,
    tavily_client: Any | None = None,
    llm: Any | None = None,
    verbose: bool = False,
) -> list[LinkedInJobPost]:
    """Extract up to 20 LinkedIn posts with one batched Tavily request."""
    if not posts:
        return []
    if len(posts) > 20:
        raise ValueError("A maximum of 20 LinkedIn posts can be fetched at once.")

    normalized_posts: dict[str, LinkedInPostRequest] = {}
    for post in posts:
        normalized = normalize_linkedin_post_url(post.url)
        if normalized is None:
            raise ValueError(f"Not a LinkedIn post URL: {post.url}")
        normalized_posts.setdefault(normalized, post)

    tavily_client = tavily_client or default_tavily_client
    llm = llm or _get_llm()
    extracted_pages = _extract_page_content(
        list(normalized_posts),
        tavily_client=tavily_client,
    )

    details: list[LinkedInJobPost] = []
    for normalized_url, post in normalized_posts.items():
        source_text = (
            extracted_pages.get(normalized_url)
            or post.fallback_content.strip()
        )
        if not source_text:
            if verbose:
                print(f"[No LinkedIn post content] {normalized_url}")
            continue

        try:
            extraction = _invoke_extractor(
                title=post.title,
                source_text=source_text,
                url=normalized_url,
                llm=llm,
            )
            extraction = _validate_contact_fields(extraction, source_text)
        except Exception as error:
            if verbose:
                print(f"[LinkedIn post extraction error] {normalized_url}: {error}")
            continue

        details.append(
            LinkedInJobPost(
                **extraction.model_dump(),
                linkedin_post_url=normalized_url,
                published_at=get_linkedin_post_datetime(normalized_url),
                tavily_score=post.tavily_score,
            )
        )

    return details


def search_linkedin_job_post_details(
    role: str = "Data Engineer",
    location: str = "Morocco",
    days: int = 7,
    max_results: int = 10,
    minimum_confidence: float = 0.7,
    *,
    tavily_client: Any | None = None,
    llm: Any | None = None,
    verbose: bool = False,
) -> list[LinkedInJobPost]:
    if days <= 0:
        raise ValueError("days must be positive.")
    if not 1 <= max_results <= 20:
        raise ValueError("max_results must be between 1 and 20.")
    if not 0 <= minimum_confidence <= 1:
        raise ValueError("minimum_confidence must be between 0 and 1.")

    tavily_client = tavily_client or default_tavily_client
    llm = llm or _get_llm()
    response = tavily_client.search(
        query=build_linkedin_job_query(role, location),
        topic="general",
        search_depth="basic",
        max_results=max_results,
        include_domains=["linkedin.com/posts"],
        start_date=(date.today() - timedelta(days=days)).isoformat(),
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates: dict[str, dict[str, Any]] = {}
    for result in response.get("results", []):
        normalized = normalize_linkedin_post_url(result.get("url", ""))
        if normalized is None or normalized in candidates:
            continue
        published_at = get_linkedin_post_datetime(normalized)
        if published_at is None or published_at < cutoff:
            continue
        candidates[normalized] = result

    extracted_pages = _extract_page_content(
        list(candidates),
        tavily_client=tavily_client,
    )
    offers: list[LinkedInJobPost] = []
    for url, result in candidates.items():
        source_text = extracted_pages.get(url) or result.get("content", "") or ""
        if not source_text:
            continue
        try:
            extraction = _invoke_extractor(
                title=result.get("title", "") or "",
                source_text=source_text,
                url=url,
                llm=llm,
            )
            extraction = _validate_contact_fields(extraction, source_text)
        except Exception as error:
            if verbose:
                print(f"[LinkedIn post extraction error] {url}: {error}")
            continue

        if (
            not extraction.is_job_offer
            or extraction.confidence < minimum_confidence
            or not extraction.job_title
        ):
            continue

        offers.append(
            LinkedInJobPost(
                **extraction.model_dump(),
                linkedin_post_url=url,
                published_at=get_linkedin_post_datetime(url),
                tavily_score=result.get("score"),
            )
        )

    return offers
