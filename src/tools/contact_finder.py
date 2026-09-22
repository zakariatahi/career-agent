import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from pydantic import BaseModel
from src.models.job import Job

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_PATTERN = re.compile(r"https?://[^\s)>\]\\\"<]+")


class ApplicationContact(BaseModel):
    method: str
    email: str | None = None
    application_url: str | None = None
    confidence: float
    source: str


def extract_emails(text: str | None) -> list[str]:
    return list(dict.fromkeys(EMAIL_PATTERN.findall(text or "")))


def rank_emails(emails: list[str]) -> list[str]:
    keywords = ("recruit", "recrutement", "career", "jobs", "talent", "rh", "hr")
    return sorted(emails, key=lambda email: sum(word in email.lower() for word in keywords), reverse=True)


def is_linkedin_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def safe_application_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username:
            return url.strip().rstrip(".,;")
    except ValueError:
        pass
    return None


def find_application_contact(job: Job) -> ApplicationContact | None:
    description = job.description or ""
    soup = BeautifulSoup(description, "html.parser")
    text = soup.get_text(" ", strip=True)
    emails = extract_emails(job.application_email) or extract_emails(description)
    if emails:
        return ApplicationContact(method="email", email=rank_emails(emails)[0],
                                  confidence=0.95, source="job_metadata" if job.application_email else "job_description")
    explicit = safe_application_url(job.application_url)
    if explicit and not is_linkedin_url(explicit):
        return ApplicationContact(method="website", application_url=explicit, confidence=0.95, source="job_metadata")
    # Only a job listing with explicit evidence can be identified as Easy Apply.
    if is_linkedin_url(job.url) and (job.easy_apply is True or (
        "/jobs/" in urlsplit(job.url).path
        and re.search(r"\b(?:easy apply|candidature simplifiée)\b", text, re.I)
        and not re.search(r"\b(?:no|not|without)\s+easy apply\b", text, re.I)
    )):
        return ApplicationContact(method="linkedin_easy_apply", application_url=job.url, confidence=0.95, source="job_metadata" if job.easy_apply else "job_description")
    if explicit:
        return ApplicationContact(method="linkedin", application_url=explicit, confidence=0.8, source="job_metadata")
    for link in soup.find_all("a", href=True):
        url = safe_application_url(link["href"])
        if url and re.search(r"apply|postuler|candidature", link.get_text(" ", strip=True), re.I):
            return ApplicationContact(method="website", application_url=url, confidence=0.9, source="application_link")
    for candidate in URL_PATTERN.findall(description):
        url = safe_application_url(candidate)
        if url and not is_linkedin_url(url) and re.search(r"career|jobs?|apply|recruit|candidature|forms\.", url, re.I):
            return ApplicationContact(method="website", application_url=url, confidence=0.85, source="job_description")
    call_to_action = re.search(r"(?:apply|postuler|candidature)[^\n]{0,80}?(https?://[^\s<>]+)", text, re.I)
    if call_to_action:
        url = safe_application_url(call_to_action.group(1))
        if url:
            return ApplicationContact(method="linkedin" if is_linkedin_url(url) else "website", application_url=url, confidence=0.8, source="application_instruction")
    if safe_application_url(job.url):
        return ApplicationContact(
            method="linkedin" if is_linkedin_url(job.url) else "website",
            application_url=job.url, confidence=0.5 if is_linkedin_url(job.url) else 0.9,
            source="job_url",
        )
    return None
