import argparse
import sys
from pathlib import Path


# Running `python scripts/seed_recruiters.py` puts only `scripts/` on the
# import path. Add the repository root so project packages resolve exactly as
# they do when running main.py from the root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.database.db import count_recruiters, init_db

from src.tools.recruiter_seeder import (
    seed_recruiters,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate the local recruiter cache."
    )
    parser.add_argument("--target-count", type=int, default=50)
    parser.add_argument("--recruiters-per-company", type=int, default=4)
    parser.add_argument("--minimum-per-company", type=int, default=2)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup and show the current count without calling APIs.",
    )
    args = parser.parse_args()

    init_db()

    if args.dry_run:
        print(f"Recruiter seeder is ready. Current count: {count_recruiters()}")
        return 0

    seed_recruiters(
        target_count=args.target_count,
        recruiters_per_company=args.recruiters_per_company,
        minimum_per_company=args.minimum_per_company,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
