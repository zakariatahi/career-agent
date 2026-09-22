import json
from datetime import datetime, timezone
from src.database import db


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS application_details (
            job_url TEXT PRIMARY KEY, job_json TEXT, method TEXT, recipient TEXT,
            application_url TEXT, subject TEXT, body TEXT, attachment_path TEXT,
            gmail_message_id TEXT, gmail_thread_id TEXT, submitted_at TEXT,
            last_response_at TEXT
        );
        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY, job_url TEXT, status TEXT, detail TEXT,
            message_id TEXT UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inbox_messages (
            message_id TEXT PRIMARY KEY, thread_id TEXT, sender TEXT, subject TEXT,
            received_at TEXT, snippet TEXT, job_url TEXT, suggested_status TEXT,
            reviewed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS application_settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS send_attempts (
            job_url TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL,
            message_id TEXT, thread_id TEXT, error TEXT
        );
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(inbox_messages)")}
    if "response_text" not in columns:
        conn.execute("ALTER TABLE inbox_messages ADD COLUMN response_text TEXT")
        conn.execute("UPDATE inbox_messages SET response_text=snippet")
        conn.execute("UPDATE inbox_messages SET reviewed=0, suggested_status=NULL WHERE suggested_status NOT IN ('interview','rejected')")


DETAIL_FIELDS = {"job_json", "method", "recipient", "application_url", "subject", "body", "attachment_path", "gmail_message_id", "gmail_thread_id", "submitted_at", "last_response_at"}


def save_details(job_url, **fields):
    if not fields or not set(fields) <= DETAIL_FIELDS:
        raise ValueError("Unsupported application detail fields")
    columns = list(fields)
    with db.get_connection() as conn:
        conn.execute(f"INSERT INTO application_details (job_url, {', '.join(columns)}) VALUES ({', '.join('?' for _ in range(len(columns)+1))}) ON CONFLICT(job_url) DO UPDATE SET " + ", ".join(f"{key}=excluded.{key}" for key in columns), [job_url, *fields.values()])
        conn.commit()


def get_details(job_url):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM application_details WHERE job_url=?", (job_url,)).fetchone()
    return dict(row) if row else {}


def tracked_applications():
    with db.get_connection() as conn:
        rows = conn.execute("SELECT a.*, d.recipient, d.gmail_thread_id, d.gmail_message_id, d.submitted_at FROM applications a JOIN application_details d ON a.job_url=d.job_url WHERE d.submitted_at IS NOT NULL").fetchall()
    return [dict(row) for row in rows]


def record_status(job_url, status, detail, message_id=None, received_at=None):
    if status not in db.VALID_STATUSES:
        raise ValueError("Unknown application status")
    timestamp = received_at or now()
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM applications WHERE job_url=?", (job_url,)).fetchone()
        if current is None:
            raise ValueError("Application not found")
        if message_id and conn.execute("SELECT 1 FROM application_events WHERE message_id=?", (message_id,)).fetchone():
            return
        # Generic replies/acknowledgements never roll back a later stage.
        preserve = message_id and (
            current["status"] in {"offer", "rejected"}
            or (current["status"] == "interview" and status in {"applied", "responded"})
            or (current["status"] == "responded" and status == "applied")
        )
        previous = conn.execute("SELECT last_response_at FROM application_details WHERE job_url=?", (job_url,)).fetchone()
        stale = message_id and previous and previous["last_response_at"] and timestamp < previous["last_response_at"]
        conn.execute("INSERT INTO application_events(job_url,status,detail,message_id,created_at) VALUES(?,?,?,?,?)", (job_url, status, detail, message_id, timestamp))
        if not preserve and not stale:
            conn.execute("UPDATE applications SET status=?,updated_at=? WHERE job_url=?", (status, now(), job_url))
        if message_id and not stale:
            conn.execute("UPDATE application_details SET last_response_at=? WHERE job_url=?", (timestamp, job_url))
        conn.commit()


def events(job_url):
    with db.get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM application_events WHERE job_url=? ORDER BY created_at DESC", (job_url,))]


def get_setting(key, default=None):
    with db.get_connection() as conn:
        row = conn.execute("SELECT value FROM application_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(key, value):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO application_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        conn.commit()
