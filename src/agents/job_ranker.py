from dotenv import load_dotenv
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from src.models.job import Job
from src.models.profile import UserProfile


load_dotenv()


class JobMatchResult(BaseModel):
    score: float = Field(ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    recommendation: str


llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
)


structured_llm = llm.with_structured_output(
    JobMatchResult,
    method="json_schema",
    strict=True,
)


def build_job_prompt(
    profile: UserProfile,
    job: Job,
) -> str:

    description = truncate_text(
        job.description,
        max_chars=4000,
    )

    requirements = truncate_text(
        "\n".join(job.requirements),
        max_chars=1500,
    )

    return f"""
Evaluate how well this job matches the candidate.

CANDIDATE

Target roles:
{profile.target_roles}

Skills:
{profile.skills}

Education:
{profile.education}

Experience:
{profile.experience}

Preferred locations:
{profile.preferred_locations}

Remote accepted:
{profile.remote}


JOB

Title:
{job.title}

Company:
{job.company}

Location:
{job.location}

Remote:
{job.remote}

Description:
{description}

Requirements:
{requirements}


Give a score from 0 to 100.

Consider:
1. Role relevance
2. Technical skill match
3. Education compatibility
4. Experience compatibility
5. Location compatibility
6. Remote compatibility

Return:
- score (required)
- positive reasons
- missing skills
- short recommendation (required)

Every response must include all four fields: score, reasons,
missing_skills, and recommendation.
"""


def evaluate_job(
    profile: UserProfile,
    job: Job,
    max_attempts: int = 3,
) -> JobMatchResult:

    prompt = build_job_prompt(profile, job)
    last_error: Exception | None = None

    for _ in range(max_attempts):
        try:
            result = structured_llm.invoke(prompt)
            return JobMatchResult.model_validate(result)
        except Exception as error:
            last_error = error

    raise RuntimeError(
        "The model could not produce a valid job ranking after "
        f"{max_attempts} attempts."
    ) from last_error

def rank_jobs(
    profile: UserProfile,
    jobs: list[Job],
    minimum_score: float = 0,
) -> list[Job]:

    ranked_jobs: list[Job] = []

    for job in jobs:
        try:
            result = evaluate_job(
                profile=profile,
                job=job,
            )

            job.match_score = result.score
            job.match_reasons = result.reasons
            job.missing_skills = result.missing_skills
            job.recommendation = result.recommendation

            if result.score >= minimum_score:
                ranked_jobs.append(job)

        except Exception as error:
            print(
                f"[Ranking error] {job.title}: {error}"
            )

    ranked_jobs.sort(
        key=lambda job: job.match_score or 0,
        reverse=True,
    )

    return ranked_jobs

def truncate_text(
    text: str | None,
    max_chars: int = 4000,
) -> str:

    if not text:
        return ""

    text = text.strip()

    if len(text) <= max_chars:
        return text

    return text[:max_chars]
