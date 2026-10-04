import json
import logging
from typing import List, Optional
from pydantic import BaseModel
from backend.app.db.models import Finding

logger = logging.getLogger("claimlens.draft")


class Citation(BaseModel):
    finding_id: str
    chunk_id: str
    quote: str
    page: int
    section_path: str


class DraftResult(BaseModel):
    draft_id: str = ""
    text: str
    citations: List[Citation]
    based_on_finding_ids: List[str]
    skipped_finding_ids: List[str] = []


def generate_draft(case_id: str, approved_findings: List[Finding]) -> DraftResult:
    # TODO(wire): replace with claimlens_ai.draft_review_request
    paragraphs = []
    all_citations: List[Citation] = []
    based_on_ids: List[str] = []
    skipped_ids: List[str] = []

    citation_counter = 1

    for finding in approved_findings:
        # Determine reasoning text: EDITED uses edited_reasoning if available, else payload reasoning
        try:
            payload = json.loads(finding.payload_json or "{}")
        except Exception:
            payload = {}

        evidence_list = payload.get("evidence", [])
        if not evidence_list:
            skipped_ids.append(finding.finding_id)
            continue

        if finding.review_status == "EDITED" and finding.edited_reasoning and finding.edited_reasoning.strip():
            reasoning_text = finding.edited_reasoning.strip()
        else:
            reasoning_text = payload.get("reasoning", finding.edited_reasoning or "Assessment provided in finding.")

        reason_id = finding.reason_id
        assessment = payload.get("assessment", "PARTIAL")

        support_phrase = "appear to support this ground" if assessment in ("SUPPORTED", "VERIFIED") else "do not appear to support this ground"

        paragraph_citations = []
        for ev in evidence_list:
            chunk_id = ev.get("chunk_id", "unknown_chunk")
            quote = ev.get("quote", "")
            page = ev.get("page", 1)
            section_path = ev.get("section_path", "")

            citation = Citation(
                finding_id=finding.finding_id,
                chunk_id=chunk_id,
                quote=quote,
                page=page,
                section_path=section_path,
            )
            paragraph_citations.append(citation)
            all_citations.append(citation)

        citation_marker = f"[{citation_counter}]"
        citation_counter += 1

        paragraph = f"Regarding reason '{reason_id}': {reasoning_text} The cited clauses {support_phrase}. {citation_marker}"
        paragraphs.append(paragraph)
        based_on_ids.append(finding.finding_id)

    footer = "This draft is based on evidence from the uploaded documents and is not legal advice."
    if paragraphs:
        full_text = "\n\n".join(paragraphs) + "\n\n" + footer
    else:
        full_text = footer

    return DraftResult(
        text=full_text,
        citations=all_citations,
        based_on_finding_ids=based_on_ids,
        skipped_finding_ids=skipped_ids,
    )
