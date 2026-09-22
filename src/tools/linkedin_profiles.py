import os
import re
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from src.database.db import get_cached_recruiters, save_recruiters
from src.tools.recruiter_discovery import find_recruiter_candidates


DEFAULT_DATASET_ID = "gd_l1viktl72bvl7bjuj0"
SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"
PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress"
SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"

READY_STATUSES = {"ready", "completed", "complete", "success", "succeeded"}
FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

RECRUITER_KEYWORDS = (
    "talent acquisition",
    "talent partner",
    "talent specialist",
    "recruiter",
    "recruitment",
    "recruteur",
    "recruteuse",
    "recrutement",
    "charge de recrutement",
    "responsable recrutement",
    "human resources",
    "hr manager",
    "hr business partner",
    "people and culture",
    "people partner",
    "campus recruiter",
    "university recruiter",
    "ressources humaines",
    "responsable rh",
    "directeur rh",
    "directrice rh",
)

COMPANY_NOISE_WORDS = {
    "company",
    "corporation",
    "corp",
    "group",
    "groupe",
    "holding",
    "inc",
    "llc",
    "ltd",
    "maroc",
    "morocco",
    "plc",
    "sa",
    "sarl",
    "sas",
}

MOROCCAN_LOCATIONS = {
    "agadir",
    "casablanca",
    "fes",
    "kenitra",
    "marrakech",
    "maroc",
    "morocco",
    "rabat",
    "sale",
    "tanger",
    "tangier",
    "tetouan",
}


class RecruiterProfile(BaseModel):
    name: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    country_code: str | None = None
    followers: int | None = Field(default=None, ge=0)
    connections: int | None = Field(default=None, ge=0)
    linkedin_url: str
    score: float = Field(default=0, ge=0, le=100)
    company_match: bool = False
    recruiter_role_match: bool = False


def _normalized_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _company_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in _normalized_text(value).split()
        if token not in COMPANY_NOISE_WORDS
    }


def company_matches(profile_company: str | None, target_company: str) -> bool:
    profile_tokens = _company_tokens(profile_company)
    target_tokens = _company_tokens(target_company)

    if not profile_tokens or not target_tokens:
        return False

    # A geographic or legal suffix should not make the same employer fail.
    if profile_tokens <= target_tokens or target_tokens <= profile_tokens:
        return True

    overlap = profile_tokens & target_tokens
    return len(overlap) >= 2 and (
        len(overlap) / min(len(profile_tokens), len(target_tokens)) >= 0.8
    )


def is_recruiter_title(title: str | None) -> bool:
    normalized_title = _normalized_text(title)
    return bool(normalized_title) and any(
        keyword in normalized_title for keyword in RECRUITER_KEYWORDS
    )


def is_morocco_profile(profile: RecruiterProfile) -> bool:
    if (profile.country_code or "").strip().upper() == "MA":
        return True

    location_tokens = set(_normalized_text(profile.location).split())
    return bool(location_tokens & MOROCCAN_LOCATIONS)


def _parse_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if not isinstance(value, str):
        return None

    normalized = value.strip().casefold().replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)([km]?)", normalized)
    if not match:
        return None

    number = float(match.group(1))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)]
    return int(number * multiplier)


def has_minimum_network(
    profile: RecruiterProfile,
    minimum: int = 500,
) -> bool:
    return max(profile.followers or 0, profile.connections or 0) >= minimum


def recruiter_score(profile: RecruiterProfile, target_company: str) -> float:
    profile.company_match = company_matches(profile.company, target_company)
    profile.recruiter_role_match = is_recruiter_title(profile.title)

    score = 0.0
    if profile.company_match:
        score += 55
    if profile.recruiter_role_match:
        score += 35
    if is_morocco_profile(profile):
        score += 10

    return score


def _request_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def canonicalize_linkedin_url(url: str) -> str:
    value = url.strip()
    if not value:
        return ""

    parts = urlsplit(value)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return value
    path = re.sub(r"/(?:en|fr)$", "", path, flags=re.IGNORECASE)
    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def _profile_url_key(url: str) -> str:
    return canonicalize_linkedin_url(url).casefold()


def _records_from_response(data: object) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in ("data", "records", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if any(key in data for key in ("url", "linkedin_url", "name", "position")):
            return [data]

    return []


def _text_field(value: object, *keys: str) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def normalize_profiles(
    data: object,
    input_urls: list[str],
) -> list[RecruiterProfile]:
    records = _records_from_response(data)
    profiles: list[RecruiterProfile] = []
    seen_urls: set[str] = set()
    single_fallback = input_urls[0] if len(input_urls) == 1 else None

    for item in records:
        input_value = item.get("input")
        linkedin_url = (
            _text_field(item.get("url"))
            or _text_field(item.get("linkedin_url"))
            or _text_field(item.get("profile_url"))
            or _text_field(input_value, "url", "linkedin_url")
            or single_fallback
        )

        if not linkedin_url:
            # Positional fallback is unsafe because snapshot rows may be reordered.
            continue

        linkedin_url = canonicalize_linkedin_url(linkedin_url)
        if not linkedin_url or linkedin_url in seen_urls:
            continue

        current_company = item.get("current_company")
        company = (
            _text_field(current_company, "name", "company_name")
            or _text_field(item.get("company"), "name", "company_name")
            or _text_field(item.get("company_name"))
            or _text_field(item.get("current_company_name"))
        )

        location = (
            _text_field(item.get("city"), "name")
            or _text_field(item.get("location"), "name", "city")
        )
        title = (
            _text_field(item.get("position"), "name", "title")
            or _text_field(current_company, "position", "title", "job_title")
            or _text_field(item.get("title"))
            or _text_field(item.get("headline"))
        )
        name = _text_field(item.get("name"))
        if not name:
            first_name = _text_field(item.get("first_name")) or ""
            last_name = _text_field(item.get("last_name")) or ""
            name = f"{first_name} {last_name}".strip() or None

        profiles.append(
            RecruiterProfile(
                name=name,
                title=title,
                company=company,
                location=location,
                country_code=_text_field(item.get("country_code")),
                followers=_parse_count(
                    item.get("followers")
                    or item.get("followers_count")
                    or item.get("num_followers")
                ),
                connections=_parse_count(
                    item.get("connections")
                    or item.get("connections_count")
                    or item.get("num_connections")
                ),
                linkedin_url=linkedin_url,
            )
        )
        seen_urls.add(linkedin_url)

    return profiles


def scrape_linkedin_profiles(
    urls: list[str],
    *,
    max_wait_seconds: float = 300,
    poll_interval_seconds: float = 3,
    session: requests.Session | None = None,
) -> list[RecruiterProfile]:
    load_dotenv()
    api_token = os.getenv("BRIGHTDATA_API_TOKEN")
    dataset_id = os.getenv("BRIGHTDATA_LINKEDIN_DATASET_ID", DEFAULT_DATASET_ID)

    if not api_token:
        raise ValueError("BRIGHTDATA_API_TOKEN is missing.")
    if max_wait_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("Polling duration and interval must be positive.")

    unique_urls = list(
        dict.fromkeys(
            canonical
            for url in urls
            if (canonical := canonicalize_linkedin_url(url))
        )
    )
    if not unique_urls:
        return []

    client = session or _request_session()
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    response = client.post(
        SCRAPE_URL,
        headers=headers,
        params={
            "dataset_id": dataset_id,
            "format": "json",
            "include_errors": "true",
        },
        json=[{"url": url} for url in unique_urls],
        # Bright Data's synchronous endpoint may take up to one minute before
        # returning records or switching the request to a snapshot.
        timeout=(10, 75),
    )
    response.raise_for_status()
    data = response.json()

    if isinstance(data, list) or (
        isinstance(data, dict) and not data.get("snapshot_id")
    ):
        return normalize_profiles(data, unique_urls)

    if not isinstance(data, dict) or not data.get("snapshot_id"):
        raise RuntimeError(f"Unexpected Bright Data response: {data}")

    snapshot_id = str(data["snapshot_id"])
    deadline = time.monotonic() + max_wait_seconds
    last_status: str | None = None

    print(f"LinkedIn profile snapshot: {snapshot_id}")

    while True:
        progress_response = client.get(
            f"{PROGRESS_URL}/{snapshot_id}",
            headers=headers,
            timeout=(10, 20),
        )
        progress_response.raise_for_status()
        progress_data = progress_response.json()
        status = str(progress_data.get("status", "unknown")).strip().lower()

        if status != last_status:
            print(f"LinkedIn profile status: {status}")
            last_status = status

        if status in READY_STATUSES:
            break
        if status in FAILED_STATUSES:
            message = progress_data.get("message") or progress_data.get("error") or status
            raise RuntimeError(f"LinkedIn profile scraping failed: {message}")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"LinkedIn profile snapshot {snapshot_id} did not finish within "
                f"{max_wait_seconds:g} seconds (last status: {status})."
            )
        time.sleep(min(poll_interval_seconds, remaining))

    download_response = client.get(
        f"{SNAPSHOT_URL}/{snapshot_id}",
        headers=headers,
        params={"format": "json"},
        timeout=(10, 30),
    )
    download_response.raise_for_status()
    return normalize_profiles(download_response.json(), unique_urls)


def find_verified_recruiters(
    company: str,
    top_k: int = 5,
    minimum_network: int = 500,
):
    cached = get_cached_recruiters(
        company=company,
        max_age_days=14,
        minimum_network=minimum_network,
    )

    if cached:
        print(
            f"Using cached recruiters for {company}"
        )

        return [
            RecruiterProfile(
                name=row["name"],
                title=row["title"],
                company=row["company"],
                location=row["location"],
                country_code=row["country_code"],
                followers=row["followers"],
                connections=row["connections"],
                linkedin_url=row["linkedin_url"],
                score=row["score"],
            )
            for row in cached[:top_k]
        ]

    print(
        f"No fresh recruiter cache for {company}"
    )

    candidates = find_recruiter_candidates(
        company=company,
        max_results_per_query=3,
    )

    if not candidates:
        return []

    discovery_titles: dict[str, str] = {}
    for candidate in candidates:
        title = (candidate.title or "").strip()
        if title:
            discovery_titles.setdefault(
                _profile_url_key(candidate.linkedin_url),
                title,
            )

    profiles = scrape_linkedin_profiles(
        [
            candidate.linkedin_url
            for candidate in candidates
        ]
    )

    for profile in profiles:
        if not profile.title:
            profile.title = discovery_titles.get(
                _profile_url_key(profile.linkedin_url)
            )
        profile.score = recruiter_score(
            profile=profile,
            target_company=company,
        )

    profiles.sort(
        key=lambda p: p.score,
        reverse=True,
    )

    verified = [
        profile
        for profile in profiles
        if profile.company_match
        and profile.recruiter_role_match
        and is_morocco_profile(profile)
        and has_minimum_network(profile, minimum_network)
    ][:top_k]

    save_recruiters(
        verified
    )

    return verified
