from datetime import datetime
from hashlib import sha256
from pathlib import Path

import streamlit as st

from src.applications import store
from src.applications.inbox import check_inbox, review_queue, apply_review, get_responses, RESPONSE_LABELS
from src.applications.service import load_job, prepare_application, send_application, mark_manual_application, reconcile_sent_application, reset_unsent_attempt
from src.database import db
from src.tools.contact_finder import find_application_contact
from src.tools.gmail import get_gmail_service
from src.applications.service import create_test_application

ATTACHMENT_DIR = Path(__file__).resolve().parents[2] / "data" / "application_attachments"

METHOD_LABELS = {"email": "Email", "website": "External application page", "linkedin_easy_apply": "LinkedIn Easy Apply", "linkedin": "LinkedIn page — application method not confirmed", "unknown": "Application method not found"}


@st.fragment(run_every="60s")
def render_inbox_monitor():
    st.subheader("Recruiter responses")
    st.caption("Connect Gmail once to allow read-only inbox checks and application sending. No replies are sent automatically.")
    if st.button("Connect Gmail", key="connect_application_gmail"):
        try:
            with st.spinner("Complete Gmail authorization in your browser..."):
                get_gmail_service(read_inbox=True, interactive=True, force_reconnect=True)
            st.success("Gmail connected.")
        except Exception as error:
            st.error(str(error))
    enabled = st.toggle("Check inbox automatically", value=store.get_setting("inbox_enabled", False))
    interval = st.selectbox("Check interval (minutes)", [5, 15, 30, 60], index=[5, 15, 30, 60].index(store.get_setting("inbox_interval_minutes", 15)))
    store.set_setting("inbox_enabled", enabled)
    store.set_setting("inbox_interval_minutes", interval)
    st.caption("Automatic checks run while this dashboard is open. Use the inbox worker to keep checking when the dashboard is closed.")
    manual = st.button("Check inbox now")
    if enabled or manual:
        try:
            counts = check_inbox(force=manual)
            if not counts.get("skipped"):
                st.success(f"Checked {counts['checked']} messages; {counts['updated']} linked responses; {counts['review']} need review.")
                if counts["updated"]:
                    st.rerun()
        except Exception as error:
            st.error(str(error))
    checked = store.get_setting("inbox_last_checked")
    if checked:
        st.caption("Last successful check: " + datetime.fromtimestamp(checked).strftime("%Y-%m-%d %H:%M"))
    rows = db.get_applications()
    for message in review_queue()[:30]:
        with st.expander(f"Review: {message['subject']} — {message['sender']}"):
            st.text(message.get("response_text") or message["snippet"])
            options = {row["job_url"]: f"{row['title']} — {row['company']}" for row in rows}
            target = st.selectbox("Match to application", [None, *options], format_func=lambda value: options[value] if value else "Select an application", key="match_" + message["message_id"])
            status = st.selectbox("Response classification", ["interview", "rejected"], format_func=lambda value: RESPONSE_LABELS[value], key="response_" + message["message_id"])
            if st.button("Apply response update", key="apply_" + message["message_id"], disabled=not target):
                apply_review(message["message_id"], target, status)
                st.rerun(scope="fragment")
            if st.button("Ignore message", key="ignore_" + message["message_id"]):
                apply_review(message["message_id"])
                st.rerun(scope="fragment")


def render_application_center():
    st.subheader("Prepare and track an application")
    rows = db.get_applications()
    if not rows:
        st.info("Search for jobs first. Ranked matches will appear here.")
        render_inbox_monitor()
        return
    options = {row["job_url"]: row for row in rows}
    pending = st.session_state.pop("pending_application_url", None)
    if pending in options:
        st.session_state["application_center_role"] = pending
    url = st.selectbox("Saved role", list(options), format_func=lambda value: f"{options[value]['title']} — {options[value]['company']}", key="application_center_role")
    row = options[url]
    job = load_job(row)
    details = store.get_details(url)
    if job.source == "manual_test":
        if st.button("Create a new test application"):
            new_job = create_test_application(job)
            st.session_state["pending_application_url"] = new_job.url
            st.rerun()
        st.caption("A new test has its own draft, attachment, and reply history. Upload your CV before generating the new email.")
    contact = find_application_contact(job)
    method = details.get("method") or (contact.method if contact else "unknown")
    application_url = details.get("application_url") or (contact.application_url if contact else None)
    st.write("**Application method:** " + METHOD_LABELS.get(method, method))
    st.write("**Current status:** " + row["status"].replace("_", " ").title())
    st.subheader("Recruiter replies for this application")
    responses = get_responses(url)
    if not responses:
        st.caption("No replies recorded yet. Check your inbox after receiving a response.")
    for response in responses:
        with st.expander(f"{response['classification_label']} — {response['subject']}", expanded=True):
            st.caption(f"From: {response['sender']} | Received: {response['received_at']}")
            st.text(response["response"])
            if response["classification"] is None:
                st.info("This reply does not clearly reject you or invite you to an interview. Classify it in the review queue below.")
    if method in {"linkedin_easy_apply", "linkedin", "website"} and application_url:
        st.link_button("Open LinkedIn Easy Apply" if method == "linkedin_easy_apply" else "Open application page", application_url)
        st.caption("Complete the form on the linked page, then confirm submission here. Opening the link does not mark the job as applied.")
        confirmed = st.checkbox("I completed and submitted the application on that page", key="manual_confirm_" + url)
        if st.button("Mark application submitted", disabled=not confirmed):
            store.save_details(url, method=method, application_url=application_url)
            mark_manual_application(url)
            st.rerun()
    upload = st.file_uploader("Attach your tailored CV (PDF, optional)", type=["pdf"], key="cv_upload_" + url)
    attachment = details.get("attachment_path")
    if upload:
        content = upload.getvalue()
        if len(content) > 10 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            st.error("Choose a valid PDF up to 10 MB.")
            return
        path = ATTACHMENT_DIR / (sha256(content).hexdigest() + ".pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        attachment = str(path)
    if attachment:
        st.caption("Attachment: " + Path(attachment).name)
        if Path(attachment).is_file():
            st.download_button("Review attached CV", data=Path(attachment).read_bytes(), file_name="application_cv.pdf", mime="application/pdf", key="review_cv_" + url)
    if st.button("Generate tailored application email", type="primary"):
        profile = db.load_profile()
        if profile is None:
            st.error("Save your candidate profile first.")
        else:
            try:
                with st.spinner("Drafting an email using your profile and this job..."):
                    prepare_application(job, profile, attachment)
                st.rerun()
            except Exception as error:
                st.error(str(error))
    if details.get("subject"):
        with st.form("application_draft_" + url):
            recipient = st.text_input("Recipient email", value=details.get("recipient") or "")
            subject = st.text_input("Application subject", value=details["subject"])
            body = st.text_area("Application email", value=details.get("body") or "", height=260)
            authorize = st.checkbox("I reviewed the recipient, email, and attachment and authorize sending this application now.")
            save = st.form_submit_button("Save draft")
            send = st.form_submit_button("Send via Gmail", type="primary")
        if save:
            store.save_details(url, recipient=recipient, subject=subject, body=body, attachment_path=attachment)
            st.success("Draft saved.")
        if send:
            try:
                with st.spinner("Sending application..."):
                    result = send_application(url, recipient, subject, body, attachment, approved=authorize)
                st.success("Application sent. Gmail message: " + result["id"])
                st.rerun()
            except Exception as error:
                st.error(str(error))
    with st.expander("Update status and view history"):
        statuses = sorted(db.VALID_STATUSES)
        status = st.selectbox("Application status", statuses, index=statuses.index(row["status"]))
        if st.button("Save status"):
            store.record_status(url, status, "Status updated by user")
            if status == "applied" and not details.get("submitted_at"):
                store.save_details(url, submitted_at=store.now())
            st.rerun()
        for event in store.events(url):
            st.write(f"{event['created_at']} · {event['status']} · {event['detail']}")
    with db.get_connection() as conn:
        attempt = conn.execute("SELECT state FROM send_attempts WHERE job_url=?", (url,)).fetchone()
    if attempt and attempt["state"] in {"uncertain", "sending"}:
        with st.expander("Resolve an uncertain send", expanded=True):
            st.warning("Check Gmail Sent before retrying. A network error can occur after Gmail accepted a message.")
            message_id = st.text_input("Gmail API message ID of the sent application")
            if st.button("Verify and link sent message", disabled=not message_id):
                try:
                    reconcile_sent_application(url, message_id)
                    st.rerun()
                except Exception as error:
                    st.error(str(error))
            absent = st.checkbox("I checked Gmail Sent and confirmed this application was not sent")
            if st.button("Allow a new send attempt", disabled=not absent):
                try:
                    reset_unsent_attempt(url, confirmed_not_sent=absent)
                    st.rerun()
                except Exception as error:
                    st.error(str(error))
    render_inbox_monitor()
