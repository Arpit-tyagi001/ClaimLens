import json
import logging
from typing import List, Optional
from pydantic import BaseModel
from backend.app.db.models import Finding
from backend.app.config import get_settings
from contracts.schemas import VerifiedFinding

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
    generated_by: str = "template"


def generate_draft(case_id: str, approved_findings: List[Finding]) -> DraftResult:
    settings = get_settings()

    if not settings.MOCK_AI:
        try:
            from backend.app.services.real_stages import _load_ai
            _, _, _, draft_review_request = _load_ai()

            if draft_review_request is not None:
                approved_vfs = []
                for f in approved_findings:
                    try:
                        payload = json.loads(f.payload_json or "{}")
                    except Exception:
                        payload = {}
                    if f.review_status == "EDITED" and f.edited_reasoning and f.edited_reasoning.strip():
                        payload["reasoning"] = f.edited_reasoning.strip()
                    try:
                        vf = VerifiedFinding.model_validate(payload)
                        approved_vfs.append(vf)
                    except Exception:
                        pass

                m3_draft = draft_review_request(case_id=case_id, approved=approved_vfs)

                text = getattr(m3_draft, "text", None)
                if text is None and isinstance(m3_draft, dict):
                    text = m3_draft.get("text", "")

                m3_finding_ids = getattr(m3_draft, "finding_ids", None)
                if m3_finding_ids is None and isinstance(m3_draft, dict):
                    m3_finding_ids = m3_draft.get("finding_ids", [])

                footer = "This draft is based on evidence from the uploaded documents and is not legal advice."
                if text and footer not in text:
                    text = text.rstrip() + "\n\n" + footer

                all_citations: List[Citation] = []
                based_on_ids: List[str] = []
                skipped_ids: List[str] = []

                for finding in approved_findings:
                    try:
                        payload = json.loads(finding.payload_json or "{}")
                    except Exception:
                        payload = {}
                    evidence_list = payload.get("evidence", [])
                    if not evidence_list:
                        skipped_ids.append(finding.finding_id)
                        continue

                    for ev in evidence_list:
                        all_citations.append(
                            Citation(
                                finding_id=finding.finding_id,
                                chunk_id=ev.get("chunk_id", "unknown_chunk"),
                                quote=ev.get("quote", ""),
                                page=ev.get("page", 1),
                                section_path=ev.get("section_path", ""),
                            )
                        )
                    based_on_ids.append(finding.finding_id)

                if m3_finding_ids is not None and len(m3_finding_ids) > 0:
                    based_on_ids = m3_finding_ids
                    skipped_ids = [f.finding_id for f in approved_findings if f.finding_id not in based_on_ids]

                return DraftResult(
                    text=text or footer,
                    citations=all_citations,
                    based_on_finding_ids=based_on_ids,
                    skipped_finding_ids=skipped_ids,
                    generated_by="m3",
                )
        except Exception as exc:
            logger.warning(f"M3 draft generation failed, falling back to template: {exc}")

    return _generate_template_draft(case_id, approved_findings)


def _generate_template_draft(case_id: str, approved_findings: List[Finding]) -> DraftResult:
    paragraphs = []
    all_citations: List[Citation] = []
    based_on_ids: List[str] = []
    skipped_ids: List[str] = []

    citation_counter = 1

    for finding in approved_findings:
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
        generated_by="template",
    )
