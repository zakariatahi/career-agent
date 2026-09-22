"""Shared inclusive publication start-date filtering for every job source."""

import re
from datetime import date, datetime, timezone

from src.models.job import Job

PostDate = date | str | None


def normalize_post_date(value: PostDate) -> date | None:
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise ValueError("post_date must be a date or YYYY-MM-DD string.")


def parse_publication_date(value: object) -> datetime | None:
    """Parse absolute timestamps only; unknown or relative dates stay unknown."""
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, date):
            result = datetime.combine(value, datetime.min.time())
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result = datetime.fromtimestamp(value, tz=timezone.utc)
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    # Date-only and timezone-free source values retain their reported day.
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def matches_post_date(published_at: object, post_date: date | None) -> bool:
    if post_date is None:
        return True
    published = parse_publication_date(published_at)
    return published is not None and published.date() >= post_date


def filter_jobs_by_date(jobs: list[Job], post_date: PostDate) -> list[Job]:
    selected = normalize_post_date(post_date)
    return [job for job in jobs if matches_post_date(job.published_at, selected)]
