"""Run once or poll while this process is running; never sends email."""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.applications import store
from src.applications.inbox import check_inbox
from src.database import db
from src.tools.gmail import get_gmail_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="Authorize Gmail send and read access")
    parser.add_argument("--watch", action="store_true", help="Check periodically until stopped; respects dashboard monitoring setting")
    args = parser.parse_args()
    db.DB_PATH = ROOT / "data" / "career_agent.db"
    db.init_db()
    if args.connect:
        get_gmail_service(read_inbox=True, interactive=True, force_reconnect=True)
        print("Gmail connected.")
        return 0
    while True:
        try:
            if not args.watch or store.get_setting("inbox_enabled", False):
                print(check_inbox(force=not args.watch), flush=True)
        except Exception as error:
            print(f"Inbox check failed: {error}", file=sys.stderr, flush=True)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
