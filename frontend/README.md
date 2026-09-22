# CareerAI web workspace

Next.js App Router, React, TypeScript, Tailwind CSS v4 and locally owned shadcn/ui components. FastAPI connects these screens to the existing Python services. The original Streamlit app and CLI remain available.

## Run locally

From the repository root, install Python dependencies with `uv sync`. Then:

```powershell
cd frontend
npm.cmd install
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/run_web.py
```

Open **http://127.0.0.1:3000**. FastAPI's endpoint documentation is at **http://127.0.0.1:8000/docs**. Ctrl+C stops both services. For development, use `scripts/run_web.py --dev`.

Alternatively, run the backend and frontend in separate terminals:

```powershell
.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm.cmd run dev
```

On macOS/Linux use `npm` and `.venv/bin/python`. Configure `CAREERAI_API_URL` before building/running Next.js if the API uses another address. Existing provider keys remain in the root `.env`; they are never exposed to the browser. Gmail continues using the existing `credentials.json` and `token.json` files.

## Workflow and persistence

- **Search jobs:** calls the existing five-source collector, prefilter and ranker. The optional date is inclusive: published on or after the selected day.
- **Job review:** select a ranked role. Already submitted roles cannot start another application.
- **CV tailoring:** compare original/tailored text, switch between Side-by-Side and Changes-Only, edit proposed LaTeX fragments, or revert/restore individual changes. The final PDF is compiled by the existing LaTeX service. MiKTeX or TeX Live is required for real CV compilation; a missing compiler leaves the workflow at CV review with a recoverable error.
- **Email review:** edit recipient, subject and body, and open the actual PDF attachment. External links and LinkedIn Easy Apply lead to manual submission instructions.
- **Send application:** a separate confirmation is required. Gmail sends use the existing idempotent send service. Website/Easy Apply submissions are confirmed manually after completing their forms.

**Applications** contains durable unfinished workflows and saved opportunities from the existing database. A workflow snapshots the original CV and profile; Settings changes apply to new workflows. Edits save through the API and use revision checks to prevent stale tabs overwriting each other. The new `web_workflows` table is added to `data/career_agent.db`; PDFs and CV snapshots live under `data/web_workflows/<id>/`. Back up both the database and this folder.

**Application Tracker** contains submitted records from the existing store, with Applied, Interview, Offer and Rejected status controls, email details, recruiter response text and history. The existing inbox classifier handles interview invitations and rejections; ambiguous replies require review. **Settings** controls the existing profile, original LaTeX CV, Gmail consent and polling interval. Polling runs while FastAPI is running. Gmail consent opens on the backend computer.

## API implementation

`src/api/main.py` exposes validated endpoints. `src/api/workflows.py` calls the existing graph nodes and application services; it does not implement new search, ranking, AI tailoring, email drafting or Gmail logic. `src/api/storage.py` stores UI checkpoints in SQLite.

Long actions run in FastAPI background tasks; the frontend polls persisted progress. Use **one backend worker** for this local app. A server restart marks interrupted actions as errors and preserves review data. Tasks are not silently retried, especially sends. The existing send ledger blocks uncertain duplicates; reconcile those with Gmail Sent using the existing Application Center before retrying.

This is a local, single-user workspace bound to loopback. It has origin/host checks but no user authentication or tenant isolation; public hosting requires those additions and a durable worker queue. In-progress Streamlit sessions cannot be recovered because their checkpoints were held in memory; persisted prepared applications can be continued from the saved-opportunity list.

## Verification

```powershell
cd frontend
npm.cmd run build
```

Framework setup follows the [Next.js documentation](https://nextjs.org/docs), [shadcn/ui manual installation](https://ui.shadcn.com/docs/installation/manual), [Tailwind Next.js guide](https://tailwindcss.com/docs/installation/framework-guides/nextjs), and [FastAPI background task guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/).
