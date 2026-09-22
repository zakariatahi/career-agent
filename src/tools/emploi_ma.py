from datetime import datetime
from pathlib import Path
import re
from urllib.parse import quote

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from src.models.job import Job
from src.tools.post_dates import PostDate, normalize_post_date, filter_jobs_by_date


BASE_URL = "https://www.emploi.ma"

BLOCK_MARKERS = (
    "access denied",
    "captcha",
    "cloudflare",
    "checking your browser",
    "just a moment",
    "verify you are human",
    "vérifiez que vous êtes humain",
    "vous avez été bloqué",
)

DEFAULT_PROFILE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "chrome-emploi-profile"
)


class EmploiMaScraperError(RuntimeError):
    """Raised when Emploi.ma cannot return a usable job-results page."""


def _parse_date(text: str) -> datetime | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%d.%m.%Y")
    except ValueError:
        return None


def _is_block_page(title: str, html: str) -> bool:
    page_text = f"{title}\n{html[:100_000]}".lower()
    return any(marker in page_text for marker in BLOCK_MARKERS)


def _parse_jobs(html: str, limit: int | None) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for header in soup.select("h3"):
        link = header.find("a", href=True)
        if link is None:
            continue

        title = link.get_text(" ", strip=True)
        href = str(link["href"]).strip()
        if not title or not href:
            continue

        job_url = f"{BASE_URL}{href}" if href.startswith("/") else href
        if job_url in seen_urls:
            continue

        container = header.find_parent(
            ["article", "li", "div"],
            class_=re.compile(r"(job|card|offer|listing)", re.IGNORECASE),
        ) or header.parent
        text = container.get_text("\n", strip=True)
        strings = list(container.stripped_strings)

        company_element = container.select_one(
            ".company-name, .company, .card-job-company, [class*='company']"
        )
        company = (
            company_element.get_text(" ", strip=True)
            if company_element is not None
            else "Unknown"
        )
        if company == "Unknown" and title in strings:
            title_index = strings.index(title)
            if title_index + 1 < len(strings):
                company = strings[title_index + 1]

        region_match = re.search(
            r"Région\s+de\s*:\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
        location = region_match.group(1).strip() if region_match else None
        lowered_text = text.casefold()
        remote = any(
            keyword in lowered_text
            for keyword in ("télétravail", "remote", "à distance")
        )

        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                description=text,
                url=job_url,
                source="emploi.ma",
                remote=remote,
                published_at=_parse_date(text),
            )
        )
        seen_urls.add(job_url)

        if limit is not None and len(jobs) >= limit:
            break

    return jobs


def search_emploi_ma_jobs(
    query: str,
    limit: int = 20,
    verification_timeout: int = 0,
    profile_dir: Path | str = DEFAULT_PROFILE_DIR,
    post_date: PostDate = None,
) -> list[Job]:
    post_date = normalize_post_date(post_date)
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1:
        return []

    search_query = quote(query.strip(), safe="")
    url = f"{BASE_URL}/recherche-jobs-maroc/{search_query}"

    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=fr-FR")

    profile_path = Path(profile_dir).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_path}")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(25)
        driver.get(url)

        WebDriverWait(driver, 15).until(
            lambda current_driver: current_driver.execute_script(
                "return document.readyState"
            )
            in {"interactive", "complete"}
            and bool(current_driver.find_elements("tag name", "body"))
        )

        html = driver.page_source
        title = driver.title

        if _is_block_page(title, html):
            if verification_timeout <= 0:
                raise EmploiMaScraperError(
                    "Emploi.ma returned HTTP 403 / Cloudflare browser "
                    "verification. Run again with verification_timeout=120 "
                    "and complete the check in the visible Chrome window."
                )

            print(
                "Emploi.ma requested browser verification. "
                f"Complete it in Chrome within {verification_timeout} "
                "seconds...",
                flush=True,
            )
            try:
                WebDriverWait(
                    driver,
                    verification_timeout,
                    poll_frequency=1,
                ).until(
                    lambda current_driver: not _is_block_page(
                        current_driver.title,
                        current_driver.page_source,
                    )
                )
            except TimeoutException as error:
                raise EmploiMaScraperError(
                    "Emploi.ma browser verification was not cleared within "
                    f"{verification_timeout} seconds."
                ) from error

            html = driver.page_source
            title = driver.title

        jobs = _parse_jobs(html, limit if post_date is None else None)
        if not jobs:
            raise EmploiMaScraperError(
                "Emploi.ma loaded, but no job cards were found. "
                f"Page title: {title!r}; URL: {driver.current_url!r}. "
                "The site may have blocked the request or changed its HTML."
            )

        return filter_jobs_by_date(jobs, post_date)[:limit]
    except TimeoutException as error:
        raise EmploiMaScraperError(
            f"Timed out while loading Emploi.ma: {url}"
        ) from error
    except WebDriverException as error:
        raise EmploiMaScraperError(
            f"Chrome could not load Emploi.ma: {error.msg}"
        ) from error
    finally:
        if driver is not None:
            driver.quit()
