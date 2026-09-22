"""Prepare applications and send only a reviewed, persisted draft."""
import re
from datetime import datetime, timezone
from email.utils import getaddresses
from pathlib import Path
from uuid import uuid4

from src.agents.email_drafter import draft_application_email
from src.applications import store
from src.database import db
from src.models.job import Job
from src.tools.contact_finder import find_application_contact, EMAIL_PATTERN
from src.tools.gmail import send_email
from src.tools.gmail import get_gmail_service, GmailAuthorizationError


def load_job(row):
    details = store.get_details(row["job_url"])
    if details.get("job_json"):
        return Job.model_validate_json(details["job_json"])
    return Job(title=row["title"], company=row["company"] or "Unknown", url=row["job_url"], source=row["source"] or "unknown", location=row.get("location"))


def prepare_application(job, profile, attachment_path=None):
    contact = find_application_contact(job)
    draft = draft_application_email(profile, job, cv_attached=bool(attachment_path))
    if job.source == "manual_test":
        test_id = job.url.rstrip("/").split("/")[-1][:8]
        draft.subject = f"[TEST {test_id}] {draft.subject}"
        draft.body = "This is a test of application email delivery and reply tracking, not a real job application.\n\n" + draft.body.replace("[Your Name]", "Career Agent test")
    db.save_job(job)
    store.save_details(job.url, method=contact.method if contact else "unknown",
                       recipient=contact.email if contact else None,
                       application_url=contact.application_url if contact else None,
                       subject=draft.subject, body=draft.body, attachment_path=attachment_path)
    return store.get_details(job.url)


def create_test_application(job):
    if job.source != "manual_test":
        raise ValueError("Only dummy test offers can be copied for another test")
    test_id = uuid4().hex
    new_job = job.model_copy(update={
        "url": f"https://example.invalid/career-agent-test/runs/{test_id}",
        "company": f"Dummy offer - application test ({test_id[:8]})",
    })
    db.save_job(new_job)
    contact = find_application_contact(new_job)
    store.save_details(new_job.url, method=contact.method if contact else "unknown", recipient=contact.email if contact else None)
    store.record_status(new_job.url, "found", "New dummy application test created")
    return new_job


def send_application(job_url, recipient, subject, body, attachment_path=None, *, approved=False, sender=None):
    if not approved:
        raise ValueError("Review and approve the application before sending")
    if not EMAIL_PATTERN.fullmatch(recipient or ""):
        raise ValueError("Enter one valid recipient email address")
    if not subject.strip() or not body.strip() or re.search(r"[\r\n]", subject):
        raise ValueError("A single-line subject and nonempty body are required")
    if attachment_path and not Path(attachment_path).is_file():
        raise ValueError("The selected attachment does not exist")
    if not store.get_details(job_url):
        raise ValueError("Prepare the application first")
    # The claim is committed before network I/O; a rerun cannot resend it.
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        attempt = conn.execute("SELECT * FROM send_attempts WHERE job_url=?", (job_url,)).fetchone()
        if attempt:
            if attempt["state"] == "sent":
                return {"id": attempt["message_id"], "threadId": attempt["thread_id"]}
            raise RuntimeError("A send is pending or its outcome is uncertain. Check Gmail Sent before trying again; automatic resend is blocked.")
        conn.execute("INSERT INTO send_attempts(job_url,state,created_at) VALUES(?,?,?)", (job_url, "sending", store.now()))
        conn.commit()
    store.save_details(job_url, recipient=recipient, subject=subject, body=body, attachment_path=attachment_path, method="email")
    try:
        result = (sender or send_email)(recipient=recipient, subject=subject, body=body, attachment_path=attachment_path)
        if not result.get("id"):
            raise RuntimeError("Gmail did not return a message ID")
        sent_at = store.now()
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE send_attempts SET state='sent',message_id=?,thread_id=? WHERE job_url=?", (result["id"], result.get("threadId"), job_url))
            conn.execute("UPDATE application_details SET gmail_message_id=?,gmail_thread_id=?,submitted_at=? WHERE job_url=?", (result["id"], result.get("threadId"), sent_at, job_url))
            conn.execute("UPDATE applications SET status='applied',updated_at=? WHERE job_url=?", (sent_at, job_url))
            conn.execute("INSERT INTO application_events(job_url,status,detail,created_at) VALUES(?,?,?,?)", (job_url, "applied", "Email sent through Gmail", sent_at))
            conn.commit()
        return result
    except GmailAuthorizationError:
        with db.get_connection() as conn:
            conn.execute("DELETE FROM send_attempts WHERE job_url=? AND state='sending'", (job_url,))
            conn.commit()
        raise
    except Exception as error:
        with db.get_connection() as conn:
            conn.execute("UPDATE send_attempts SET state='uncertain',error=? WHERE job_url=?", (type(error).__name__, job_url))
            conn.commit()
        raise


def mark_manual_application(job_url):
    store.save_details(job_url, submitted_at=store.now())
    store.record_status(job_url, "applied", "User confirmed submission on the application website")


def reconcile_sent_application(job_url, message_id, *, service=None):
    """Link an uncertain send to verified Gmail Sent evidence without resending."""
    details = store.get_details(job_url)
    service = service or get_gmail_service(read_inbox=True, interactive=False)
    message = service.users().messages().get(userId="me", id=message_id.strip(), format="metadata", metadataHeaders=["To", "Subject"]).execute()
    headers = {header["name"].lower(): header["value"] for header in message.get("payload", {}).get("headers", [])}
    recipients = {address.casefold() for _, address in getaddresses([headers.get("to", "")])}
    if "SENT" not in message.get("labelIds", []) or (details.get("recipient") or "").casefold() not in recipients or headers.get("subject") != details.get("subject"):
        raise ValueError("This is not the sent message for the saved recipient and subject")
    sent_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc).isoformat()
    with db.get_connection() as conn:
        conn.execute("UPDATE send_attempts SET state='sent',message_id=?,thread_id=? WHERE job_url=?", (message["id"], message.get("threadId"), job_url))
        conn.commit()
    store.save_details(job_url, gmail_message_id=message["id"], gmail_thread_id=message.get("threadId"), submitted_at=sent_at)
    store.record_status(job_url, "applied", "Verified sent message in Gmail")


def reset_unsent_attempt(job_url, *, confirmed_not_sent=False):
    if not confirmed_not_sent:
        raise ValueError("Check Gmail Sent and confirm that the application was not sent")
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM send_attempts WHERE job_url=?", (job_url,)).fetchone()
        if row and row["state"] == "sent":
            raise ValueError("A confirmed sent application cannot be resent")
        if row and row["state"] == "sending" and (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds() < 600:
            raise ValueError("A send may still be in progress. Verify the sent message before recovery")
        conn.execute("DELETE FROM send_attempts WHERE job_url=? AND state IN ('uncertain','sending')", (job_url,))
        conn.commit()
