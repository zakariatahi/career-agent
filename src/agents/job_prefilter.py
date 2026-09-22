from src.models.job import Job
from src.models.profile import UserProfile


def _normalize(text: str | None) -> str:
    return (text or "").lower().strip()


def _keyword_overlap(
    keywords: list[str],
    text: str,
) -> int:
    text = _normalize(text)

    matches = 0

    for keyword in keywords:
        if _normalize(keyword) in text:
            matches += 1

    return matches


def prefilter_score(
    profile: UserProfile,
    job: Job,
) -> float:

    score = 0.0

    title = _normalize(job.title)
    description = _normalize(job.description)
    location = _normalize(job.location)

    full_text = f"{title} {description}"

    # 1. Target role match
    role_matches = _keyword_overlap(
        profile.target_roles,
        title,
    )

    score += role_matches * 20

    # 2. Skill overlap
    skill_matches = _keyword_overlap(
        profile.skills,
        full_text,
    )

    score += skill_matches * 5

    # 3. Preferred location
    for preferred_location in profile.preferred_locations:
        if _normalize(preferred_location) in location:
            score += 15
            break

    # 4. Remote compatibility
    if profile.remote and job.remote:
        score += 10

    # Keep score bounded
    return min(score, 100)
def prefilter_jobs(
    profile: UserProfile,
    jobs: list[Job],
    top_k: int = 15,
) -> list[Job]:

    scored_jobs = []

    for job in jobs:
        score = prefilter_score(
            profile,
            job,
        )

        scored_jobs.append(
            (score, job)
        )

    scored_jobs.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        job
        for score, job in scored_jobs[:top_k]
    ]