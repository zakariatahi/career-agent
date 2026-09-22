import sqlite3
from datetime import datetime
from pathlib import Path
import json
import re
from contextlib import contextmanager

from src.models.profile import UserProfile

from src.models.job import Job
from datetime import datetime, timedelta


DB_PATH = Path("data/career_agent.db")


def _company_cache_pattern(company: str) -> str:
    core = re.sub(
        r"\b(?:morocco|maroc)\b",
        "",
        company,
        flags=re.IGNORECASE,
    ).strip()
    return f"%{core or company.strip()}%"


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY,
                target_roles TEXT NOT NULL DEFAULT '[]',
                skills TEXT NOT NULL DEFAULT '[]',
                education TEXT,
                experience TEXT NOT NULL DEFAULT '[]',
                preferred_locations TEXT NOT NULL DEFAULT '[]',
                remote INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                source TEXT,
                location TEXT,
                status TEXT NOT NULL DEFAULT 'found',
                match_score REAL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recruiters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                linkedin_url TEXT UNIQUE NOT NULL,
                name TEXT,
                title TEXT,
                company TEXT,
                location TEXT,
                country_code TEXT,
                followers INTEGER,
                connections INTEGER,
                score REAL,
                fetched_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discovered_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                description TEXT,
                source TEXT NOT NULL,
                remote INTEGER NOT NULL DEFAULT 0,
                discovered_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_posts (
                post_url TEXT PRIMARY KEY,
                is_job_offer INTEGER NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )

        recruiter_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(recruiters)").fetchall()
        }
        if "followers" not in recruiter_columns:
            conn.execute("ALTER TABLE recruiters ADD COLUMN followers INTEGER")
        if "connections" not in recruiter_columns:
            conn.execute("ALTER TABLE recruiters ADD COLUMN connections INTEGER")

        from src.applications.store import initialize
        initialize(conn)

        conn.commit()

def save_profile(profile: UserProfile):
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (
                id,
                target_roles,
                skills,
                education,
                experience,
                preferred_locations,
                remote,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(id)
            DO UPDATE SET
                target_roles = excluded.target_roles,
                skills = excluded.skills,
                education = excluded.education,
                experience = excluded.experience,
                preferred_locations = excluded.preferred_locations,
                remote = excluded.remote,
                updated_at = excluded.updated_at
            """,
            (
                json.dumps(profile.target_roles),
                json.dumps(profile.skills),
                profile.education,
                json.dumps(profile.experience),
                json.dumps(profile.preferred_locations),
                int(profile.remote),
                now,
            ),
        )

        conn.commit()

def load_profile() -> UserProfile | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM user_profile
            WHERE id = 1
            """
        ).fetchone()

    if row is None:
        return None

    return UserProfile(
        target_roles=json.loads(row["target_roles"] or "[]"),
        skills=json.loads(row["skills"] or "[]"),
        education=row["education"],
        experience=json.loads(row["experience"] or "[]"),
        preferred_locations=json.loads(
            row["preferred_locations"] or "[]"
        ),
        remote=bool(row["remote"]),
    )

def save_job(
    job: Job,
    status: str = "found",
    notes: str | None = None,
):
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO applications (
                job_url,
                title,
                company,
                source,
                location,
                status,
                match_score,
                notes,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(job_url)
            DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                source = excluded.source,
                location = excluded.location,
                status = CASE
                    WHEN excluded.status = 'found' THEN applications.status
                    WHEN excluded.status = 'shortlisted' AND applications.status IN ('applied','responded','interview','rejected','offer','delivery_failed') THEN applications.status
                    WHEN excluded.status = 'applied' AND applications.status IN ('responded','interview','rejected','offer') THEN applications.status
                    ELSE excluded.status
                END,
                match_score = excluded.match_score,
                notes = COALESCE(excluded.notes, applications.notes),
                updated_at = excluded.updated_at
            """,
            (
                job.url,
                job.title,
                job.company,
                job.source,
                job.location,
                status,
                job.match_score,
                notes,
                now,
                now,
            ),
        )

        conn.execute("INSERT INTO application_details(job_url,job_json) VALUES(?,?) ON CONFLICT(job_url) DO UPDATE SET job_json=excluded.job_json", (job.url, job.model_dump_json()))
        conn.commit()

VALID_STATUSES = {
    "found",
    "shortlisted",
    "applied",
    "interview",
    "rejected",
    "offer",
    "responded",
    "delivery_failed",
}


def update_application_status(
    job_url: str,
    status: str,
):
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status: {status}"
        )

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE applications
            SET status = ?, updated_at = ?
            WHERE job_url = ?
            """,
            (
                status,
                datetime.now().isoformat(),
                job_url,
            ),
        )

        conn.commit()
def get_applications(
    status: str | None = None,
):
    with get_connection() as conn:

        if status:
            rows = conn.execute(
                """
                SELECT *
                FROM applications
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()

        else:
            rows = conn.execute(
                """
                SELECT *
                FROM applications
                ORDER BY created_at DESC
                """
            ).fetchall()

    return [dict(row) for row in rows]




def save_recruiters(recruiters):
    now = datetime.now().isoformat()

    with get_connection() as conn:

        for recruiter in recruiters:
            conn.execute(
                """
                INSERT INTO recruiters (
                    linkedin_url,
                    name,
                    title,
                    company,
                    location,
                    country_code,
                    followers,
                    connections,
                    score,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(linkedin_url)
                DO UPDATE SET
                    name = excluded.name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    country_code = excluded.country_code,
                    followers = excluded.followers,
                    connections = excluded.connections,
                    score = excluded.score,
                    fetched_at = excluded.fetched_at
                """,
                (
                    recruiter.linkedin_url,
                    recruiter.name,
                    recruiter.title,
                    recruiter.company,
                    recruiter.location,
                    recruiter.country_code,
                    recruiter.followers,
                    recruiter.connections,
                    recruiter.score,
                    now,
                ),
            )

        conn.commit()

def get_cached_recruiters(
    company: str,
    max_age_days: int = 14,
    minimum_network: int = 500,
):

    cutoff = (
        datetime.now()
        - timedelta(days=max_age_days)
    ).isoformat()

    with get_connection() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM recruiters
            WHERE LOWER(company) LIKE LOWER(?)
            AND fetched_at >= ?
            AND (
                UPPER(COALESCE(country_code, '')) = 'MA'
                OR LOWER(COALESCE(location, '')) LIKE '%morocco%'
                OR LOWER(COALESCE(location, '')) LIKE '%maroc%'
                OR LOWER(COALESCE(location, '')) LIKE '%casablanca%'
                OR LOWER(COALESCE(location, '')) LIKE '%rabat%'
                OR LOWER(COALESCE(location, '')) LIKE '%tanger%'
                OR LOWER(COALESCE(location, '')) LIKE '%tangier%'
                OR LOWER(COALESCE(location, '')) LIKE '%marrakech%'
                OR LOWER(COALESCE(location, '')) LIKE '%agadir%'
                OR LOWER(COALESCE(location, '')) LIKE '%kenitra%'
                OR LOWER(COALESCE(location, '')) LIKE '%fes%'
            )
            AND (
                COALESCE(followers, 0) >= ?
                OR COALESCE(connections, 0) >= ?
            )
            ORDER BY score DESC
            """,
            (
                _company_cache_pattern(company),
                cutoff,
                minimum_network,
                minimum_network,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def prune_unqualified_recruiters(
    minimum_network: int = 500,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM recruiters
            WHERE NOT (
                (
                    UPPER(COALESCE(country_code, '')) = 'MA'
                    OR LOWER(COALESCE(location, '')) LIKE '%morocco%'
                    OR LOWER(COALESCE(location, '')) LIKE '%maroc%'
                    OR LOWER(COALESCE(location, '')) LIKE '%casablanca%'
                    OR LOWER(COALESCE(location, '')) LIKE '%rabat%'
                    OR LOWER(COALESCE(location, '')) LIKE '%tanger%'
                    OR LOWER(COALESCE(location, '')) LIKE '%tangier%'
                    OR LOWER(COALESCE(location, '')) LIKE '%marrakech%'
                    OR LOWER(COALESCE(location, '')) LIKE '%agadir%'
                    OR LOWER(COALESCE(location, '')) LIKE '%kenitra%'
                    OR LOWER(COALESCE(location, '')) LIKE '%fes%'
                )
                AND (
                    COALESCE(followers, 0) >= ?
                    OR COALESCE(connections, 0) >= ?
                )
            )
            """,
            (minimum_network, minimum_network),
        )
        conn.commit()
        return cursor.rowcount

def count_recruiters() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM recruiters
            """
        ).fetchone()

    return row["count"]

def count_recruiters_for_company(
    company: str,
) -> int:

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM recruiters
            WHERE LOWER(company) LIKE LOWER(?)
            """,
            (_company_cache_pattern(company),),
        ).fetchone()

    return row["count"]

def get_all_recruiters():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                name,
                title,
                company,
                location,
                country_code,
                followers,
                connections,
                score,
                linkedin_url,
                fetched_at
            FROM recruiters
            ORDER BY score DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]

def save_discovered_job(job: Job):
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO discovered_jobs (
                job_url,
                title,
                company,
                location,
                description,
                source,
                remote,
                discovered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(job_url)
            DO NOTHING
            """,
            (
                job.url,
                job.title,
                job.company,
                job.location,
                job.description,
                job.source,
                int(job.remote),
                now,
            ),
        )

        conn.commit()

def discovered_job_exists(
    job_url: str,
) -> bool:

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM discovered_jobs
            WHERE job_url = ?
            LIMIT 1
            """,
            (job_url,),
        ).fetchone()

    return row is not None


def get_discovered_jobs():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM discovered_jobs
            ORDER BY discovered_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_discovered_job(job_url: str) -> Job | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM discovered_jobs
            WHERE job_url = ?
            """,
            (job_url,),
        ).fetchone()

    if row is None:
        return None

    return Job(
        title=row["title"],
        company=row["company"] or "Unknown",
        location=row["location"],
        description=row["description"],
        requirements=[],
        url=row["job_url"],
        source=row["source"],
        remote=bool(row["remote"]),
    )

def processed_post_exists(
    post_url: str,
) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM processed_posts
            WHERE post_url = ?
            LIMIT 1
            """,
            (post_url,),
        ).fetchone()

    return row is not None

def save_processed_post(
    post_url: str,
    is_job_offer: bool,
):
    now = datetime.now().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO processed_posts (
                post_url,
                is_job_offer,
                processed_at
            )
            VALUES (?, ?, ?)

            ON CONFLICT(post_url)
            DO UPDATE SET
                is_job_offer = excluded.is_job_offer,
                processed_at = excluded.processed_at
            """,
            (
                post_url,
                int(is_job_offer),
                now,
            ),
        )

        conn.commit()
