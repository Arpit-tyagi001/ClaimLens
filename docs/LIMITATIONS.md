# Known limitations (ClaimLens)

ClaimLens is a hackathon MVP built in about 30 hours. It uses production-style engineering practices (validation, tests, audit trail, error handling), but it is **not a production-ready product**. This file lists what it does not do. The backend items below are from Member 1. Other members should add their own sections.

## What the product is and is not

- It compares a rejection letter with the policy wording and shows evidence. It does **not** give legal advice and does **not** predict the outcome of an appeal.
- Output wording is "the cited clauses do / do not appear to support this ground". It may be wrong.
- The demo uses **synthetic data only**. No real personal data should be uploaded, committed or logged.

## Backend limitations

### Database
- **SQLite is a temporary local fallback.** The target stack is PostgreSQL 16 with pgvector, but the backend currently runs on SQLite because Docker was unavailable on the development machine. JSON fields are stored as text columns.
- **No Alembic migrations.** Tables are created with `create_all`, which does not alter existing tables. After a model change the local database file must be deleted.
- **No durable job queue.** Pipeline stages run as in-process asyncio tasks. State is persisted in `pipeline_events`, and unfinished cases are resumed on startup, but there is no `jobs` table and no multi-worker coordination. Run a single worker.
- **Per-case event numbering uses max+1.** This is safe with a single writer per case, which the in-process concurrency guard enforces. A unique `(case_id, seq)` constraint catches collisions, but multi-worker deployments would need a different approach.

### Mock stages
- **Pipeline stages are mocks until `claimlens_docs` (Member 4) and `claimlens_ai` (Member 3) are wired in.** With `MOCK_DOCS=true` or `MOCK_AI=true`, extraction, findings and verification are synthetic fixtures, not real analysis. The API reports this in the `mode` field, and the UI should label it.
- The draft generator is a deterministic template when `MOCK_AI` is true. It is not an LLM draft.
- Trace token counts and model names are empty or `"mock"` for mock stages.
- If a real package is missing and `ALLOW_MOCK_FALLBACK` is true, the pipeline falls back to the mock stage and emits an event saying so. This setting should be false outside development.

### Audit log and deletion
- The audit log is append-only: database triggers block `UPDATE` and `DELETE`, and no API route modifies it.
- **Deleting a case removes its documents, chunks, findings, facts, events and files, but audit rows are retained.** A final `CASE_DELETED` row is appended. Because audit rows store before and after copies of review actions, **they can contain finding text** (reasoning and quoted clauses) after the case is deleted. This is a known tension between "data is removed" and "append-only audit". A stricter design would store only ids and statuses in audit rows.
- The audit trigger is implemented for SQLite only. The PostgreSQL version is not done.

### Security and access
- **No authentication, accounts or multi-tenancy.** Anyone who can reach the API and knows a case id can read or delete that case.
- **Case ids are the only access control.** They are random, but they are not a substitute for authentication.
- **Rate limiting is in-memory and per process,** keyed by client IP. It resets on restart, does not work across multiple workers, and can be bypassed with many IP addresses or by clients behind one shared IP.
- The reviewer in the audit log is a fixed label (`reviewer`), not a verified identity.
- CORS is restricted to configured origins, and the debug router is mounted only when `ENV` is not `production`. Neither has been security-tested.
- No tested compliance with any data protection law.

### Uploads and documents
- **PDF only, 10 MB per file** (configurable with `MAX_UPLOAD_BYTES`). Files are checked by magic bytes (`%PDF-`), not by extension or declared content type. There is no malware scanning and no deep PDF structure validation.
- A policy PDF with no text layer stops the pipeline at `EXTRACTING` with a clear error. OCR is not part of the backend.
- One policy and one letter per case.
- **Uploads and cases are purged after the retention window** (`RETENTION_HOURS`, default 24). The purge runs at startup and then every 30 minutes while the process is running. If the server is down, expired data stays until it restarts.

### Pipeline behaviour
- **Stage timeouts abandon work, they do not kill it.** A synchronous stage function runs in a worker thread. After a timeout the pipeline moves on, but the thread may keep running until it finishes.
- `PipelineException` defaults to non-retryable. Transient errors from other modules must be raised as ordinary exceptions to be retried.
- The confirm-facts step validates dates and non-negative values, but it cannot tell whether the facts match the policy document.

### Evaluation
- `GET /api/eval/latest` only serves the stored report. The backend does not compute metrics. Any figures come from a small synthetic set and are not general accuracy.

## Document intelligence limitations (docint, M4)

### Ingestion and sections
- Scanned pages are detected (`pages_without_text`, `has_text_layer`) but not read: there is no OCR.
- Sections are found by numbering, heading font size and all-caps lines. Two-column layouts, tables and running headers or footers are not handled and can end up inside a clause chunk.
- Heading lines are not chunks, so a quote of a heading alone is not grounded.

### Extraction
- Letter and policy extraction use regular expressions and keywords. Unusual wording or tables may give empty or wrong fields; every policy fact is shown to the user to confirm or correct.
- Reason categories come from keywords. A reason that mentions two categories gets the first match in a fixed order (pre-existing, waiting period, missing documents, limit, exclusion); anything else goes to a human.
- Only English, and only day-first Indian date formats. A waiting period stated in days is rounded to whole months in `months` and kept exactly in `days`.

### Grounding (`locate_quote`)
- A close fuzzy match is rejected if any number or meaning word (not, only, unless, excluded, covered, ...) differs. A fabricated quote that changes only an ordinary word and stays above 90% similar would still be accepted.
- Highlight boxes cover the whole chunk (clause or letter line) that contains the quote, not the exact words. A quote that crosses a page break is boxed on its first page only.
- `locate_quote(doc_id, ...)` looks documents up in an in-process registry filled by `ingest_document`; another process must call `register_document` first (for example after loading chunks from the database).

### Evaluation data
- 9 synthetic cases and one synthetic policy, written by the team; 3 cases where the rejection is supported. The extraction rules were written while looking at these letters, so extraction scores on this set are optimistic.
- The PDFs in `eval/data/pdf/` are rendered by our own script (`eval/make_pdfs.py`). Real insurer PDFs will be harder.
- The injected-fault benchmark uses a fixed set of fabricated quotes. Catching all of them shows the grounding gate works on these kinds of fakes, not that every hallucination is caught.

### Safety
- PII masking covers phone numbers, email, Aadhaar-like and PAN-like numbers. It misses names and addresses and may mask a harmless 10- or 12-digit number.
- The prompt-injection guard flags common instruction-like phrases and always wraps document text as delimited data. That reduces, not removes, the risk.

## Roadmap (not done)
- Move to PostgreSQL with pgvector, Alembic migrations and a `SELECT ... FOR UPDATE SKIP LOCKED` job queue.
- Wire the real `claimlens_docs` and `claimlens_ai` packages and run the whole flow with the mock switches off.
- Store only ids and statuses in audit rows, or encrypt them.
- Authentication, per-user case ownership and a shared rate limiter.
- Real reviewer identities in the audit trail.