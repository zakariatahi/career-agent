from datetime import datetime

from pydantic import BaseModel, Field


class Job(BaseModel):
    title: str
    company: str

    location: str | None = None
    description: str | None = None

    requirements: list[str] = Field(default_factory=list)

    url: str
    source: str

    remote: bool = False
    published_at: datetime | None = None
    application_email: str | None = None
    application_url: str | None = None
    easy_apply: bool | None = None
    original_post_url: str | None = None

    match_score: float | None = None

    match_reasons: list[str] = Field(
        default_factory=list
    )

    missing_skills: list[str] = Field(
        default_factory=list
    )

    recommendation: str | None = None
