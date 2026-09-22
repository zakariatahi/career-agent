from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = ROOT_DIR / os.getenv("DATABASE_PATH", "data/career_agent.db")
REMOTIVE_API_URL = os.getenv("REMOTIVE_API_URL", "https://remotive.com/api/remote-jobs")
ARBEITNOW_API_URL = os.getenv(
    "ARBEITNOW_API_URL", "https://www.arbeitnow.com/api/job-board-api"
)
