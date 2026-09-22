import os
import re
import time
from datetime import datetime, timedelta, timezone
from src.tools.post_dates import PostDate, normalize_post_date, matches_post_date, parse_publication_date
from urllib.parse import urlsplit, urlunsplit
import requests
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from src.models.job import Job
from src.tools.linkedin_profiles import (
    RecruiterProfile,
    _request_session,
    canonicalize_linkedin_url,
)
from src.database.db import (
    get_discovered_job,
    processed_post_exists,
    save_discovered_job,
    save_processed_post,
)


DEFAULT_POSTS_DATASET_ID = "gd_lyy3tktm25m4avu764"
SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"
PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress"
SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"
READY_STATUSES = {"ready", "completed", "complete", "success", "succeeded"}
FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}


class LinkedInPost(BaseModel):
    url: str
    author_name: str | None = None
    author_url: str | None = None
    text: str
    posted_at: str | None = None
    embedded_links: list[str] = Field(default_factory=list)


class JobPostExtraction(BaseModel):
    is_job_offer: bool
    title: str | None
    company: str | None
    location: str | None
    email: str | None
    apply_url: str | None
    confidence: float = Field(ge=0, le=1)


def get_date_range(days: int = 30) -> tuple[str, str]:
    if days <= 0:
        raise ValueError("days must be positive.")

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    return (
        start_date.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        end_date.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


def _records_from_response(data: object) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "records", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(key in data for key in ("url", "post_url", "post_text", "text")):
            return [data]
    return []


def _string(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _post_source_url(url: str) -> tuple[str, str]:
    canonical = canonicalize_linkedin_url(url)
    parts = urlsplit(canonical)
    path_parts = [part for part in parts.path.split("/") if part]

    if len(path_parts) >= 2 and path_parts[0].casefold() == "company":
        company_path = f"/company/{path_parts[1]}"
        return urlunsplit((parts.scheme, parts.netloc, company_path, "", "")), "company_url"

    if len(path_parts) >= 2 and path_parts[0].casefold() == "in":
        return canonical, "profile_url"

    raise ValueError(
        "LinkedIn post discovery requires a /company/<name> or /in/<profile> URL."
    )


def normalize_posts(raw_posts: object) -> list[LinkedInPost]:
    posts: list[LinkedInPost] = []
    seen_urls: set[str] = set()

    for item in _records_from_response(raw_posts):
        text = (
            _string(item.get("post_text"))
            or _string(item.get("text"))
            or _string(item.get("description"))
            or _string(item.get("content"))
        )
        url = _string(item.get("url")) or _string(item.get("post_url"))
        if not text or not url or url in seen_urls:
            continue

        author_url = (
            _string(item.get("user_url"))
            or _string(item.get("author_url"))
            or _string(item.get("profile_url"))
            or _string(item.get("use_url"))
        )
        posts.append(
            LinkedInPost(
                url=url,
                author_name=(
                    _string(item.get("user_name"))
                    or _string(item.get("author_name"))
                    or _string(item.get("name"))
                ),
                author_url=author_url,
                text=text,
                posted_at=(
                    _string(item.get("date_posted"))
                    or _string(item.get("posted_at"))
                    or _string(item.get("timestamp"))
                ),
                embedded_links=_string_list(item.get("embedded_links")),
            )
        )
        seen_urls.add(url)

    return posts


def scrape_recruiter_posts(
    profile_urls: list[str],
    limit_per_profile: int = 10,
    days: int = 30,
    *,
    only_authored_posts: bool = True,
    max_wait_seconds: float = 300,
    poll_interval_seconds: float = 3,
    session: requests.Session | None = None,
    post_date: PostDate = None,
) -> list[LinkedInPost]:
    post_date = normalize_post_date(post_date)
    load_dotenv()
    api_token = os.getenv("BRIGHTDATA_API_TOKEN")
    dataset_id = os.getenv("BRIGHTDATA_LINKEDIN_POSTS_DATASET_ID", DEFAULT_POSTS_DATASET_ID)

    if not api_token:
        raise ValueError("BRIGHTDATA_API_TOKEN is missing.")
    if limit_per_profile <= 0:
        return []
    if max_wait_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("Polling duration and interval must be positive.")

    normalized_sources = [_post_source_url(url) for url in profile_urls if url.strip()]
    source_kinds = {kind for _, kind in normalized_sources}
    if len(source_kinds) > 1:
        raise ValueError("Company and personal profile URLs must be collected separately.")

    unique_urls = list(dict.fromkeys(url for url, _ in normalized_sources))
    if not unique_urls:
        return []
    discover_by = next(iter(source_kinds))

    if post_date is None:
        start_date, end_date = get_date_range(days)
    else:
        start = datetime.combine(post_date, datetime.min.time(), tzinfo=timezone.utc)
        start_date = start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        end_date = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if start > datetime.now(timezone.utc):
            return []
    client = session or _request_session()
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": []
    }
    for url in unique_urls:
        item = {
                "url": url,
                "start_date": start_date,
                "end_date": end_date,
        }
        if discover_by == "profile_url":
            item["only_authored_posts"] = only_authored_posts
        payload["input"].append(item)
    response = client.post(
        SCRAPE_URL,
        headers=headers,
        params={
            "dataset_id": dataset_id,
            "format": "json",
            "notify": False,
            "include_errors": True,
            "type": "discover_new",
            "discover_by": discover_by,
        },
        json=payload,
        # Leave enough room for the documented one-minute synchronous window
        # to return either records or a snapshot ID.
        timeout=(10, 75),
    )
    response.raise_for_status()
    result = response.json()

    if isinstance(result, list) or (
        isinstance(result, dict) and not result.get("snapshot_id")
    ):
        posts = normalize_posts(result)
        posts = [post for post in posts if matches_post_date(post.posted_at, post_date)]
        return posts[: len(unique_urls) * limit_per_profile]

    if not isinstance(result, dict) or not result.get("snapshot_id"):
        raise RuntimeError(f"Unexpected LinkedIn posts response: {result}")

    snapshot_id = str(result["snapshot_id"])
    deadline = time.monotonic() + max_wait_seconds
    last_status: str | None = None
    print(f"LinkedIn posts snapshot: {snapshot_id}")

    while True:
        progress_response = client.get(
            f"{PROGRESS_URL}/{snapshot_id}",
            headers=headers,
            timeout=(10, 20),
        )
        progress_response.raise_for_status()
        progress = progress_response.json()
        status = str(progress.get("status", "unknown")).strip().lower()

        if status != last_status:
            print(f"LinkedIn posts status: {status}")
            last_status = status
        if status in READY_STATUSES:
            break
        if status in FAILED_STATUSES:
            message = progress.get("message") or progress.get("error") or status
            raise RuntimeError(f"LinkedIn post collection failed: {message}")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"LinkedIn posts snapshot {snapshot_id} did not finish within "
                f"{max_wait_seconds:g} seconds (last status: {status})."
            )
        time.sleep(min(poll_interval_seconds, remaining))

    download_response = client.get(
        f"{SNAPSHOT_URL}/{snapshot_id}",
        headers=headers,
        params={"format": "json"},
        timeout=(10, 60),
    )
    download_response.raise_for_status()
    posts = normalize_posts(download_response.json())
    posts = [post for post in posts if matches_post_date(post.posted_at, post_date)]
    return posts[: len(unique_urls) * limit_per_profile]


def classify_job_post(
    post: LinkedInPost,
    default_company: str | None = None,
    max_attempts: int = 3,
) -> JobPostExtraction:
    load_dotenv()
    llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0)
    structured_llm = llm.with_structured_output(
        JobPostExtraction,
        method="json_schema",
        strict=True,
    )
    prompt = f"""
Analyze this LinkedIn post and determine whether it advertises a real job,
internship, PFE internship, graduate opportunity, or hiring opportunity.

Do not classify career advice, a new-job announcement, marketing, congratulations,
an event, a course, training, general HR content, a completed staffing success
story, a client case study, or a vague statement about future hiring as an offer.

Set is_job_offer=true only when the post contains a current, explicit call for
candidates for at least one identifiable role. A true result must include a
concrete title copied or faithfully summarized from the advertised roles. When
several roles are advertised, combine their names concisely, for example:
"Data Engineer / Data Scientist / ML Engineer". Never use generic labels such
as "Recruitment", "Hiring", "Opportunity", or the post heading as the title.

Known recruiter company: {default_company}
Author: {post.author_name}
Post URL: {post.url}
Links included in the post: {post.embedded_links}
Post text:
{post.text}

Extract only information supported by the post. Never invent an email address,
URL, location, company, or role title. If the recruiter clearly refers to hiring
for their own company, you may use the known recruiter company.

Return a complete JobPostExtraction matching the supplied schema. All fields,
including nullable fields, are required.
"""

    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            result = structured_llm.invoke(prompt)
            extraction = JobPostExtraction.model_validate(result)

            # Enforce the same invariant used when converting an extraction to
            # a Job. A vague hiring signal without a concrete role is not an
            # actionable offer.
            if extraction.is_job_offer and not extraction.title:
                return extraction.model_copy(
                    update={
                        "is_job_offer": False,
                        "company": None,
                        "location": None,
                        "email": None,
                        "apply_url": None,
                        "confidence": 0.0,
                    }
                )

            if not extraction.is_job_offer:
                return extraction.model_copy(
                    update={
                        "title": None,
                        "company": None,
                        "location": None,
                        "email": None,
                        "apply_url": None,
                    }
                )

            return extraction
        except Exception as error:
            last_error = error

    raise RuntimeError(
        "The model could not produce a valid job-post classification after "
        f"{max_attempts} attempts."
    ) from last_error


def post_to_job(post: LinkedInPost, extraction: JobPostExtraction) -> Job | None:
    if not extraction.is_job_offer or extraction.confidence < 0.70 or not extraction.title:
        return None

    normalized_text = post.text.casefold()
    remote = any(
        keyword in normalized_text
        for keyword in ("remote", "télétravail", "teletravail", "à distance")
    )
    return Job(
        title=extraction.title,
        company=extraction.company or "Unknown",
        location=extraction.location,
        description=post.text,
        requirements=[],
        url=post.url,
        source="linkedin_post",
        application_email=extraction.email if extraction.email and extraction.email in post.text else None,
        application_url=extraction.apply_url if extraction.apply_url and (extraction.apply_url in post.text or extraction.apply_url in post.embedded_links) else None,
        original_post_url=post.url,
        published_at=parse_publication_date(post.posted_at),
        remote=remote,
    )


def _profile_key(url: str | None) -> str:
    return canonicalize_linkedin_url(url or "").casefold()


def _name_key(name: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").casefold()))


def find_jobs_from_recruiter_posts(
    recruiter_profiles: list[RecruiterProfile],
    posts_per_recruiter: int = 10,
    days: int = 30,
    *,
    include_reposts: bool = False,
    reprocess_existing: bool = False,
    verbose: bool = False,
    post_date: PostDate = None,
) -> list[Job]:
    post_date = normalize_post_date(post_date)

    if not recruiter_profiles:
        return []

    # 1. Fetch recent posts
    posts = scrape_recruiter_posts(
        profile_urls=[
            recruiter.linkedin_url
            for recruiter in recruiter_profiles
        ],
        limit_per_profile=posts_per_recruiter,
        days=days,
        only_authored_posts=not include_reposts,
        **({"post_date": post_date} if post_date is not None else {}),
    )

    print(f"Posts collected: {len(posts)}")

    # 2. Cheap keyword pre-filter
    candidate_posts = [
        post
        for post in posts
        if matches_post_date(post.posted_at, post_date) and looks_like_job_post(post)
    ]

    print(
        f"Posts after keyword pre-filter: "
        f"{len(candidate_posts)}"
    )

    # 3. Build recruiter lookup tables
    recruiters_by_url = {
        _profile_key(recruiter.linkedin_url): recruiter
        for recruiter in recruiter_profiles
    }

    recruiters_by_name = {
        _name_key(recruiter.name): recruiter
        for recruiter in recruiter_profiles
        if recruiter.name
    }

    jobs: list[Job] = []
    seen_job_urls: set[str] = set()

    # 4. Process candidate posts
    for post in candidate_posts:

        # Skip anything already classified before,
        # whether it was a job offer or not.
        if processed_post_exists(post.url) and not reprocess_existing:
            cached_job = get_discovered_job(post.url)
            if cached_job is not None and cached_job.url not in seen_job_urls:
                cached_job = cached_job.model_copy(update={"published_at": parse_publication_date(post.posted_at)})
                jobs.append(cached_job)
                seen_job_urls.add(cached_job.url)
                if verbose:
                    print(f"[Cached offer] {cached_job.title}: {cached_job.url}")
            elif verbose:
                print(f"[Cached non-offer] {post.url}")
            continue

        # Try to identify which recruiter published it
        recruiter = recruiters_by_url.get(
            _profile_key(post.author_url)
        )

        if recruiter is None and post.author_name:
            recruiter = recruiters_by_name.get(
                _name_key(post.author_name)
            )

        default_company = (
            recruiter.company
            if recruiter
            else None
        )

        # 5. LLM classification/extraction
        try:
            extraction = classify_job_post(
                post=post,
                default_company=default_company,
            )

        except Exception as error:
            print(
                f"[Post classification error] "
                f"{post.url}: {error}"
            )
            continue

        # 6. Cache the classification result
        save_processed_post(
            post_url=post.url,
            is_job_offer=extraction.is_job_offer,
        )

        if verbose:
            print(
                "[Classification] "
                f"offer={extraction.is_job_offer} "
                f"confidence={extraction.confidence:.2f} "
                f"title={extraction.title!r} "
                f"url={post.url}"
            )

        # 7. Convert valid hiring post -> Job
        job = post_to_job(
            post=post,
            extraction=extraction,
        )

        if job is None:
            continue

        save_discovered_job(job)

        # 8. Avoid duplicates in this execution
        if job.url in seen_job_urls:
            continue

        jobs.append(job)
        seen_job_urls.add(job.url)

    print(
        f"Valid job offers found: {len(jobs)}"
    )

    return jobs

JOB_SIGNAL_KEYWORDS = [
    # English
    "hiring",
    "we are hiring",
    "we're hiring",
    "job opening",
    "job opportunity",
    "internship",
    "intern",
    "graduate program",
    "graduate opportunity",
    "join our team",

    # French
    "recrutement",
    "nous recrutons",
    "offre d'emploi",
    "opportunité",
    "stage",
    "stagiaire",
    "pfe",
    "candidature",
    "rejoignez-nous",

    # Useful application signals
    "send your cv",
    "send your resume",
    "envoyez votre cv",
    "postulez",
    "apply now",
]


NEGATIVE_KEYWORDS = [
    "happy to announce i joined",
    "pleased to announce i joined",
    "career advice",
    "webinar",
    "conference",
    "training course",
]

def looks_like_job_post(
    post: LinkedInPost,
) -> bool:

    text = post.text.lower()

    # Obvious non-job content
    if any(
        keyword in text
        for keyword in NEGATIVE_KEYWORDS
    ):
        return False

    # At least one hiring signal
    return any(
        keyword in text
        for keyword in JOB_SIGNAL_KEYWORDS
    )
