from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class SearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    location: str = Field(default="Morocco", max_length=200)
    country: str = Field(default="MA", pattern=r"^[A-Z]{2}$")
    post_date: date | None = None
    limit_per_source: int = Field(default=5, ge=1, le=20)
    minimum_score: float = Field(default=30, ge=0, le=100)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    selected_index: int | None = None
    approved_indices: list[int] | None = None
    edited_changes: dict[str, str] | None = None
    cv_view: Literal["side", "changes"] | None = None
    recipient: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=998)
    body: str | None = Field(default=None, max_length=30000)


class ActionInput(BaseModel):
    revision: int
    approved: bool = False


class InboxSettings(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=1, le=1440)


class StatusInput(BaseModel):
    status: Literal["applied", "interview", "offer", "rejected"]


class ReplyInput(BaseModel):
    application_id: int
    status: Literal["interview", "rejected"]


class CVInput(BaseModel):
    source: str = Field(min_length=20, max_length=250000)
