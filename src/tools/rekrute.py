import re
from datetime import datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, matches_post_date


BASE_URL = "https://www.rekrute.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/152.0 Safari/537.36"
    )
}


def _extract_location(title: str) -> str | None:
    """
    Example:
    'Data Analyst | Rabat (Maroc)'
        -> 'Rabat'
    """
    if "|" not in title:
        return None

    location = title.split("|")[-1]
    location = re.sub(r"\(Maroc\)", "", location, flags=re.IGNORECASE)

    return location.strip()


def _clean_title(title: str) -> str:
    if "|" in title:
        return title.split("|")[0].strip()

    return title.strip()


def _parse_date(text: str) -> datetime | None:
    """
    Example:
    'Publication : du 28/08/2026 au 28/10/2026'
    """
    match = re.search(r"du\s+(\d{2}/\d{2}/\d{4})", text)

    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y")
    except ValueError:
        return None


def search_rekrute_jobs(
    query: str,
    limit: int = 20,
    post_date: PostDate = None,
) -> list[Job]:

    post_date = normalize_post_date(post_date)
    search_query = quote(query.strip())

    url = f"{BASE_URL}/emploi-{search_query}"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=15,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    jobs: list[Job] = []

    # ReKrute job titles are generally contained in h2 elements.
    job_headers = soup.find_all("h2")

    for header in job_headers:

        link = header.find("a")

        if not link:
            continue

        raw_title = link.get_text(" ", strip=True)

        # Ignore unrelated h2 elements.
        if not raw_title:
            continue

        href = link.get("href")

        if not href:
            continue

        if href.startswith("/"):
            job_url = BASE_URL + href
        else:
            job_url = href

        title = _clean_title(raw_title)
        location = _extract_location(raw_title)

        # Try to collect the surrounding job block.
        container = header.parent

        container_text = container.get_text(
            " ",
            strip=True,
        )

        published_at = _parse_date(container_text)

        lower_text = container_text.lower()

        remote = (
            "télétravail : hybride" in lower_text
            or "télétravail : oui" in lower_text
        )

        job = Job(
            title=title,
            company="Unknown",
            location=location,
            description=container_text,
            requirements=[],
            url=job_url,
            source="rekrute",
            remote=remote,
            published_at=published_at,
        )

        if not matches_post_date(job.published_at, post_date):
            continue
        jobs.append(job)

        if len(jobs) >= limit:
            break

    return jobs
