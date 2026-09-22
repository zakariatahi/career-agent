from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from src.models.job import Job


class CVChange(BaseModel):
    original_text: str
    proposed_text: str
    reason: str


class CVChangePlan(BaseModel):
    changes: list[CVChange] = Field(default_factory=list)


def propose_cv_changes(
    cv_path: str,
    job: Job,
    max_attempts: int = 3,
) -> CVChangePlan:

    load_dotenv()
    llm = ChatGroq(
        model="openai/gpt-oss-120b",
        temperature=0,
    )
    structured_llm = llm.with_structured_output(
        CVChangePlan,
        method="json_schema",
        strict=True,
    )

    latex_cv = Path(cv_path).read_text(
        encoding="utf-8"
    )

    prompt = f"""
You are tailoring a LaTeX CV for a specific job.

Do NOT rewrite the whole CV yet.

Your task is only to propose useful textual changes.

Rules:
- preserve all facts
- do not invent skills
- do not invent experience
- do not invent technologies
- do not invent metrics
- preserve the original writing style
- only propose changes that materially improve relevance
- original_text must be copied exactly from the supplied CV
- do not propose LaTeX formatting changes

TARGET JOB

Title:
{job.title}

Company:
{job.company}

Description:
{job.description}

Requirements:
{job.requirements}


ORIGINAL LATEX CV

{latex_cv}


Return a complete CVChangePlan matching the supplied schema.
Every change must contain original_text, proposed_text, and reason.
"""

    last_error: Exception | None = None

    for _ in range(max_attempts):
        try:
            result = structured_llm.invoke(prompt)
            return CVChangePlan.model_validate(result)
        except Exception as error:
            last_error = error

    raise RuntimeError(
        "The model could not produce a valid CV change plan after "
        f"{max_attempts} attempts."
    ) from last_error


def apply_cv_changes(
    cv_path: str,
    output_path: str,
    approved_changes: list[CVChange],
):

    latex_cv = Path(cv_path).read_text(
        encoding="utf-8"
    )

    modified_cv = latex_cv

    for change in approved_changes:

        if change.original_text not in modified_cv:
            print(
                f"Warning: text not found:\n"
                f"{change.original_text}"
            )
            continue

        modified_cv = modified_cv.replace(
            change.original_text,
            change.proposed_text,
            1,
        )

    Path(output_path).write_text(
        modified_cv,
        encoding="utf-8",
    )

    return modified_cv
