import requests

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, filter_jobs_by_date, parse_publication_date


REMOTIVE_URL = "https://remotive.com/api/remote-jobs"


def search_remotive_jobs(query: str, limit: int = 20, post_date: PostDate = None) -> list[Job]:
    post_date = normalize_post_date(post_date)
    params = {
        "search": query,
        "limit": limit,
    }
    if post_date is not None:
        params.pop("limit")

    response = requests.get(
        REMOTIVE_URL,
        params=params,
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    jobs = []

    for item in data.get("jobs", []):
        job = Job(
            title=item["title"],
            company=item["company_name"],
            location=item.get("candidate_required_location"),
            description=item.get("description"),
            url=item["url"],
            source="remotive",
            remote=True,
            published_at=parse_publication_date(item.get("publication_date")),
        )

        jobs.append(job)

    return filter_jobs_by_date(jobs, post_date)[:limit]
