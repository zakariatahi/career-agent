from pathlib import Path
import sys
from uuid import uuid4

for output_stream in (sys.stdout, sys.stderr):
    if hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(encoding="utf-8", errors="replace")

import streamlit as st
from langgraph.types import Command

from src.database.db import (
    get_applications,
    init_db,
    load_profile,
    save_profile,
)
from src.graph.workflow import build_career_graph
from src.models.profile import UserProfile
from src.applications.ui import render_application_center


BASE_CV_PATH = "data/base_cv.tex"
TAILORED_CV_PATH = "data/tailored_cv.tex"

st.set_page_config(
    page_title="Career Agent | Job search workspace",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ca-ink: #17212b;
        --ca-muted: #64748b;
        --ca-primary: #0f766e;
        --ca-border: #dce5e4;
    }
    .stApp { background: #f7faf9; }
    [data-testid="stHeader"] { background: rgba(247, 250, 249, 0.88); }
    [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--ca-border); }
    .block-container { max-width: 1240px; padding-top: 2rem; padding-bottom: 4rem; }
    h1, h2, h3 { color: var(--ca-ink); letter-spacing: -0.025em; }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--ca-border);
        border-radius: 14px;
        padding: 0.85rem 1rem;
        box-shadow: 0 4px 18px rgba(15, 118, 110, 0.04);
    }
    div[data-testid="stForm"], div[data-testid="stVerticalBlockBorderWrapper"] {
        background: #ffffff;
        border-color: var(--ca-border);
        border-radius: 16px;
    }
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
        background: var(--ca-primary);
        border-color: var(--ca-primary);
    }
    .ca-eyebrow {
        color: var(--ca-primary);
        font-size: 0.76rem;
        font-weight: 750;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin-bottom: 0.4rem;
    }
    .ca-hero {
        background: linear-gradient(135deg, #0f766e 0%, #155e75 100%);
        border-radius: 22px;
        color: white;
        padding: 1.7rem 2rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 16px 38px rgba(15, 118, 110, 0.16);
    }
    .ca-hero h1 { color: white; margin: 0; font-size: 2rem; }
    .ca-hero p { color: #dff7f3; margin: 0.55rem 0 0; max-width: 760px; }
    .ca-step {
        border-top: 3px solid #dce5e4;
        color: var(--ca-muted);
        font-size: 0.82rem;
        padding-top: 0.7rem;
    }
    .ca-step strong { color: var(--ca-ink); display: block; margin-bottom: 0.12rem; }
    .ca-step.done { border-color: #14b8a6; }
    .ca-step.active { border-color: #0f766e; background: #f0fdfa; border-radius: 0 0 10px 10px; padding: 0.7rem; }
    .ca-section-copy { color: var(--ca-muted); margin-top: -0.6rem; margin-bottom: 1.2rem; }
    .ca-profile-score { color: var(--ca-muted); font-size: 0.85rem; margin-bottom: 0.35rem; }
    [data-testid="stDataFrame"] { border: 1px solid var(--ca-border); border-radius: 14px; overflow: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _split_lines(value: str) -> list[str]:
    return [item.strip() for item in value.splitlines() if item.strip()]


def _join_lines(values: list[str]) -> str:
    return "\n".join(values)


def profile_completion(profile: UserProfile) -> int:
    fields = (
        profile.target_roles,
        profile.skills,
        profile.education,
        profile.experience,
        profile.preferred_locations,
    )
    return round(sum(bool(field) for field in fields) / len(fields) * 100)


def render_hero() -> None:
    st.markdown(
        """
        <div class="ca-hero">
            <div class="ca-eyebrow" style="color:#99f6e4">AI job search workspace</div>
            <h1>Turn strong matches into ready-to-send applications.</h1>
            <p>Discover relevant roles, review the fit, tailor your CV, and stay in control before anything is sent.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_graph():
    return build_career_graph()


def current_interrupt(result: dict | None) -> dict | None:
    if not result:
        return None

    interrupts = result.get("__interrupt__", ())
    if not interrupts:
        return None

    item = interrupts[0]
    if isinstance(item, dict):
        return item.get("value", item)
    return item.value


def invoke_workflow(command: dict | Command) -> None:
    config = st.session_state.get("workflow_config")
    if config is None:
        st.error("The workflow session is missing. Start a new search.")
        return

    try:
        with st.spinner("Searching five job sources and ranking matches. This can take a few minutes..."):
            result = get_graph().invoke(command, config=config)
        st.session_state["workflow_result"] = result
        st.session_state.pop("workflow_error", None)
        st.rerun()
    except Exception as error:
        st.session_state["workflow_error"] = str(error)
        st.error(f"Workflow failed: {error}")


def reset_workflow() -> None:
    for key in (
        "workflow_config",
        "workflow_result",
        "workflow_error",
    ):
        st.session_state.pop(key, None)


def render_progress(result: dict) -> None:
    interrupt_payload = current_interrupt(result)
    interrupt_type = interrupt_payload.get("type") if interrupt_payload else None

    stages = [
        ("1", "Search and rank", bool(result.get("ranked_jobs"))),
        ("2", "Select job", bool(result.get("selected_job"))),
        ("3", "Review CV", "approved_changes" in result),
        ("4", "Create PDF", bool(result.get("tailored_cv_pdf_path"))),
        (
            "5",
            "Application",
            interrupt_type is None and bool(result.get("selected_job")),
        ),
    ]
    first_incomplete = next(
        (index for index, (_, _, complete) in enumerate(stages) if not complete),
        None,
    )
    columns = st.columns(len(stages))
    for index, (column, (number, label, complete)) in enumerate(zip(columns, stages)):
        state = "done" if complete else "active" if index == first_incomplete else ""
        marker = "Complete" if complete else "In progress" if index == first_incomplete else "Upcoming"
        column.markdown(
            f'<div class="ca-step {state}"><strong>{number}. {label}</strong>{marker}</div>',
            unsafe_allow_html=True,
        )


def render_job_selection(payload: dict, result: dict) -> None:
    st.markdown('<div class="ca-eyebrow">Step 2 of 5</div>', unsafe_allow_html=True)
    st.subheader("Choose your strongest match")
    st.markdown(
        '<p class="ca-section-copy">Review the shortlist and choose one role to tailor your application for.</p>',
        unsafe_allow_html=True,
    )

    jobs = payload.get("jobs", [])
    if not jobs:
        st.warning("No selectable jobs were returned.")
        return

    jobs_by_index = {job["index"]: job for job in jobs}
    options = list(jobs_by_index)
    selected_index = st.radio(
        "Ranked jobs",
        options=options,
        format_func=lambda index: (
            f"{jobs_by_index[index]['title']} — {jobs_by_index[index]['company']} "
            f"({jobs_by_index[index].get('score') or 0:.0f}% match)"
        ),
    )

    selected = jobs_by_index[selected_index]
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.markdown(f"### {selected['title']}")
        left.write(
            f"**{selected['company']}** · "
            f"{selected.get('location') or 'Location not specified'}"
        )
        left.caption(f"Source: {selected.get('source', 'Unknown')}")
        right.metric("Match", f"{selected.get('score') or 0:.0f}%")

        ranked_jobs = result.get("ranked_jobs", [])
        if selected_index < len(ranked_jobs):
            ranked_job = ranked_jobs[selected_index]
            if ranked_job.match_reasons:
                st.write("**Why it matches**")
                for reason in ranked_job.match_reasons:
                    st.write(f"- {reason}")
            if ranked_job.missing_skills:
                st.write("**Missing skills:** " + ", ".join(ranked_job.missing_skills))
            if ranked_job.recommendation:
                st.info(ranked_job.recommendation)

        if selected.get("url"):
            st.link_button("Open job posting", selected["url"])

    if st.button("Use this role", type="primary", icon="➡️"):
        invoke_workflow(Command(resume={"selected_index": selected_index}))


def render_cv_approval(payload: dict) -> None:
    job = payload.get("job", {})
    changes = payload.get("changes", [])

    st.markdown('<div class="ca-eyebrow">Step 3 of 5</div>', unsafe_allow_html=True)
    st.subheader("Review tailored CV changes")
    st.write(
        f"Target: **{job.get('title', 'Selected role')}** at "
        f"**{job.get('company', 'the company')}**"
    )

    if not changes:
        st.info("The agent found no useful factual changes for this job. The original CV will be compiled.")

    with st.form("cv_approval_form"):
        reviewed: list[tuple[int, bool, str, str]] = []
        for change in changes:
            index = change["index"]
            with st.container(border=True):
                approved = st.checkbox(
                    f"Approve change {index + 1}",
                    value=True,
                    key=f"approve_cv_{index}",
                )
                st.text_area(
                    "Original text",
                    value=change.get("original", ""),
                    disabled=True,
                    height=110,
                    key=f"original_cv_{index}",
                )
                proposed = st.text_area(
                    "Proposed text (editable)",
                    value=change.get("proposed", ""),
                    height=110,
                    key=f"proposed_cv_{index}",
                )
                st.caption(f"Reason: {change.get('reason', '')}")
                reviewed.append((index, approved, proposed, change.get("proposed", "")))

        continue_clicked = st.form_submit_button(
            "Apply choices and create PDF",
            type="primary",
        )

    if continue_clicked:
        approved_indices = [index for index, approved, _, _ in reviewed if approved]
        edited_changes = {
            str(index): proposed
            for index, approved, proposed, original_proposal in reviewed
            if approved and proposed != original_proposal
        }
        invoke_workflow(
            Command(
                resume={
                    "approved_indices": approved_indices,
                    "edited_changes": edited_changes,
                }
            )
        )


def render_downloads(result: dict) -> None:
    tex_path = Path(result.get("tailored_cv_path", TAILORED_CV_PATH))
    pdf_value = result.get("tailored_cv_pdf_path")
    pdf_path = Path(pdf_value) if pdf_value else None

    if not tex_path.exists() and not (pdf_path and pdf_path.exists()):
        return

    st.subheader("Tailored CV")
    columns = st.columns(2)
    if tex_path.exists():
        columns[0].download_button(
            "Download LaTeX CV",
            data=tex_path.read_bytes(),
            file_name=tex_path.name,
            mime="application/x-tex",
            width="stretch",
        )
    if pdf_path and pdf_path.exists():
        columns[1].download_button(
            "Download PDF CV",
            data=pdf_path.read_bytes(),
            file_name=pdf_path.name,
            mime="application/pdf",
            width="stretch",
        )


def render_email_approval(payload: dict, result: dict) -> None:
    job = payload.get("job", {})
    email = payload.get("email", {})
    recipient = payload.get("recipient_email") or result.get("recipient_email")

    render_downloads(result)
    st.markdown('<div class="ca-eyebrow">Final review</div>', unsafe_allow_html=True)
    st.subheader("Review application email")
    st.write(
        f"Application for **{job.get('title', 'the selected role')}** at "
        f"**{job.get('company', 'the company')}**"
    )
    st.write(f"**Recipient:** {recipient or 'No recipient found'}")

    with st.form("email_approval_form"):
        subject = st.text_input("Subject", value=email.get("subject", ""))
        body = st.text_area("Body", value=email.get("body", ""), height=260)
        confirm = st.checkbox(
            "I reviewed this email and authorize Career Agent to send it now."
        )
        send_clicked = st.form_submit_button("Send application email", type="primary")
        skip_clicked = st.form_submit_button("Keep as shortlisted without sending")

    if send_clicked:
        if not recipient:
            st.error("No recipient email is available, so this application cannot be sent.")
        elif not confirm:
            st.warning("Check the authorization box before sending.")
        else:
            invoke_workflow(
                Command(
                    resume={"approved": True, "subject": subject, "body": body}
                )
            )
    elif skip_clicked:
        invoke_workflow(
            Command(resume={"approved": False, "subject": subject, "body": body})
        )


def render_completed(result: dict) -> None:
    if result.get("no_jobs_message"):
        st.info(result["no_jobs_message"])
        return
    selected_job = result.get("selected_job")
    if not selected_job:
        return

    st.markdown('<div class="ca-eyebrow">Workflow complete</div>', unsafe_allow_html=True)
    st.subheader("Application prepared")
    if result.get("email_sent"):
        st.success(
            "The application email was sent successfully. "
            f"Gmail message ID: {result.get('gmail_message_id')}"
        )
    else:
        st.info("The job remains shortlisted; no email was sent.")

    contact = result.get("application_contact")
    if contact is None:
        st.warning("No application email or application page was found automatically.")
    elif contact.method in {"website", "linkedin", "linkedin_easy_apply"} and contact.application_url:
        st.write("This employer requires a manual website application.")
        st.link_button("Open application page", contact.application_url, type="primary")
    elif contact.method == "email" and contact.email:
        st.write(f"Contact: **{contact.email}**")

    render_downloads(result)


init_db()
saved_profile = load_profile() or UserProfile()
applications = get_applications()

render_hero()

with st.sidebar:
    st.markdown('<div class="ca-eyebrow">Your workspace</div>', unsafe_allow_html=True)
    st.header("Candidate profile")
    completion = profile_completion(saved_profile)
    st.markdown(
        f'<div class="ca-profile-score">Profile strength · {completion}%</div>',
        unsafe_allow_html=True,
    )
    st.progress(completion, text=None)
    st.caption("A complete profile produces more relevant rankings and CV suggestions.")
    with st.form("profile_form"):
        target_roles = st.text_area(
            "Target roles",
            value=_join_lines(saved_profile.target_roles),
            height=100,
            placeholder="Data analyst\nAI engineer",
            help="Add one role per line.",
        )
        skills = st.text_area(
            "Skills",
            value=_join_lines(saved_profile.skills),
            height=150,
            placeholder="Python\nSQL\nMachine learning",
            help="Add one skill per line.",
        )
        education = st.text_area("Education", value=saved_profile.education or "")
        experience = st.text_area(
            "Experience",
            value=_join_lines(saved_profile.experience),
            height=110,
            help="Add one experience item per line.",
        )
        preferred_locations = st.text_area(
            "Preferred locations",
            value=_join_lines(saved_profile.preferred_locations),
            height=90,
            placeholder="Casablanca\nRabat",
            help="Add one location per line.",
        )
        remote = st.checkbox("Accept remote jobs", value=saved_profile.remote)
        save_clicked = st.form_submit_button(
            "Save profile", type="primary", width="stretch", icon="✅"
        )

    if save_clicked:
        updated_profile = UserProfile(
            target_roles=_split_lines(target_roles),
            skills=_split_lines(skills),
            education=education.strip() or None,
            experience=_split_lines(experience),
            preferred_locations=_split_lines(preferred_locations),
            remote=remote,
        )
        save_profile(updated_profile)
        st.toast("Profile saved", icon="✅")
        st.rerun()

workflow_tab, applications_tab, application_center_tab = st.tabs(["✨ New application", "📋 Application tracker", "Application Center"])

with workflow_tab:
    if st.session_state.get("workflow_config"):
        if st.button("Start a new search", icon="🔄"):
            reset_workflow()
            st.rerun()

    if not st.session_state.get("workflow_config"):
        st.markdown('<div class="ca-eyebrow">Discover roles</div>', unsafe_allow_html=True)
        st.subheader("What opportunity are you looking for?")
        st.markdown(
            '<p class="ca-section-copy">Search across configured sources, then let the agent rank results against your profile.</p>',
            unsafe_allow_html=True,
        )
        with st.form("search_form", border=True):
            query = st.text_input(
                "Role or keywords",
                value=saved_profile.target_roles[0] if saved_profile.target_roles else "AI Engineer",
                placeholder="e.g. Machine learning internship",
                icon="🔎",
            )
            first_location = (
                saved_profile.preferred_locations[0]
                if saved_profile.preferred_locations
                else "Morocco"
            )
            location_column, country_column = st.columns(2)
            location = location_column.text_input("Location", value=first_location, icon="📍")
            country = country_column.text_input("Country code", value="MA", max_chars=2)
            with st.expander("Search preferences"):
                post_date = st.date_input(
                    "Published on or after (optional)", value=None,
                    help="Keep jobs published on the selected date or later. Timestamps use UTC; date-only sources use their reported day. Jobs without a known date are excluded.",
                )
                limit_column, prefilter_column, score_column = st.columns(3)
                limit = limit_column.number_input(
                    "Jobs per source", min_value=1, max_value=50, value=5,
                    help="Maximum number collected from each configured job source.",
                )
                prefilter_top_k = prefilter_column.number_input(
                    "AI ranking pool", min_value=1, max_value=50, value=10,
                    help="Number of jobs evaluated in depth by the ranking agent.",
                )
                minimum_score = score_column.slider(
                    "Minimum match", min_value=0, max_value=100, value=50,
                    format="%d%%",
                )
            start_clicked = st.form_submit_button(
                "Find matching jobs", type="primary", icon="✨", width="stretch"
            )

        if start_clicked:
            if not query.strip():
                st.warning("Enter a search query.")
            elif load_profile() is None:
                st.warning("Save your candidate profile before searching.")
            elif not Path(BASE_CV_PATH).exists():
                st.error(f"Base CV not found at {BASE_CV_PATH}.")
            else:
                st.session_state["workflow_config"] = {
                    "configurable": {"thread_id": f"streamlit-{uuid4()}"}
                }
                invoke_workflow(
                    {
                        "query": query.strip(),
                        "location": location.strip() or "Morocco",
                        "country": country.strip().upper() or "MA",
                        "limit_per_source": int(limit),
                        "post_date": post_date.isoformat() if post_date else None,
                        "prefilter_top_k": int(prefilter_top_k),
                        "minimum_score": float(minimum_score),
                        "cv_path": BASE_CV_PATH,
                        "tailored_cv_path": TAILORED_CV_PATH,
                    }
                )
    else:
        result = st.session_state.get("workflow_result", {})
        if result:
            render_progress(result)
            payload = current_interrupt(result)
            if payload:
                payload_type = payload.get("type")
                if payload_type == "job_selection":
                    render_job_selection(payload, result)
                elif payload_type == "cv_approval":
                    render_cv_approval(payload)
                elif payload_type == "email_approval":
                    render_email_approval(payload, result)
                else:
                    st.error(f"Unsupported workflow checkpoint: {payload_type}")
            else:
                render_completed(result)
        else:
            st.info("The workflow is starting. If this message remains, start a new workflow.")

    if st.session_state.get("workflow_error"):
        st.error(st.session_state["workflow_error"])

with applications_tab:
    st.markdown('<div class="ca-eyebrow">Pipeline</div>', unsafe_allow_html=True)
    st.subheader("Your application tracker")
    if applications:
        summary_columns = st.columns(4)
        summary_columns[0].metric("Total roles", len(applications))
        summary_columns[1].metric(
            "Shortlisted", sum(item["status"] == "shortlisted" for item in applications)
        )
        summary_columns[2].metric(
            "Applied", sum(item["status"] == "applied" for item in applications)
        )
        summary_columns[3].metric(
            "Interviews", sum(item["status"] == "interview" for item in applications)
        )
        status_filter = st.selectbox(
            "Filter by status",
            options=["all", "found", "shortlisted", "applied", "responded", "interview", "rejected", "offer", "delivery_failed"],
            format_func=lambda value: value.replace("_", " ").title(),
        )
        filtered = (
            applications
            if status_filter == "all"
            else [item for item in applications if item["status"] == status_filter]
        )
        visible_columns = [
            "title", "company", "location", "status", "match_score", "source", "updated_at"
        ]
        st.dataframe(
            [{key: item.get(key) for key in visible_columns} for item in filtered],
            width="stretch",
            hide_index=True,
            column_config={
                "title": st.column_config.TextColumn("Role"),
                "company": st.column_config.TextColumn("Company"),
                "location": st.column_config.TextColumn("Location"),
                "status": st.column_config.TextColumn("Status"),
                "match_score": st.column_config.ProgressColumn(
                    "Match", min_value=0, max_value=100, format="%.0f%%"
                ),
                "source": st.column_config.TextColumn("Source"),
                "updated_at": st.column_config.DatetimeColumn("Last updated", format="D MMM, HH:mm"),
            },
        )
    else:
        st.info("No saved applications yet. Start a search to build your pipeline.", icon="💡")

with application_center_tab:
    render_application_center()
