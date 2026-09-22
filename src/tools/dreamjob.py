import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, matches_post_date


BASE_URL = "https://www.dreamjob.ma"
STAGE_URL = f"{BASE_URL}/stage/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/152.0 Safari/537.36"
    )
}


def _parse_date(text: str) -> datetime | None:
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)

    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y")
    except ValueError:
        return None


def _extract_location(text: str) -> str | None:
    cities = [
        "Casablanca",
        "Rabat",
        "Tanger",
        "Tétouan",
        "Marrakech",
        "Agadir",
        "Fès",
        "Meknès",
        "Kénitra",
        "Temara",
        "Témara",
        "Oujda",
        "Mohammedia",
        "Nouaceur",
    ]

    lower_text = text.lower()

    for city in cities:
        if city.lower() in lower_text:
            return city

    return None


def _matches_query(text: str, query: str) -> bool:
    if not query.strip():
        return True

    words = query.lower().split()
    text = text.lower()

    return any(word in text for word in words)


def search_dreamjob_jobs(
    query: str = "",
    limit: int = 20,
    pages: int = 3,
    post_date: PostDate = None,
) -> list[Job]:

    post_date = normalize_post_date(post_date)
    jobs: list[Job] = []

    for page in range(1, pages + 1):

        if page == 1:
            url = STAGE_URL
        else:
            url = f"{STAGE_URL}page/{page}/"

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "lxml",
        )

        # Dreamjob article titles are links inside h3 elements.
        headers = soup.find_all("h3")

        for header in headers:

            link = header.find("a")

            if not link:
                continue

            title = link.get_text(
                " ",
                strip=True,
            )

            href = link.get("href")

            if not title or not href:
                continue

            job_url = urljoin(
                BASE_URL,
                href,
            )

            container = header.parent

            text = container.get_text(
                " ",
                strip=True,
            )

            # Skip results unrelated to the query.
            if not _matches_query(
                f"{title} {text}",
                query,
            ):
                continue

            remote = any(
                keyword in text.lower()
                for keyword in [
                    "à distance",
                    "télétravail",
                    "remote",
                ]
            )

            job = Job(
                title=title,
                company="Unknown",
                location=_extract_location(text),
                description=text,
                requirements=[],
                url=job_url,
                source="dreamjob",
                remote=remote,
                published_at=_parse_date(text),
            )

            if not matches_post_date(job.published_at, post_date):
                continue
            jobs.append(job)

            if len(jobs) >= limit:
                return jobs

    return jobs
