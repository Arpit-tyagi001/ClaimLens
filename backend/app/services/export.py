import html
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any
from sqlmodel import Session, select

from backend.app.db.models import Case, Document, Finding, AuditLog, Draft, utc_now
from backend.app.services.facts import load_facts

logger = logging.getLogger("claimlens.export")


def generate_export_data(case_id: str, session: Session) -> Dict[str, Any]:
    from backend.app.main import PipelineException

    case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    if not case:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    # Allowed in any stage after READY_FOR_REVIEW (READY_FOR_REVIEW or COMPLETED)
    if case.status not in ("READY_FOR_REVIEW", "COMPLETED"):
        raise PipelineException(
            code="WRONG_STAGE",
            message="Export is only allowed when case is at READY_FOR_REVIEW or COMPLETED stage",
            stage=case.status,
            status_code=409,
        )

    docs = session.exec(select(Document).where(Document.case_id == case_id)).all()
    documents_list = [
        {
            "doc_id": d.doc_id,
            "doc_type": d.doc_type,
            "filename": d.filename or f"{d.doc_id}.pdf",
        }
        for d in docs
    ]

    facts = load_facts(case_id)
    facts_data = facts.model_dump(mode="json") if facts else None

    findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()
    findings_list = []
    for f in findings:
        try:
            payload = json.loads(f.payload_json or "{}")
        except Exception:
            payload = {}

        findings_list.append(
            {
                "finding_id": f.finding_id,
                "reason_id": f.reason_id,
                "assessment": payload.get("assessment", "PARTIAL"),
                "confidence": payload.get("confidence", 0.0),
                "reasoning": payload.get("reasoning", ""),
                "evidence": payload.get("evidence", []),
                "status": payload.get("status", "VERIFIED"),
                "final_assessment": payload.get("final_assessment", payload.get("assessment", "PARTIAL")),
                "final_confidence": payload.get("final_confidence", payload.get("confidence", 0.0)),
                "citation_checks": payload.get("citation_checks", []),
                "challenges": payload.get("challenges", []),
                "review_status": f.review_status,
                "edited_reasoning": f.edited_reasoning,
                "updated_at": f.updated_at.isoformat(),
            }
        )

    audit_rows = session.exec(
        select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.id.asc())
    ).all()
    audit_list = [
        {
            "id": a.id,
            "actor": a.actor,
            "action": a.action,
            "finding_id": a.finding_id,
            "before_json": a.before_json,
            "after_json": a.after_json,
            "note": a.note,
            "ts": a.ts.isoformat(),
        }
        for a in audit_rows
    ]

    latest_draft_row = session.exec(
        select(Draft).where(Draft.case_id == case_id).order_by(Draft.id.desc())
    ).first()

    if latest_draft_row:
        try:
            citations = json.loads(latest_draft_row.citations_json or "[]")
        except Exception:
            citations = []

        draft_data = {
            "draft_id": latest_draft_row.draft_id,
            "text": latest_draft_row.text,
            "citations": citations,
            "created_at": latest_draft_row.created_at.isoformat(),
        }
    else:
        draft_data = None

    now_iso = datetime.now(timezone.utc).isoformat()
    created_at_iso = case.created_at.isoformat() if hasattr(case, "created_at") and case.created_at else now_iso

    return {
        "case_id": case.case_id,
        "created_at": created_at_iso,
        "status": case.status,
        "notice": "Notice: This export is generated from synthetic data and evidence quotes. It is not legal advice.",
        "generated_at": now_iso,
        "documents": documents_list,
        "policy_facts": facts_data,
        "findings": findings_list,
        "audit_log": audit_list,
        "latest_draft": draft_data,
    }


def export_as_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, default=str)


def export_as_html(data: Dict[str, Any]) -> str:
    case_id = html.escape(str(data.get("case_id", "")))
    status = html.escape(str(data.get("status", "")))
    created_at = html.escape(str(data.get("created_at", "")))
    generated_at = html.escape(str(data.get("generated_at", "")))
    notice = html.escape(str(data.get("notice", "")))

    docs_html_items = []
    for d in data.get("documents", []):
        doc_id = html.escape(str(d.get("doc_id", "")))
        doc_type = html.escape(str(d.get("doc_type", "")))
        filename = html.escape(str(d.get("filename", "")))
        docs_html_items.append(f"<li><strong>{doc_id}</strong> ({doc_type}): {filename}</li>")
    docs_html = "".join(docs_html_items)

    findings_html_items = []
    for f in data.get("findings", []):
        fid = html.escape(str(f.get("finding_id", "")))
        rid = html.escape(str(f.get("reason_id", "")))
        st = html.escape(str(f.get("status", "")))
        rev_st = html.escape(str(f.get("review_status", "")))
        reasoning = html.escape(str(f.get("edited_reasoning") or f.get("reasoning") or ""))
        
        evidence_items = []
        for ev in f.get("evidence", []):
            q = html.escape(str(ev.get("quote", "")))
            p = html.escape(str(ev.get("page", 1)))
            sp = html.escape(str(ev.get("section_path", "")))
            evidence_items.append(f"<blockquote>{q} <small>(Page {p}, {sp})</small></blockquote>")
        ev_html = "".join(evidence_items)

        findings_html_items.append(f"""
        <div class="card">
            <h3>Finding {fid} (Reason: {rid})</h3>
            <p><strong>Status:</strong> {st} | <strong>Review Status:</strong> {rev_st}</p>
            <p><strong>Reasoning:</strong> {reasoning}</p>
            <div>{ev_html}</div>
        </div>
        """)
    findings_html = "".join(findings_html_items)

    audit_items = []
    for a in data.get("audit_log", []):
        actor = html.escape(str(a.get("actor", "")))
        action = html.escape(str(a.get("action", "")))
        note = html.escape(str(a.get("note") or ""))
        ts = html.escape(str(a.get("ts", "")))
        audit_items.append(f"<li>[{ts}] <strong>{actor}</strong>: {action} - {note}</li>")
    audit_html = "".join(audit_items)

    draft_info = data.get("latest_draft")
    if draft_info:
        draft_text = html.escape(str(draft_info.get("text", "")))
        draft_html = f"<div class='card'><h3>Latest Draft</h3><pre>{draft_text}</pre></div>"
    else:
        draft_html = "<p>No draft generated.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ClaimLens Export - {case_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; margin: 2rem; color: #333; }}
        h1, h2, h3 {{ color: #111; }}
        .notice {{ background: #fff3cd; border: 1px solid #ffeeba; padding: 1rem; border-radius: 4px; margin-bottom: 2rem; color: #856404; }}
        .card {{ border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; border-radius: 4px; background: #fafafa; }}
        blockquote {{ border-left: 3px solid #007bff; margin: 0.5rem 0; padding-left: 1rem; color: #555; background: #f0f4f8; }}
        pre {{ white-space: pre-wrap; font-family: inherit; }}
    </style>
</head>
<body>
    <h1>ClaimLens Case Packet: {case_id}</h1>
    <div class="notice"><strong>Disclaimer:</strong> {notice}</div>
    <p><strong>Created At:</strong> {created_at} | <strong>Status:</strong> {status} | <strong>Generated At:</strong> {generated_at}</p>

    <h2>Documents</h2>
    <ul>{docs_html}</ul>

    <h2>Findings</h2>
    {findings_html}

    <h2>Audit Trail</h2>
    <ul>{audit_html}</ul>

    <h2>Draft Review Request</h2>
    {draft_html}
</body>
</html>"""
