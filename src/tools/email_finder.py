import re


EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


def extract_emails(text: str | None) -> list[str]:
    if not text:
        return []

    emails = EMAIL_PATTERN.findall(text)

    return list(dict.fromkeys(emails))

def rank_emails(emails: list[str]) -> list[str]:
    preferred_keywords = [
        "recruit",
        "recrutement",
        "career",
        "careers",
        "jobs",
        "rh",
        "hr",
        "talent",
    ]

    def score(email: str):
        email_lower = email.lower()

        return sum(
            keyword in email_lower
            for keyword in preferred_keywords
        )

    return sorted(
        emails,
        key=score,
        reverse=True,
    )