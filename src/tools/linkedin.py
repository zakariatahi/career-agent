import os
import time

import requests
from dotenv import load_dotenv

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, matches_post_date, parse_publication_date


load_dotenv()

API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN")

DATASET_ID = "gd_lpfll7v5hcqtkxl6l"

TRIGGER_URL = "https://api.brightdata.com/datasets/v3/trigger"
PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress"
SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"


def search_linkedin_jobs(
    query: str,
    location: str = "Morocco",
    country: str = "MA",
    limit: int = 20,
    post_date: PostDate = None,
) -> list[Job]:

    post_date = normalize_post_date(post_date)
    if not API_TOKEN:
        raise ValueError(
            "BRIGHTDATA_API_TOKEN is missing from .env"
        )

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }

    params = {
        "dataset_id": DATASET_ID,
        "type": "discover_new",
        "discover_by": "keyword",
        "limit_per_input": str(limit),
        "include_errors": "true",
    }

    payload = [
        {
            "keyword": query,
            "location": location,
            "country": country,
        }
    ]

    response = requests.post(
        TRIGGER_URL,
        headers=headers,
        params=params,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    snapshot_id = response.json()["snapshot_id"]

    print(
        f"LinkedIn snapshot created: {snapshot_id}"
    )

    # Wait until Bright Data finishes collecting.
    for _ in range(30):

        progress_response = requests.get(
            f"{PROGRESS_URL}/{snapshot_id}",
            headers=headers,
            timeout=20,
        )

        progress_response.raise_for_status()

        status = progress_response.json().get(
            "status"
        )

        print(
            f"LinkedIn status: {status}"
        )

        if status == "ready":
            break

        if status in {
            "failed",
            "error",
        }:
            raise RuntimeError(
                f"LinkedIn collection failed: {status}"
            )

        time.sleep(3)

    else:
        raise TimeoutError(
            "LinkedIn collection did not finish."
        )

    # Download results.
    result_response = requests.get(
        f"{SNAPSHOT_URL}/{snapshot_id}",
        headers=headers,
        params={
            "format": "json"
        },
        timeout=30,
    )

    result_response.raise_for_status()

    data = result_response.json()

    jobs: list[Job] = []

    for item in data:

        url = item.get("url")

        if not url:
            continue

        job = Job(
            title=item.get(
                "job_title",
                "Unknown",
            ),
            company=item.get(
                "company_name",
                "Unknown",
            ),
            location=item.get(
                "job_location"
            ),
            description=item.get("job_description") or item.get("job_description_formatted") or item.get("job_summary"),
            requirements=[],
            url=url,
            source="linkedin",
            application_url=item.get("application_url") or item.get("apply_link") or item.get("job_apply_link"),
            easy_apply=item.get("is_easy_apply") if isinstance(item.get("is_easy_apply"), bool) else (item.get("easy_apply") if isinstance(item.get("easy_apply"), bool) else None),
            published_at=parse_publication_date(item.get("job_posted_date") or item.get("date_posted")),
            remote=False,
        )

        if not matches_post_date(job.published_at, post_date):
            continue
        jobs.append(job)

    return jobs
