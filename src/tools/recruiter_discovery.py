from pydantic import BaseModel

from src.tools.tavily_search import search_web


class RecruiterCandidate(BaseModel):
    linkedin_url: str
    title: str | None = None
    snippet: str | None = None
    search_query: str


RECRUITER_QUERIES = [
    "Talent Acquisition",
    "Recruiter",
    "Recruitment Specialist",
    "HR Manager",
    "Talent Partner",
    "HR Business Partner",
]


def find_recruiter_candidates(
    company: str,
    max_results_per_query: int = 5,
) -> list[RecruiterCandidate]:

    candidates = []
    seen_urls = set()

    for role in RECRUITER_QUERIES:

        query = (
            f'site:linkedin.com/in '
            f'"{role}" '
            f'"{company}"'
        )

        results = search_web(
            query=query,
            max_results=max_results_per_query,
        )

        for result in results:

            url = result.get(
                "url",
                ""
            )

            if "linkedin.com/in/" not in url:
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            candidates.append(
                RecruiterCandidate(
                    linkedin_url=url,
                    title=result.get("title"),
                    snippet=result.get("content"),
                    search_query=query,
                )
            )

    return candidates