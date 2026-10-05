# docint: document intelligence (Member 4)

Turns the policy PDF and the rejection letter into structured data, proves that cited clauses really exist
in the policy, and measures it on synthetic cases. No LLM is used here.

## What the backend calls

```python
import docint.claimlens_docs as docs   # or `import claimlens_docs` when docint/ is on the path

docs.ingest_document(path, doc_id, doc_type)  # -> ParsedDocument; doc_type is "policy" or "letter"
docs.extract_rejection(letter_doc)            # -> RejectionExtraction; each reason has page + bbox
docs.extract_policy_facts(policy_doc)         # -> PolicyFacts; waiting_periods = [{kind, months, chunk_id, days?}]
docs.sanitize_for_llm(text)                   # -> SanitizedText: PII masked, injection flags, delimited for the prompt
docs.locate_quote(doc_or_doc_id, quote)       # -> LocateResult {found, match_score, page, bbox, chunk_id, reason}
```

The returned models subclass the classes in `contracts/schemas.py`, so `contracts.schemas.X.model_validate(...)`
accepts them. Extra fields are optional.

- `chunk_id` is `<doc_id>-c-NNNN` for policy clauses and `<doc_id>-l-NNNN` for letter lines (one chunk per line,
  so a reason is highlighted on exactly its lines).
- `has_text_layer` is false only when no page has text; scanned pages are listed in `pages_without_text`.
- Rule checks: `docint.claimlens_docs.rules` (`waiting_period_elapsed`, `event_within_policy_period`,
  `amount_within_limit`); `RuleResult.to_dict()` gives `{rule, inputs, result, explanation}`.

## Modules

| Module | What it does |
|---|---|
| `ingest.py` | PyMuPDF: lines with bounding boxes in reading order; pages with no text layer |
| `structure.py` | Sectionizer: one chunk per clause, `section_path` like `4 Waiting Periods > 4.2`, tags `WAITING_PERIOD` / `EXCLUSION` / `DEFINITION` |
| `grounding.py` | `locate_quote`: exact, then fuzzy (rapidfuzz) match; a close match is rejected if a number or a meaning word differs |
| `letter.py` | Claim/policy numbers, dates, amounts, each rejection reason with category, clause refs, page and bbox |
| `policy_facts.py` | Policy period, inception, sum insured, waiting periods, room-rent limit, each with its source chunk |
| `rules/checks.py` | Date and amount rules used by the verifier |
| `security/` | `redact`, `validate_upload`, `detect_injection` + `sanitize_for_llm`, `purge_expired` |

## Run

From the repo root:

```bash
pip install -r requirements.txt
pytest -q                      # backend + docint tests (pytest.ini)
python -m eval.run             # writes eval/report.json, served by GET /api/eval/latest
python -m eval.run --gate      # exits 1 if a metric drops below target (CI)
python -m eval.make_pdfs       # renders eval/data/*.txt to eval/data/pdf/
python -m eval.make_fixtures   # regenerates contracts/fixtures/*.json from the PDFs
```

Retrieval recall@3 and verdict agreement need the AI pipeline's output:
`python -m eval.run --predictions predictions.json` with
`{"case_01": {"reasons": [{"retrieved_clause_ids": ["4.2"], "verdict": "SUPPORTED"}]}}`.

## Results on 11 synthetic cases and 2 synthetic policies

| Metric | Result |
|---|---|
| Letter extraction matches gold (text and rendered PDF) | 11 of 11 |
| Policy facts match gold (both policies) | 18 of 18 |
| Fabricated citations rejected by `locate_quote` | 63 of 63 |
| Real quotes accepted (verbatim, reformatted, one typo) | 78 of 78 |
| Retrieval recall@3, verdict agreement | not measured yet |

The two policies use different styles (`SECTION 4.` / `4.2` versus `PART 3 -` / `3.2.`, and different field
names such as "Policy Period" / "Period of Insurance"), so extraction is not tuned to one layout only.
Exact counts on a small synthetic set, not accuracy on real claims. See `docs/LIMITATIONS.md`.

## Deploy

`docker-compose.yml` (Postgres + pgvector, backend, frontend), `deploy/Dockerfile.backend|frontend`
(non-root, healthchecks, one worker) and `deploy/render.yaml`. Uploads and the SQLite file live in `/data`;
mount a persistent disk there.
