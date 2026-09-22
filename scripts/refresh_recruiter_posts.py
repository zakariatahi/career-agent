from src.database.db import (
    get_all_recruiters,
    init_db,
    save_discovered_job,
)

from src.tools.linkedin_posts import (
    find_jobs_from_recruiter_posts,
)

from src.tools.linkedin_profiles import (
    RecruiterProfile,
)


init_db()

rows = get_all_recruiters()

recruiters = [
    RecruiterProfile(
        name=row["name"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        country_code=row["country_code"],
        score=row["score"],
        linkedin_url=row["linkedin_url"],
    )
    for row in rows
]

print(
    f"Recruiters loaded: {len(recruiters)}"
)

jobs = find_jobs_from_recruiter_posts(
    recruiter_profiles=recruiters,
    posts_per_recruiter=5,
    days=7,
)

print(
    f"Job offers discovered: {len(jobs)}"
)

for job in jobs:
    save_discovered_job(job)

    print("=" * 70)
    print("Title:", job.title)
    print("Company:", job.company)
    print("Source:", job.source)
    print("URL:", job.url)