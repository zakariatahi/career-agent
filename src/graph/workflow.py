# src/graph/workflow.py

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pathlib import Path
from src.tools.gmail import send_email
from src.applications import store as application_store
from src.applications.service import send_application
from src.tools.latex import compile_latex_to_pdf
from src.tools.contact_finder import (
    find_application_contact,
)



from src.agents.cv_tailor import (
    apply_cv_changes,
    propose_cv_changes,
)
from src.agents.email_drafter import (
    ApplicationEmail,
    draft_application_email,
)
from src.agents.job_prefilter import prefilter_jobs
from src.agents.job_ranker import rank_jobs
from src.database.db import (
    load_profile,
    save_job,
)
from src.graph.state import CareerState
from src.tools.job_collector import search_all_jobs


# =========================================================
# 1. LOAD PROFILE
# =========================================================

def load_profile_node(state: CareerState):

    profile = load_profile()

    if profile is None:
        raise ValueError(
            "No user profile found in database."
        )

    return {
        "profile": profile
    }


# =========================================================
# 2. SEARCH JOBS
# =========================================================

def search_jobs_node(state: CareerState):

    query = state["query"]

    jobs = search_all_jobs(
        **({"verbose": True} if state.get("verbose_search") else {}),
        **({"post_date": state["post_date"]} if state.get("post_date") else {}),
        query=query,
        location=state.get(
            "location",
            "Morocco",
        ),
        country=state.get(
            "country",
            "MA",
        ),
        limit_per_source=state.get(
            "limit_per_source",
            10,
        ),
    )

    print(
        f"\nCollected jobs: {len(jobs)}"
    )

    return {
        "jobs": jobs,
        "ranked_jobs": [],
        "no_jobs_message": (
            "No matching jobs were returned by the search sources. Try different keywords, a broader location, or another publication date."
            if not jobs else None
        ),
    }


# =========================================================
# 3. PREFILTER
# =========================================================

def prefilter_jobs_node(state: CareerState):

    candidate_jobs = prefilter_jobs(
        profile=state["profile"],
        jobs=state["jobs"],
        top_k=state.get(
            "prefilter_top_k",
            10,
        ),
    )

    print(
        f"After pre-filter: "
        f"{len(candidate_jobs)}"
    )

    return {
        "jobs": candidate_jobs,
        "no_jobs_message": (
            "No jobs remained after pre-filtering. Try broader search criteria or review your profile preferences."
            if not candidate_jobs else None
        ),
    }


# =========================================================
# 4. LLM RANKING
# =========================================================

def rank_jobs_node(state: CareerState):

    ranked_jobs = rank_jobs(
        profile=state["profile"],
        jobs=state["jobs"],
        minimum_score=state.get(
            "minimum_score",
            50,
        ),
    )

    print(
        f"After ranking: "
        f"{len(ranked_jobs)}"
    )

    for job in ranked_jobs:
        save_job(job, status="found")

    return {
        "ranked_jobs": ranked_jobs,
        "no_jobs_message": (
            "No jobs met the minimum match score. Try a lower minimum score or different search criteria."
            if not ranked_jobs else None
        ),
    }


# =========================================================
# 5. HUMAN SELECTS JOB
# =========================================================

def select_job_node(state: CareerState):

    ranked_jobs = state["ranked_jobs"]

    top_jobs = ranked_jobs[:5]

    payload = {
        "type": "job_selection",

        "jobs": [
            {
                "index": index,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "source": job.source,
                "score": job.match_score,
                "url": job.url,
            }
            for index, job in enumerate(top_jobs)
        ],
    }

    response = interrupt(payload)

    selected_index = response.get(
        "selected_index"
    )

    if selected_index is None:
        raise ValueError(
            "No job selected."
        )

    if not (
        0 <= selected_index < len(top_jobs)
    ):
        raise ValueError(
            "Invalid job index."
        )

    selected_job = top_jobs[
        selected_index
    ]

    save_job(
        selected_job,
        status="shortlisted",
    )

    return {
        "selected_job": selected_job
    }


# =========================================================
# 6. PROPOSE CV CHANGES
# =========================================================

def propose_cv_changes_node(
    state: CareerState,
):

    plan = propose_cv_changes(
        cv_path=state["cv_path"],
        job=state["selected_job"],
    )

    return {
        "cv_changes": plan.changes
    }


# =========================================================
# 7. HUMAN APPROVES CV CHANGES
# =========================================================

def approve_cv_changes_node(
    state: CareerState,
):

    changes = state["cv_changes"]

    payload = {
        "type": "cv_approval",

        "job": {
            "title":
                state["selected_job"].title,

            "company":
                state["selected_job"].company,
        },

        "changes": [
            {
                "index": index,

                "original":
                    change.original_text,

                "proposed":
                    change.proposed_text,

                "reason":
                    change.reason,
            }
            for index, change
            in enumerate(changes)
        ],
    }

    response = interrupt(payload)

    approved_indices = response.get(
        "approved_indices",
        [],
    )

    edited_changes = response.get(
        "edited_changes",
        {},
    )

    approved_changes = []

    for index in approved_indices:

        if not (
            0 <= index < len(changes)
        ):
            continue

        change = changes[index]

        # Allow the human to modify
        # the AI proposal.
        if str(index) in edited_changes:
            change.proposed_text = (
                edited_changes[str(index)]
            )

        approved_changes.append(
            change
        )

    return {
        "approved_changes":
            approved_changes
    }


# =========================================================
# 8. APPLY APPROVED CV CHANGES
# =========================================================

def apply_cv_changes_node(
    state: CareerState,
):

    approved_changes = state.get(
        "approved_changes",
        [],
    )

    if not approved_changes:

        print(
            "No CV changes approved."
        )

        Path(state["tailored_cv_path"]).write_text(
            Path(state["cv_path"]).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        return {"cv_updated": False}

    apply_cv_changes(
        cv_path=state["cv_path"],

        output_path=state[
            "tailored_cv_path"
        ],

        approved_changes=
            approved_changes,
    )

    print(
        "\nTailored CV created:"
    )

    print(
        state["tailored_cv_path"]
    )

    return {
        "cv_updated": True
    }

def compile_cv_pdf_node(state: CareerState):
    pdf_path = compile_latex_to_pdf(
        state["tailored_cv_path"]
    )

    print(f"CV PDF created: {pdf_path}")

    return {
        "tailored_cv_pdf_path": pdf_path
    }
# =========================================================
# 9. DRAFT APPLICATION EMAIL
# =========================================================

def find_contact_node(
    state: CareerState,
):

    contact = find_application_contact(
        state["selected_job"]
    )

    if contact:
        application_store.save_details(state["selected_job"].url, method=contact.method,
                                       recipient=contact.email, application_url=contact.application_url)

    if contact is None:
        print(
            "No application contact found."
        )

        return {
            "application_contact": None,
            "recipient_email": None,
        }

    if contact.method == "email":
        print(
            "Found email:",
            contact.email,
        )

        return {
            "application_contact": contact,
            "recipient_email": contact.email,
        }

    print(
        "Found application URL:",
        contact.application_url,
    )

    return {
        "application_contact": contact,
        "recipient_email": None,
    }


def route_after_cv(state: CareerState):
    contact = state.get("application_contact")

    if (
        contact is not None
        and contact.method == "email"
        and contact.email
    ):
        return "draft_email"

    return "finalize_application"

def draft_email_node(
    state: CareerState,
):

    email = draft_application_email(
        profile=state["profile"],
        job=state["selected_job"],
    )

    return {
        "email_draft": email
    }


# =========================================================
# 10. HUMAN APPROVES EMAIL
# =========================================================

def approve_email_node(
    state: CareerState,
):

    email = state["email_draft"]

    payload = {
        "type": "email_approval",

        "job": {
            "title":
                state["selected_job"].title,

            "company":
                state["selected_job"].company,
        },

        "email": {
            "subject": email.subject,
            "body": email.body,
        },
        "recipient_email": state.get("recipient_email"),
    }

    response = interrupt(payload)

    approved = response.get(
        "approved",
        False,
    )

    # Human can edit subject/body
    # before approval.
    subject = response.get(
        "subject",
        email.subject,
    )

    body = response.get(
        "body",
        email.body,
    )

    final_email = ApplicationEmail(
        subject=subject,
        body=body,
    )

    return {
        "email_draft": final_email,
        "email_approved": approved,
    }



# =========================================================
# 11. SAVE APPLICATION STATE
# =========================================================

def finalize_application_node(
    state: CareerState,
):
    job = state["selected_job"]

    if state.get("email_sent", False):

        save_job(
            job=job,
            status="applied",
            notes=(
                "Application sent by Career Agent. "
                f"Gmail message ID: "
                f"{state.get('gmail_message_id')}"
            ),
        )

        print(
            "\nApplication status: APPLIED"
        )

    else:

        contact = state.get("application_contact")
        if contact and contact.application_url:
            notes = (
                "Application prepared for manual submission at: "
                f"{contact.application_url}"
            )
        else:
            notes = "Application prepared; no application contact was found."

        save_job(
            job=job,
            status="shortlisted",
            notes=notes,
        )

        print(
            "\nApplication status: SHORTLISTED"
        )

    return {}

def send_email_node(state: CareerState):

    if not state.get("email_approved", False):
        return {
            "email_sent": False
        }

    recipient = state.get("recipient_email")
    if not recipient:
        raise ValueError(
            "The application email was approved, but no recipient address "
            "is available."
        )

    email = state["email_draft"]

    application_store.save_details(state["selected_job"].url, subject=email.subject, body=email.body)
    result = send_application(
        job_url=state["selected_job"].url,
        approved=True,
        sender=send_email,
        recipient=recipient,
        subject=email.subject,
        body=email.body,
        attachment_path=state[
            "tailored_cv_pdf_path"
        ],
    )

    return {
        "email_sent": True,
        "gmail_message_id": result.get("id"),
        "gmail_thread_id": result.get("threadId"),
    }

# =========================================================
# GRAPH
# =========================================================

def build_career_graph():

    builder = StateGraph(
        CareerState
    )

    # -------------------------
    # Nodes
    # -------------------------

    builder.add_node(
        "load_profile",
        load_profile_node,
    )

    builder.add_node(
        "search_jobs",
        search_jobs_node,
    )

    builder.add_node(
        "prefilter_jobs",
        prefilter_jobs_node,
    )

    builder.add_node(
        "rank_jobs",
        rank_jobs_node,
    )

    builder.add_node(
        "select_job",
        select_job_node,
    )

    builder.add_node(
        "propose_cv_changes",
        propose_cv_changes_node,
    )

    builder.add_node(
        "approve_cv_changes",
        approve_cv_changes_node,
    )

    builder.add_node(
        "apply_cv_changes",
        apply_cv_changes_node,
    )

    builder.add_node(
    "compile_cv_pdf",
    compile_cv_pdf_node,
    )
    builder.add_node(
    "find_contact",
    find_contact_node,
)
    builder.add_node(
        "draft_email",
        draft_email_node,
    )

    builder.add_node(
        "approve_email",
        approve_email_node,
    )

    builder.add_node(
        "finalize_application",
        finalize_application_node,
    )

    # -------------------------
    # Edges
    # -------------------------

    builder.add_edge(
        START,
        "load_profile",
    )

    builder.add_edge(
        "load_profile",
        "search_jobs",
    )

    builder.add_conditional_edges(
        "search_jobs",
        lambda state: "prefilter_jobs" if state.get("jobs") else END,
        {"prefilter_jobs": "prefilter_jobs", END: END},
    )

    builder.add_conditional_edges(
        "prefilter_jobs",
        lambda state: "rank_jobs" if state.get("jobs") else END,
        {"rank_jobs": "rank_jobs", END: END},
    )

    builder.add_conditional_edges(
        "rank_jobs",
        lambda state: "select_job" if state.get("ranked_jobs") else END,
        {"select_job": "select_job", END: END},
    )

    builder.add_edge(
        "select_job",
        "find_contact",
    )

    builder.add_edge(
        "find_contact",
        "propose_cv_changes",
    )

    builder.add_edge(
        "propose_cv_changes",
        "approve_cv_changes",
    )

    builder.add_edge(
        "approve_cv_changes",
        "apply_cv_changes",
    )

    builder.add_edge(
        "apply_cv_changes",
        "compile_cv_pdf",
    )

    builder.add_conditional_edges(
        "compile_cv_pdf",
        route_after_cv,
        {
            "draft_email": "draft_email",
            "finalize_application": "finalize_application",
        },
    )

    builder.add_edge(
        "draft_email",
        "approve_email",
    )

    builder.add_node(
        "send_email",
        send_email_node,
    )

    builder.add_edge(
        "approve_email",
        "send_email",
    )

    builder.add_edge(
        "send_email",
        "finalize_application",
    )

    builder.add_edge(
        "finalize_application",
        END,
    )

    # Needed for interrupt/resume
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("src.models.profile", "UserProfile"),
            ("src.models.job", "Job"),
            ("src.agents.cv_tailor", "CVChange"),
            ("src.agents.email_drafter", "ApplicationEmail"),
            ("src.tools.contact_finder", "ApplicationContact"),
        ]
    )
    checkpointer = MemorySaver(
        serde=serializer
    )

    return builder.compile(
        checkpointer=checkpointer
    )
