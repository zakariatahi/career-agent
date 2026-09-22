from src.models.job import Job
from src.tools import arbeitnow, remotive
from src.tools.post_dates import PostDate, normalize_post_date


def search_jobs(query: str = "", limit: int = 50, post_date: PostDate = None) -> list[Job]:
    """Fetch jobs from supported job boards."""
    post_date = normalize_post_date(post_date)
    jobs = remotive.search_remotive_jobs(query, limit, post_date=post_date)
    try:
        jobs.extend(arbeitnow.fetch_jobs(query, limit, post_date=post_date))
    except OSError:
        pass
    return jobs[:limit]
