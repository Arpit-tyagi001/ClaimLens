Backend, API and pipeline orchestration (Member 1)
## AI Investigation, Retrieval, Verification and Drafting (Member 3)

`ai/claimlens_ai` provides the AI investigation, evidence retrieval, adversarial verification and drafting pipeline. It plugs into the backend as plain Python functions through the shared typed contracts.

### What it does

**Investigator**

* Takes a rejection reason and case facts.
* Searches relevant policy evidence.
* Creates a structured finding with assessment, confidence, reasoning and evidence citations.
* Uses the retrieval layer to ground findings in policy chunks.

**Retrieval**

The full retrieval implementation uses:

* PostgreSQL
* PostgreSQL full-text search
* pgvector
* Sentence Transformers embeddings
* Reciprocal Rank Fusion (RRF)

The retrieval pipeline combines lexical search and vector similarity to find relevant policy evidence.

**Verifier**

Every finding passes through deterministic verification before it can be used for drafting.

The verifier:

1. Checks that cited chunks exist.
2. Checks that the quoted evidence is actually present in the cited chunk.
3. Performs fact consistency checks.
4. Runs one adversarial challenge.
5. Allows exactly one investigator rebuttal.
6. Applies a deterministic final verification decision.
7. Ensures verification confidence cannot increase.

Supported verification statuses:

* `VERIFIED`
* `DOWNGRADED`
* `NEEDS_HUMAN`
* `REJECTED_UNGROUNDED`

A fabricated citation is rejected when the quoted text cannot be grounded in the cited policy chunk.

**Drafting**

The drafter uses only approved/verified findings and includes evidence chunk citations in the generated draft.

### Fake Citation Verification

The M3 test suite includes a fake-citation injection test:

```bash
python -m ai.claimlens_ai.test_verifier --inject-fake-citation
```

A genuine citation is verified successfully. When the citation is replaced with fabricated policy text, the verifier detects that the quote does not exist in the source chunk and returns:

```text
status: REJECTED_UNGROUNDED
final_confidence: 0.0
```

This demonstrates that the verification layer does not blindly trust an AI-generated citation.

### Runtime Requirements

The complete M3 retrieval implementation requires a PostgreSQL environment with pgvector and Python dependencies including Sentence Transformers and PyTorch.

The deployed submission uses **mock AI/retrieval stages** because the full PostgreSQL + pgvector + PyTorch stack is too resource-intensive for the available deployment environment.

The complete implementation and local verification tests are available on the `member-3-ai` branch.

### Scope and Safety

ClaimLens uses synthetic claim and policy data for demonstration and evaluation.

The system is not intended to:

* provide legal advice;
* predict whether an appeal will succeed;
* make final real-world insurance decisions;
* replace a qualified investigator or human reviewer.

Verification confidence represents the strength of the available evidence and does not guarantee the correctness of a real-world insurance or legal decision.

The FastAPI backend sits between the browser and the AI and document packages. It owns the API, the data model, the staged pipeline runner, live progress streaming, the human-review workflow and the audit trail. claimlens_docs (Member 4) and claimlens_ai (Member 3) plug in as plain Python functions through typed contracts.

Status: the pipeline stages currently run as mock stages unless MOCK_DOCS=false / MOCK_AI=false and the real packages are importable. GET /api/cases/{id} returns a mode field so the UI can show which mode is active. This is a synthetic-data demo, and the output is not legal advice.

What it does
Staged pipeline: UPLOADED -> EXTRACTING -> AWAITING_FACTS -> INVESTIGATING -> VERIFYING -> READY_FOR_REVIEW.
Per-stage timeout, bounded retry with exponential backoff and jitter, optional fallback. A permanent error (PipelineException(retryable=False)) is not retried. Everything else is.
Persisted state and resume. Every transition is a row in pipeline_events with a monotonic per-case seq. After a restart, cases that were mid-run are resumed from the last event.
Human-in-the-loop pause. The runner stops at AWAITING_FACTS until the user confirms policy facts. A confirmed PUT resumes the run.
Live progress over SSE with Last-Event-ID replay, so a page reload rebuilds the timeline.
Review workflow. Approve, reject or edit each finding. Every action writes an append-only audit_log row (database triggers block UPDATE and DELETE).
Draft and export. A draft is built from approved or edited findings only. The evidence bundle (JSON or HTML) includes verifier results and the audit trail.
Hygiene. PDF magic-byte and size validation, uniform error shape with no stack traces to clients, request ids, JSON logs, rate limiting, case deletion and TTL purge.
Run it

From the repo root (Python 3.12 is the project target):

bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env           # then edit if needed
uvicorn backend.app.main:app --reload

Interactive docs: http://localhost:8000/docs

Run the tests from the repo root:

bash
pytest -q

If you change the database models, delete the local SQLite file (claimlens_local.db) and restart. The app uses create_all, which does not alter existing tables.

Configuration (.env)

See .env.example for every variable. The main ones:

Variable	Default	Purpose
ENV	dev	production disables the debug router and wildcard CORS
MOCK_DOCS / MOCK_AI	true	Use mock stages instead of claimlens_docs / claimlens_ai
ALLOW_MOCK_FALLBACK	true in dev	If a real package is missing, run the mock and say so in an event
MAX_UPLOAD_BYTES	10 MB	Per-file upload cap
RETENTION_HOURS	24	Uploads and cases older than this are purged
RATE_LIMIT_UPLOAD_PER_MIN / RATE_LIMIT_REVIEW_PER_MIN	10 / 60	In-memory per-IP limits
CORS_ORIGINS	http://localhost:5173	Allowed frontend origins
Endpoints

Errors always use {"error": {"code": "...", "message": "...", "stage": "..." | null}}.

Method and path	Purpose
POST /api/cases	Upload policy and letter PDFs. Returns 202 {case_id, status} and starts the pipeline
GET /api/cases/{id}	Case state: stage, documents with file_url, facts_confirmed, mode
DELETE /api/cases/{id}	Delete case data and files
POST /api/cases/{id}/resume	Resume or retry a stopped run
GET /api/cases/{id}/events	SSE stream of stage events (seq, stage, status, detail, ts)
GET /api/cases/{id}/documents/{doc_id}/file	Stream the stored PDF
GET /api/cases/{id}/policy-facts	Extracted policy facts for the confirm form
PUT /api/cases/{id}/policy-facts	Save confirmed facts and resume the pipeline
GET /api/cases/{id}/findings	Findings with review status
POST /api/findings/{id}/review	{action: APPROVE or REJECT or EDIT, note?, edited_reasoning?}
GET /api/cases/{id}/audit	Append-only audit trail
GET /api/cases/{id}/review-summary	Counts and can_draft
POST /api/cases/{id}/draft	Draft from approved or edited findings only
GET /api/cases/{id}/draft/latest	Latest draft
GET /api/cases/{id}/export?format=json|html	Evidence bundle
GET /api/cases/{id}/trace	Per-stage latency, attempts, retries, fallback
GET /api/eval/latest	Contents of eval/report.json (or {"available": false})
GET /healthz, GET /readyz	Liveness and readiness
GET/DELETE /debug/fail-stage	Dev only. Failure injection
Demo: failure injection, retry, fallback and recovery

Use ENV=dev. Open a second terminal for the stream (on Windows use curl.exe, not curl).

1. Retry, then success

bash
curl "http://localhost:8000/debug/fail-stage?stage=INVESTIGATING&mode=once"

Upload a case at /docs and copy the case_id, then watch the stream:

bash
curl.exe -N http://localhost:8000/api/cases/<case_id>/events

At AWAITING_FACTS, PUT the facts back to /api/cases/<case_id>/policy-facts (or use the form). In INVESTIGATING you should see RUNNING, then RETRYING, then RUNNING and COMPLETED.

2. Retries exhausted, then fallback

bash
curl "http://localhost:8000/debug/fail-stage?stage=INVESTIGATING&mode=fail"

Repeat the upload and confirm. You should see RETRYING events, then FALLBACK, then COMPLETED with the detail "completed via fallback".

3. Timeout

Use mode=timeout. The stage is cut off by its timeout and retried.

4. Crash recovery

Stop the server (Ctrl+C) while a case is in INVESTIGATING and start it again. On startup the runner finds cases whose last event was RUNNING, RETRYING or FALLBACK and resumes them. Stages that already completed are not run again.

Reset with DELETE /debug/fail-stage.

Tests

The suite covers retry semantics, timeouts, fallback, resume after a simulated crash, the duplicate-start guard, event sequence numbers, SSE replay and keep-alive, upload validation, PDF serving and path-traversal protection, uniform errors including 500s, the facts confirm flow, the review and audit log (including append-only enforcement and rollback atomicity), delete and TTL purge, rate limiting, draft, export (including HTML escaping), trace, and wiring behind the mock switches with fake packages.

Run pytest -q for the current count. See docs/LIMITATIONS.md for what this backend does not do.
Document intelligence, grounding, evaluation and deployment (Member 4)

docint/claimlens_docs turns the policy PDF and the rejection letter into structured data and proves that cited clauses exist in the policy. No LLM is used in it. The backend imports it as docint.claimlens_docs.

ingest_document reads a PDF with PyMuPDF (lines with bounding boxes, pages with no text layer). The sectionizer makes one chunk per policy clause with a section_path such as "4 Waiting Periods > 4.2".
extract_rejection and extract_policy_facts return the contract models; every rejection reason has a page and bbox, and every policy fact keeps the chunk it came from.
locate_quote finds a quote in the policy (exact, then fuzzy) and returns page, bbox and chunk_id. A close match is rejected if any number or word such as "not", "only" or "excluded" differs.
Rule checks (waiting period, policy period, limits), PII masking, prompt-injection flags with delimited data, upload validation and TTL purge.
Run the evaluation from the repo root:

python -m eval.run           # metrics table; writes eval/report.json, served by GET /api/eval/latest
python -m eval.run --gate    # exits 1 if a metric drops below target (CI)

On 11 synthetic cases and 2 synthetic policies: letter extraction matches gold in 11 of 11 (also through rendered PDFs), policy facts 18 of 18, fabricated citations rejected 63 of 63, real quotes accepted 78 of 78. Retrieval recall@3 and verdict agreement are not measured yet. These are exact counts on a small synthetic set, not accuracy on real claims.

Deployment: docker compose up --build starts Postgres with pgvector, the backend and the frontend (http://localhost:5173). deploy/ has the Dockerfiles (non-root, healthchecks, one worker) and a Render blueprint. CI (.github/workflows/ci.yml) runs lint, type checks, pytest, the AI tests, the eval gate, gitleaks, the frontend build and the Docker builds. Details: docint/README.md.
