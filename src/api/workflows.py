"""Step adapters: delegate all career logic to the existing services."""
import logging
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from src.api import storage
from src.applications import service, store
from src.database import db
from src.graph import workflow as core
from src.models.job import Job
from src.models.profile import UserProfile
from src.agents.cv_tailor import CVChange

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "data" / "web_workflows"
BASE_CV = ROOT / "data" / "base_cv.tex"
logger = logging.getLogger(__name__)


def new_workflow(search):
    profile = db.load_profile()
    if not profile:
        raise ValueError("Add your profile in Settings before searching.")
    if not BASE_CV.is_file():
        raise ValueError("Add your original LaTeX CV in Settings before searching.")
    return storage.create({"step": "search", "status": "idle", "error": None,
                           "search": search, "profile": profile.model_dump(),
                           "cv_source": BASE_CV.read_text(encoding="utf-8"), "jobs": [], "draft": {}})


def run(identifier, action):
    try:
        flow = storage.get(identifier)
        draft = flow["draft"]
        folder = ARTIFACTS / identifier
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / "original.tex"
        original.write_text(flow["cv_source"], encoding="utf-8")
        profile = UserProfile.model_validate(flow["profile"])
        state = {"profile": profile, "cv_path": str(original),
                 "tailored_cv_path": str(folder / "tailored.tex")}
        if flow.get("selected_job"):
            state["selected_job"] = Job.model_validate(flow["selected_job"])

        if action == "search":
            state.update(flow["search"], prefilter_top_k=10)
            state.update(core.search_jobs_node(state))
            if state["jobs"]:
                state.update(core.prefilter_jobs_node(state))
                state.update(core.rank_jobs_node(state))
            result = {"jobs": jsonable_encoder(state.get("ranked_jobs", [])),
                      "step": "job_review", "no_jobs_message": state.get("no_jobs_message")}
        elif action == "select":
            job = Job.model_validate(flow["jobs"][draft["selected_index"]])
            if store.get_details(job.url).get("submitted_at"):
                raise ValueError("You have already applied to this role. View it in Application Tracker or select another opportunity.")
            state["selected_job"] = job
            db.save_job(job, status="shortlisted")
            state.update(core.find_contact_node(state))
            state.update(core.propose_cv_changes_node(state))
            changes = jsonable_encoder(state["cv_changes"])
            result = {"selected_job": job.model_dump(mode="json"),
                      "contact": jsonable_encoder(state.get("application_contact")),
                      "changes": changes, "step": "cv_tailoring",
                      "draft": {"approved_indices": list(range(len(changes))), "edited_changes": {}, "cv_view": "side"}}
        elif action == "tailor":
            changes = [CVChange.model_validate(item) for item in flow["changes"]]
            approved = []
            preview = flow["cv_source"]
            for index in draft.get("approved_indices", []):
                change = changes[index].model_copy()
                change.proposed_text = draft.get("edited_changes", {}).get(str(index), change.proposed_text)
                if not change.original_text or change.original_text not in preview:
                    raise ValueError(f"Change {index + 1} is missing or overlaps another selected change. Revert it before continuing.")
                preview = preview.replace(change.original_text, change.proposed_text, 1)
                approved.append(change)
            state["approved_changes"] = approved
            state.update(core.apply_cv_changes_node(state))
            state.update(core.compile_cv_pdf_node(state))
            contact = flow.get("contact") or {}
            result = {"pdf_ready": True, "tailored_source": Path(state["tailored_cv_path"]).read_text(encoding="utf-8"),
                      "step": "email_review"}
            if contact.get("method") == "email":
                prepared = service.prepare_application(state["selected_job"], profile, state["tailored_cv_pdf_path"])
                result["draft"] = {**draft, "recipient": prepared.get("recipient") or "",
                                   "subject": prepared["subject"], "body": prepared["body"]}
            else:
                store.save_details(state["selected_job"].url, attachment_path=state["tailored_cv_pdf_path"])
        elif action == "send":
            # Approval is checked by the API before this action is ever queued.
            service.send_application(state["selected_job"].url, draft["recipient"],
                                     draft["subject"], draft["body"], str(folder / "tailored.pdf"), approved=True)
            result = {"step": "submitted"}
        elif action == "manual":
            service.mark_manual_application(state["selected_job"].url)
            result = {"step": "submitted"}
        else:
            raise ValueError("Unsupported workflow action")
        storage.update(identifier, {**result, "status": "idle", "error": None})
    except Exception as error:
        logger.exception("Workflow %s failed during %s", identifier, action)
        storage.update(identifier, {"status": "error", "error": str(error)[:4000]})
