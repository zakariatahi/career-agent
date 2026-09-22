import json
from urllib.request import urlopen

from src.config import ARBEITNOW_API_URL
from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, filter_jobs_by_date, parse_publication_date


def fetch_jobs(search: str = "", limit: int = 50, post_date: PostDate = None) -> list[Job]:
    post_date = normalize_post_date(post_date)
    with urlopen(ARBEITNOW_API_URL, timeout=20) as response:
        payload = json.load(response)
    jobs = [
        Job(
            title=item.get("title", ""),
            company=item.get("company_name", ""),
            url=item.get("url", ""),
            description=item.get("description", ""),
            location=item.get("location", ""),
            source="arbeitnow",
            published_at=parse_publication_date(item.get("created_at")),
            tags=item.get("tags", []),
            raw=item,
        )
        for item in payload.get("data", [])
        if not search or search.lower() in item.get("title", "").lower()
    ]
    return filter_jobs_by_date(jobs, post_date)[:limit]
