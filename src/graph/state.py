# src/graph/state.py

from typing import TypedDict

from src.agents.cv_tailor import CVChange
from src.agents.email_drafter import ApplicationEmail
from src.models.job import Job
from src.models.profile import UserProfile
from src.tools.contact_finder import ApplicationContact




class CareerState(
    TypedDict,
    total=False,
):
    # Search configuration
    query: str
    location: str
    country: str
    post_date: str | None
    verbose_search: bool
    no_jobs_message: str | None
    limit_per_source: int
    prefilter_top_k: int
    minimum_score: float

    # Candidate
    profile: UserProfile

    # Jobs
    jobs: list[Job]
    ranked_jobs: list[Job]
    selected_job: Job

    # CV
    cv_path: str
    tailored_cv_path: str
    cv_changes: list[CVChange]
    approved_changes: list[CVChange]
    cv_updated: bool
    tailored_cv_pdf_path: str

    # Email
    email_draft: ApplicationEmail
    email_approved: bool

    application_contact: ApplicationContact | None
    recipient_email: str | None

    email_sent: bool
    gmail_message_id: str | None
    gmail_thread_id: str | None
