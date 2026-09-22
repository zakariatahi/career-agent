"""Read recruiter replies without changing Gmail messages or sending replies."""
import base64
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from uuid import uuid4

from bs4 import BeautifulSoup

from src.applications import store
from src.database import db
from src.tools.gmail import get_gmail_service


def strip_quoted_history(text):
    return re.split(r"(?im)^\s*(?:On [^\n]*(?:\n[^\n]*){0,2}?wrote:|Le [^\n]*(?:\n[^\n]*){0,2}?écrit\s*:|>+|[- ]*Original Message[- ]*)", text)[0].strip()


def message_text(payload):
    parts = payload.get("parts", [])
    if parts:
        plain = [part for part in parts if part.get("mimeType") == "text/plain"]
        return "\n".join(message_text(part) for part in (plain or parts))
    if payload.get("filename") or payload.get("mimeType") not in {"text/plain", "text/html"}:
        return ""
    data = payload.get("body", {}).get("data", "")
    try:
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""
    if payload.get("mimeType") == "text/html":
        soup = BeautifulSoup(text, "html.parser")
        for quoted in soup.select("blockquote, .gmail_quote"):
            quoted.decompose()
        text = soup.get_text("\n", strip=True)
    # Do not classify quoted application text as the recruiter's response.
    return strip_quoted_history(text)[:12000]


RESPONSE_LABELS = {"interview": "Accepted for interview", "rejected": "Rejected"}


def classify_reply(subject, text, headers):
    """Two decision classes; None means insufficient evidence."""
    value = f"{subject}\n{text}".casefold()
    if headers.get("auto-submitted", "no").lower() != "no" or re.search(
        r"delivery (?:status notification|failed)|undeliverable|address not found|out of office|automatic reply|réponse automatique|absence du bureau", value
    ):
        return None
    if re.search(
        r"not (?:be )?(?:moving|proceeding) forward|"
        r"(?:application|candidature).{0,60}(?:rejected|unsuccessful|not selected|pas retenue|non retenue|refusée|rejetée)|"
        r"regret to inform|ne pouvons.{0,40}suite|pas (?:été )?retenu[e]?|"
        r"not (?:been )?(?:accepted|selected|shortlisted|invited).{0,30}(?:interview|position)|"
        r"لن نتمكن من المضي|تم رفض طلبك", value, re.S
    ):
        return "rejected"
    if re.search(
        r"invite you.{0,60}interview|schedule.{0,50}interview|interview invitation|"
        r"(?:accepted|selected|shortlisted|invited).{0,45}(?:for|to).{0,20}(?:an? )?interview|"
        r"entretien.{0,50}(?:disponibilités|prévu|planifié)|invitons.{0,50}entretien|"
        r"(?:retenu|retenue|acceptée|accepté).{0,40}entretien|"
        r"ندعوك.{0,30}مقابلة|تم قبولك.{0,30}مقابلة", value, re.S
    ):
        return "interview"
    return None


def get_responses(job_url):
    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM inbox_messages WHERE job_url=? ORDER BY received_at DESC", (job_url,)).fetchall()
    return [{
        "message_id": row["message_id"], "sender": row["sender"],
        "subject": row["subject"], "received_at": row["received_at"],
        "response": strip_quoted_history(row["response_text"] or row["snippet"] or ""),
        "classification": ("accepted_for_interview" if row["suggested_status"] == "interview" else "rejected" if row["suggested_status"] == "rejected" else None),
        "classification_label": RESPONSE_LABELS.get(row["suggested_status"], "Needs review"),
    } for row in rows]


def match_application(message, applications):
    eligible = [app for app in applications if app["submitted_at"] <= message["received_at"]]
    thread_matches = [app for app in eligible if app.get("gmail_thread_id") and app["gmail_thread_id"] == message["thread_id"]]
    if len(thread_matches) == 1:
        return thread_matches[0]["job_url"]
    if thread_matches:
        return None
    candidates = []
    for app in eligible:
        if not app.get("recipient") or app["recipient"].casefold() != message["sender"].casefold():
            continue
        title_words = [word for word in re.findall(r"\w+", app["title"].casefold()) if len(word) > 2]
        content = f"{message['subject']} {message['snippet']}".casefold()
        if app["job_url"] in content or (len(title_words) >= 2 and all(re.search(r"\b" + re.escape(word) + r"\b", content) for word in title_words)):
            candidates.append(app["job_url"])
    return candidates[0] if len(candidates) == 1 else None


def review_queue():
    with db.get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM inbox_messages WHERE reviewed=0 ORDER BY received_at DESC")]


def apply_review(message_id, job_url=None, status=None):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM inbox_messages WHERE message_id=?", (message_id,)).fetchone()
    if row is None:
        raise ValueError("Message not found")
    if job_url:
        status = status or row["suggested_status"]
        if status not in RESPONSE_LABELS:
            raise ValueError("Choose Rejected or Accepted for interview")
        store.record_status(job_url, status, f"User classified Gmail response {message_id}: " + row["subject"])
    with db.get_connection() as conn:
        conn.execute("UPDATE inbox_messages SET reviewed=1,job_url=COALESCE(?,job_url),suggested_status=? WHERE message_id=?", (job_url, status, message_id))
        conn.commit()


def check_inbox(*, service=None, force=False):
    interval = store.get_setting("inbox_interval_minutes", 15) * 60
    last = store.get_setting("inbox_last_checked", 0)
    if not force and time.time() - last < interval:
        return {"skipped": True}
    # Serialize checks across Streamlit sessions and the optional worker.
    owner = uuid4().hex
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM application_settings WHERE key='inbox_lock_until'").fetchone()
        lease = json.loads(row[0]) if row else 0
        expires = lease.get("expires", 0) if isinstance(lease, dict) else lease
        if expires > time.time():
            return {"skipped": True}
        conn.execute("INSERT INTO application_settings(key,value) VALUES('inbox_lock_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps({"owner": owner, "expires": time.time() + 600}),))
        conn.commit()
    try:
        apps = store.tracked_applications()
        if not apps:
            return {"checked": 0, "updated": 0, "review": 0}
        service = service or get_gmail_service(read_inbox=True, interactive=False)
        me = service.users().getProfile(userId="me").execute()["emailAddress"].casefold()
        earliest = min(datetime.fromisoformat(app["submitted_at"]).timestamp() for app in apps)
        since = max(0, (last or earliest) - 300)
        started = time.time()
        query = f"in:inbox after:{int(since)} before:{int(started) + 1}"
        counts = {"checked": 0, "updated": 0, "review": 0}
        page = None
        for _ in range(100):
            kwargs = {"userId": "me", "q": query, "maxResults": 100}
            if page:
                kwargs["pageToken"] = page
            batch = service.users().messages().list(**kwargs).execute()
            for item in batch.get("messages", []):
                with db.get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    lease = json.loads(conn.execute("SELECT value FROM application_settings WHERE key='inbox_lock_until'").fetchone()[0])
                    if not isinstance(lease, dict) or lease.get("owner") != owner:
                        raise RuntimeError("Inbox check lease expired; retry the check")
                    conn.execute("UPDATE application_settings SET value=? WHERE key='inbox_lock_until'", (json.dumps({"owner": owner, "expires": time.time() + 600}),))
                    conn.commit()
                    existing = conn.execute("SELECT reviewed FROM inbox_messages WHERE message_id=?", (item["id"],)).fetchone()
                if existing and existing["reviewed"]:
                    continue
                raw = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
                headers = {header["name"].lower(): header["value"] for header in raw.get("payload", {}).get("headers", [])}
                sender = parseaddr(headers.get("from", ""))[1].casefold()
                if sender == me or "SENT" in raw.get("labelIds", []):
                    continue
                text = message_text(raw.get("payload", {}))
                message = {"message_id": raw["id"], "thread_id": raw.get("threadId"), "sender": sender,
                           "subject": headers.get("subject", ""), "snippet": text[:1500],
                           "received_at": datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=timezone.utc).isoformat()}
                counts["checked"] += 1
                job_url = match_application(message, apps)
                relevant = job_url or any(sender == (app.get("recipient") or "").casefold() for app in apps) or re.search(r"application|candidature|interview|entretien|recruit", message["subject"], re.I)
                if not relevant:
                    continue
                status = classify_reply(message["subject"], text, headers)
                with db.get_connection() as conn:
                    conn.execute("INSERT OR IGNORE INTO inbox_messages(message_id,thread_id,sender,subject,received_at,snippet,job_url,suggested_status) VALUES(?,?,?,?,?,?,?,?)", (message["message_id"], message["thread_id"], sender, message["subject"], message["received_at"], message["snippet"], job_url, status))
                    conn.execute("UPDATE inbox_messages SET response_text=? WHERE message_id=?", (text, message["message_id"]))
                    conn.commit()
                if job_url and status:
                    store.record_status(job_url, status, "Recruiter response: " + message["subject"], message_id=message["message_id"], received_at=message["received_at"])
                    with db.get_connection() as conn:
                        conn.execute("UPDATE inbox_messages SET reviewed=1,job_url=? WHERE message_id=?", (job_url, message["message_id"]))
                        conn.commit()
                    counts["updated"] += 1
                else:
                    counts["review"] += 1
            page = batch.get("nextPageToken")
            if not page:
                break
        else:
            raise RuntimeError("Inbox check reached its page limit; the checkpoint was preserved for retry")
        store.set_setting("inbox_last_checked", started)
        store.set_setting("inbox_last_result", counts)
        store.set_setting("inbox_error", None)
        return counts
    except Exception as error:
        store.set_setting("inbox_error", str(error))
        raise
    finally:
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM application_settings WHERE key='inbox_lock_until'").fetchone()
            lease = json.loads(row[0]) if row else None
            if isinstance(lease, dict) and lease.get("owner") == owner:
                conn.execute("UPDATE application_settings SET value='0' WHERE key='inbox_lock_until'")
                conn.commit()
