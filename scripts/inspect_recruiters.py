import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.db import get_connection


def inspect_recruiters():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                name,
                title,
                company,
                location,
                country_code,
                score,
                linkedin_url,
                fetched_at
            FROM recruiters
            ORDER BY company, score DESC
            """
        ).fetchall()

    print(f"\nTotal recruiters: {len(rows)}\n")

    for row in rows:
        print("=" * 80)
        print("ID:", row["id"])
        print("Name:", row["name"])
        print("Title:", row["title"])
        print("Company:", row["company"])
        print("Location:", row["location"])
        print("Country:", row["country_code"])
        print("Score:", row["score"])
        print("LinkedIn:", row["linkedin_url"])
        print("Fetched at:", row["fetched_at"])


if __name__ == "__main__":
    inspect_recruiters()