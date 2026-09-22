import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, filter_jobs_by_date
from src.tools.dreamjob import search_dreamjob_jobs
from src.tools.linkedin import search_linkedin_jobs
from src.tools.linkedin_post_search import search_linkedin_job_posts
from src.tools.rekrute import search_rekrute_jobs
from src.tools.remotive import search_remotive_jobs

UNKNOWN_COMPANIES = {
    "",
    "confidential",
    "unknown",
    "n a",
    "none",
    "not specified",
}
TRACKING_QUERY_KEYS = {
    "ref",
    "refid",
    "trackingid",
    "trk",
}

def _normalize_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = normalized.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _canonical_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""

    parts = urlsplit(value)
    filtered_query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold().removeprefix("www."),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        )
    )


def _similarity(first: str | None, second: str | None) -> float:
    return SequenceMatcher(
        None,
        _normalize_text(first),
        _normalize_text(second),
    ).ratio()


def _known_company(company: str | None) -> bool:
    return _normalize_text(company) not in UNKNOWN_COMPANIES


def _is_duplicate(first: Job, second: Job) -> bool:
    first_url = _canonical_url(first.url)
    second_url = _canonical_url(second.url)
    if first_url and first_url == second_url:
        return True

    # Do not merge jobs merely because both scrapers reported the company as
    # "Unknown"; the role may be offered by different employers.
    if not (_known_company(first.company) and _known_company(second.company)):
        return False

    return (
        _similarity(first.title, second.title) >= 0.90
        and _similarity(first.company, second.company) >= 0.85
    )


def _job_quality(job: Job) -> tuple[int, int]:
    populated = sum(
        bool(value)
        for value in (
            job.company if _known_company(job.company) else None,
            job.location,
            job.description,
            job.published_at,
        )
    )
    return populated, len(job.description or "")


def deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    unique_jobs: list[Job] = []

    for job in jobs:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(unique_jobs)
                if _is_duplicate(job, existing)
            ),
            None,
        )
        if duplicate_index is None:
            unique_jobs.append(job)
        elif _job_quality(job) > _job_quality(unique_jobs[duplicate_index]):
            # Keep the richer record when two sources describe the same job.
            unique_jobs[duplicate_index] = job

    return unique_jobs


def extract_companies_from_jobs(
    jobs: list[Job],
    max_companies: int | None = None,
) -> list[str]:
    if max_companies is not None and max_companies <= 0:
        return []

    companies: list[str] = []
    seen: set[str] = set()

    for job in jobs:
        company = (job.company or "").strip()
        key = _normalize_text(company)
        if key in UNKNOWN_COMPANIES or key in seen:
            continue
        companies.append(company)
        seen.add(key)

        if max_companies is not None and len(companies) >= max_companies:
            break

    return companies


def _run_source(name: str, search: Callable[[], list[Job]]) -> list[Job]:
    print(f"\n[{name}] Starting search...", flush=True)
    started = monotonic()
    try:
        jobs = search()
        if not isinstance(jobs, list):
            raise TypeError(f"{name} returned {type(jobs).__name__}, expected list.")
        print(f"[{name}] Completed in {monotonic() - started:.1f}s: {len(jobs)} job(s).", flush=True)
        return jobs
    except Exception as error:
        print(f"[{name}] FAILED after {monotonic() - started:.1f}s: {error}", flush=True)
        return []


def search_all_jobs(
    query: str,
    location: str = "Morocco",
    country: str = "MA",
    limit_per_source: int = 10,
    post_date: PostDate = None,
    verbose: bool = False,
) -> list[Job]:
    """Collect jobs from job boards and Tavily's classified LinkedIn post search."""
    post_date = normalize_post_date(post_date)
    if limit_per_source <= 0:
        return []
    date_options = {"post_date": post_date} if post_date is not None else {}
    sources = [
        ("LinkedIn", lambda: search_linkedin_jobs(
            query=query, location=location, country=country,
            limit=limit_per_source, **date_options,
        )),
        ("ReKrute", lambda: search_rekrute_jobs(
            query=query, limit=limit_per_source, **date_options,
        )),
        ("Dreamjob", lambda: search_dreamjob_jobs(
            query=query, limit=limit_per_source, **date_options,
        )),
        ("Remotive", lambda: search_remotive_jobs(
            query=query, limit=limit_per_source, **date_options,
        )),
        ("Tavily", lambda: search_linkedin_job_posts(
            query=query, location=location,
            max_results=limit_per_source, **date_options,
        )),
    ]
    jobs: list[Job] = []
    for name, search in sources:
        source_jobs = filter_jobs_by_date(_run_source(name, search), post_date)[:limit_per_source]
        if verbose:
            print(f"[{name}] Matching jobs kept: {len(source_jobs)}", flush=True)
            for index, job in enumerate(source_jobs, start=1):
                print(f"  {index}. {job.title} | {job.company}")
                print(f"     Location: {job.location or 'Unknown'}")
                print(f"     Published: {job.published_at.isoformat() if job.published_at else 'Unknown'}")
                print(f"     URL: {job.url}", flush=True)
        jobs.extend(source_jobs)

    jobs = deduplicate_jobs(jobs)
    print(f"\nTotal jobs after deduplication: {len(jobs)}")
    return jobs
