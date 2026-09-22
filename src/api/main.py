"""Run locally: python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000.

Single-user, single-worker API. The API persists review checkpoints; existing
services own search, ranking, tailoring, sending and application tracking.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
import os
from pathlib import Path
import shutil

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from src.api import storage, workflows
from src.api.models import SearchInput, DraftInput, ActionInput, InboxSettings, StatusInput, ReplyInput, CVInput
from src.applications import store, inbox, service
from src.database import db
from src.models.profile import UserProfile
from src.tools.contact_finder import EMAIL_PATTERN
from src.tools.gmail import get_gmail_service

db.DB_PATH = Path(os.environ.get("CAREERAI_DB_PATH", str(ROOT / "data" / "career_agent.db")))
ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"]


async def poll_inbox():
    while True:
        await asyncio.sleep(60)
        if store.get_setting("inbox_enabled", False):
            with suppress(Exception):  # Existing checker persists its error for Settings.
                await asyncio.to_thread(inbox.check_inbox)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    storage.initialize()
    storage.recover_interrupted()
    store.set_setting("web_gmail_connecting", False)
    task = None if os.environ.get("CAREERAI_DISABLE_POLLING") == "1" else asyncio.create_task(poll_inbox())
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="CareerAI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "PATCH", "PUT"], allow_headers=["Content-Type"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in ORIGINS:
        return JSONResponse({"detail": "This local API only accepts actions from CareerAI."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid_input(request, error):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/workflows")
def list_workflows():
    return [{key: value for key, value in flow.items() if key not in {"cv_source", "tailored_source", "profile", "changes", "jobs", "draft"}}
            for flow in storage.all_workflows()]


@app.get("/api/workflows/{identifier}")
def get_workflow(identifier: str):
    return storage.get(identifier)


@app.post("/api/workflows", status_code=201)
def create_workflow(body: SearchInput, tasks: BackgroundTasks):
    flow = workflows.new_workflow(body.model_dump(mode="json"))
    flow = storage.update(flow["id"], {"status": "running", "last_action": "search"}, claim=True)
    tasks.add_task(workflows.run, flow["id"], "search")
    return flow


@app.patch("/api/workflows/{identifier}/draft")
def save_draft(identifier: str, body: DraftInput):
    flow = storage.get(identifier)
    if flow["status"] == "running" or flow["step"] == "submitted":
        raise HTTPException(409, "This workflow cannot be edited now")
    changes = body.model_dump(exclude_none=True, exclude={"revision"})
    allowed = {"job_review": {"selected_index"}, "cv_tailoring": {"approved_indices", "edited_changes", "cv_view"},
               "email_review": {"recipient", "subject", "body"}}
    if not changes.keys() <= allowed.get(flow["step"], set()):
        raise HTTPException(409, "These fields cannot be edited at this step")
    if "selected_index" in changes and not 0 <= changes["selected_index"] < len(flow["jobs"]):
        raise ValueError("Choose an available job")
    count = len(flow.get("changes", []))
    if "approved_indices" in changes:
        if len(set(changes["approved_indices"])) != len(changes["approved_indices"]) or any(i < 0 or i >= count for i in changes["approved_indices"]):
            raise ValueError("Invalid CV change selection")
    if "edited_changes" in changes and any(not key.isdigit() or int(key) >= count or len(value) > 50000 for key, value in changes["edited_changes"].items()):
        raise ValueError("Invalid CV edit")
    return storage.update(identifier, {"draft": {**flow["draft"], **changes}}, revision=body.revision)


@app.post("/api/workflows/{identifier}/actions/{action}", status_code=202)
def action_workflow(identifier: str, action: str, body: ActionInput, tasks: BackgroundTasks):
    flow = storage.get(identifier)
    allowed = {"search": {"search", "job_review"}, "select": {"job_review"}, "tailor": {"cv_tailoring"},
               "review": {"email_review"}, "back": {"send"}, "send": {"send"}, "manual": {"send"}}
    if action not in allowed or flow["step"] not in allowed[action]:
        raise HTTPException(409, "This action is not available at the current step")
    draft = flow["draft"]
    if action == "select" and draft.get("selected_index") is None:
        raise ValueError("Select a job before continuing")
    email_method = (flow.get("contact") or {}).get("method") == "email"
    if action in {"review", "send"} and email_method:
        if not EMAIL_PATTERN.fullmatch(draft.get("recipient", "")) or not draft.get("subject", "").strip() or not draft.get("body", "").strip() or any(c in draft.get("subject", "") for c in "\r\n"):
            raise ValueError("Enter a valid recipient, single-line subject and email body")
    if action in {"send", "manual"}:
        if not body.approved:
            raise ValueError("Explicit confirmation is required to submit an application")
        if (action == "send") != email_method:
            raise ValueError("Use the detected application method")
        pdf = workflows.ARTIFACTS / identifier / "tailored.pdf"
        if not pdf.is_file():
            raise ValueError("The tailored CV PDF is missing. Restore the workflow artifact before submitting.")
    if action in {"review", "back"}:
        return storage.update(identifier, {"step": "send" if action == "review" else "email_review", "error": None}, revision=body.revision, claim=True)
    flow = storage.update(identifier, {"status": "running", "error": None, "last_action": action}, revision=body.revision, claim=True)
    tasks.add_task(workflows.run, identifier, action)
    return flow


@app.get("/api/workflows/{identifier}/cv.pdf")
def download_cv(identifier: str):
    storage.get(identifier)
    path = workflows.ARTIFACTS / identifier / "tailored.pdf"
    if not path.is_file():
        raise HTTPException(404, "The tailored PDF is not ready yet")
    return FileResponse(path, media_type="application/pdf", filename="tailored-cv.pdf", content_disposition_type="inline")


def application(identifier):
    row = next((row for row in db.get_applications() if row["id"] == identifier), None)
    if not row:
        raise HTTPException(404, "Application not found")
    return row


@app.get("/api/applications")
def applications():
    tracked = sorted(store.tracked_applications(), key=lambda row: row["submitted_at"], reverse=True)
    submitted = {row["job_url"] for row in tracked}
    return {"submitted": tracked, "saved": [row for row in db.get_applications() if row["job_url"] not in submitted]}


@app.get("/api/applications/{identifier}")
def application_detail(identifier: int):
    row = application(identifier)
    details = store.get_details(row["job_url"])
    return {**row, "details": {k: v for k, v in details.items() if k not in {"attachment_path", "job_json"}},
            "responses": inbox.get_responses(row["job_url"]), "history": store.events(row["job_url"])}


@app.patch("/api/applications/{identifier}/status")
def application_status(identifier: int, body: StatusInput):
    row = application(identifier)
    if not store.get_details(row["job_url"]).get("submitted_at"):
        raise ValueError("Submit this application before tracking its status")
    store.record_status(row["job_url"], body.status, "Status updated in CareerAI")
    return application_detail(identifier)


@app.post("/api/applications/{identifier}/continue", status_code=201)
def continue_saved(identifier: int, tasks: BackgroundTasks):
    row = application(identifier)
    details = store.get_details(row["job_url"])
    if details.get("submitted_at"):
        raise ValueError("This application has already been submitted")
    for flow in storage.all_workflows():
        if (flow.get("selected_job") or {}).get("url") == row["job_url"] and flow["step"] != "submitted":
            return flow
    job = service.load_job(row)
    flow = workflows.new_workflow(SearchInput(query=job.title).model_dump(mode="json"))
    data = {"jobs": [job.model_dump(mode="json")], "draft": {"selected_index": 0}, "step": "job_review"}
    attachment = Path(details["attachment_path"]) if details.get("attachment_path") else None
    if attachment and attachment.is_file() and attachment.suffix.lower() == ".pdf":
        folder = workflows.ARTIFACTS / flow["id"]
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(attachment, folder / "tailored.pdf")
        data.update(selected_job=job.model_dump(mode="json"), pdf_ready=True, step="email_review",
                    contact={"method": details.get("method"), "email": details.get("recipient"), "application_url": details.get("application_url")},
                    draft={"recipient": details.get("recipient") or "", "subject": details.get("subject") or "", "body": details.get("body") or ""})
        return storage.update(flow["id"], data)
    flow = storage.update(flow["id"], {**data, "status": "running", "last_action": "select"})
    tasks.add_task(workflows.run, flow["id"], "select")
    return flow


@app.get("/api/settings")
def settings():
    profile = db.load_profile()
    return {"profile": profile.model_dump() if profile else UserProfile().model_dump(),
            "cv_source": workflows.BASE_CV.read_text(encoding="utf-8") if workflows.BASE_CV.exists() else "",
            "gmail_saved": (ROOT / "token.json").exists(),
            "gmail_connecting": store.get_setting("web_gmail_connecting", False),
            "gmail_error": store.get_setting("web_gmail_error"),
            "inbox": {"enabled": store.get_setting("inbox_enabled", False),
                      "interval_minutes": store.get_setting("inbox_interval_minutes", 15),
                      "last_checked": store.get_setting("inbox_last_checked"),
                      "error": store.get_setting("inbox_error")}}


@app.put("/api/settings/profile")
def save_profile(body: UserProfile):
    for key in ("target_roles", "skills", "experience", "preferred_locations"):
        setattr(body, key, [value.strip() for value in getattr(body, key) if value.strip()])
    db.save_profile(body)
    return {"saved": True}


@app.put("/api/settings/cv")
def save_cv(body: CVInput):
    if "\\begin{document}" not in body.source or "\\end{document}" not in body.source:
        raise ValueError("Upload a complete LaTeX CV, including begin/end document")
    workflows.BASE_CV.parent.mkdir(parents=True, exist_ok=True)
    workflows.BASE_CV.write_text(body.source, encoding="utf-8")
    return {"saved": True}


@app.put("/api/settings/inbox")
def save_inbox(body: InboxSettings):
    store.set_setting("inbox_enabled", body.enabled)
    store.set_setting("inbox_interval_minutes", body.interval_minutes)
    return {"saved": True}


def connect_gmail():
    try:
        get_gmail_service(read_inbox=True, force_reconnect=True)
        store.set_setting("web_gmail_error", None)
    except Exception as error:
        store.set_setting("web_gmail_error", str(error))
    finally:
        store.set_setting("web_gmail_connecting", False)


@app.post("/api/settings/gmail/connect", status_code=202)
def gmail_connect(tasks: BackgroundTasks):
    if store.get_setting("web_gmail_connecting", False):
        raise HTTPException(409, "Google consent is already open")
    store.set_setting("web_gmail_connecting", True)
    store.set_setting("web_gmail_error", None)
    tasks.add_task(connect_gmail)
    return {"connecting": True}


@app.post("/api/inbox/check")
def check_inbox():
    try:
        return inbox.check_inbox(force=True)
    except Exception as error:
        raise HTTPException(502, f"Inbox check failed: {error}") from error


@app.get("/api/inbox/review")
def review_inbox():
    return inbox.review_queue()


@app.post("/api/inbox/{message_id}/classify")
def classify_response(message_id: str, body: ReplyInput):
    row = application(body.application_id)
    if not store.get_details(row["job_url"]).get("submitted_at"):
        raise ValueError("Choose a submitted application for this response")
    inbox.apply_review(message_id, job_url=row["job_url"], status=body.status)
    return {"saved": True}
