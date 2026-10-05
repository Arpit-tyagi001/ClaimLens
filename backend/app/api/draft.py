import json
import uuid
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session, select

from backend.app.db.session import get_session
from backend.app.db.models import Case, Finding, Draft, AuditLog, PipelineEvent
from backend.app.services.draft import generate_draft
from backend.app.services.export import generate_export_data, export_as_json, export_as_html
from backend.app.services.trace import get_case_trace

logger = logging.getLogger("claimlens.api.draft")

router = APIRouter(prefix="/api/cases", tags=["Draft & Export"])


@router.post("/{case_id}/draft")
async def create_draft(
    case_id: str,
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    if not case:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    # Check case stage: MUST be READY_FOR_REVIEW + COMPLETED
    events = session.exec(
        select(PipelineEvent)
        .where(PipelineEvent.case_id == case_id)
        .order_by(PipelineEvent.seq.desc())
    ).all()
    latest_ev = events[0] if events else None

    if not (latest_ev and latest_ev.stage == "READY_FOR_REVIEW" and latest_ev.status == "COMPLETED") and case.status not in ("READY_FOR_REVIEW", "COMPLETED"):
        raise PipelineException(
            code="WRONG_STAGE",
            message="Draft generation is only allowed when case is at READY_FOR_REVIEW stage with status COMPLETED",
            stage=latest_ev.stage if latest_ev else case.status,
            status_code=409,
        )

    # Query approved/edited findings
    approved_findings = session.exec(
        select(Finding)
        .where(Finding.case_id == case_id)
        .where(Finding.review_status.in_(["APPROVED", "EDITED"]))
    ).all()

    if not approved_findings:
        raise PipelineException(
            code="NO_APPROVED_FINDINGS",
            message="No approved or edited findings available to generate a draft",
            stage="READY_FOR_REVIEW",
            status_code=409,
        )

    # Generate draft
    draft_res = generate_draft(case_id, approved_findings)
    draft_id = f"draft_{uuid.uuid4().hex[:8]}"
    draft_res.draft_id = draft_id

    citations_list = [c.model_dump() for c in draft_res.citations]

    # Save Draft row & write AuditLog row in one transaction
    draft_row = Draft(
        draft_id=draft_id,
        case_id=case_id,
        text=draft_res.text,
        citations_json=json.dumps(citations_list),
        based_on_json=json.dumps(draft_res.based_on_finding_ids),
        skipped_finding_ids_json=json.dumps(draft_res.skipped_finding_ids),
    )

    audit_entry = AuditLog(
        case_id=case_id,
        finding_id=None,
        actor="reviewer",
        action="DRAFT_GENERATED",
        before_json="{}",
        after_json=json.dumps({"draft_id": draft_id, "finding_ids": draft_res.based_on_finding_ids}),
        note=f"Draft generated from {len(approved_findings)} approved findings",
    )

    session.add(draft_row)
    session.add(audit_entry)
    session.commit()

    return {
        "draft_id": draft_id,
        "text": draft_res.text,
        "citations": citations_list,
        "skipped_finding_ids": draft_res.skipped_finding_ids,
        "generated_by": getattr(draft_res, "generated_by", "template"),
    }



@router.get("/{case_id}/draft/latest")
async def get_latest_draft(
    case_id: str,
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    if not case:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    latest_draft = session.exec(
        select(Draft).where(Draft.case_id == case_id).order_by(Draft.id.desc())
    ).first()

    if not latest_draft:
        raise PipelineException(
            code="DRAFT_NOT_FOUND",
            message=f"No draft found for case {case_id}",
            stage=None,
            status_code=404,
        )

    try:
        citations = json.loads(latest_draft.citations_json or "[]")
    except Exception:
        citations = []

    try:
        skipped = json.loads(latest_draft.skipped_finding_ids_json or "[]")
    except Exception:
        skipped = []

    return {
        "draft_id": latest_draft.draft_id,
        "text": latest_draft.text,
        "citations": citations,
        "skipped_finding_ids": skipped,
        "created_at": latest_draft.created_at.isoformat(),
    }


@router.get("/{case_id}/export")
async def export_case_packet(
    case_id: str,
    format: str = Query("json"),
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    fmt = format.lower()
    if fmt not in ("json", "html"):
        raise PipelineException(
            code="VALIDATION_ERROR",
            message=f"Invalid export format '{format}'. Supported formats: json, html",
            stage=None,
            status_code=422,
        )

    export_data = generate_export_data(case_id, session)

    if fmt == "json":
        content = export_as_json(export_data)
        headers = {"Content-Disposition": f'attachment; filename="claimlens-{case_id}.json"'}
        return Response(content=content, media_type="application/json", headers=headers)
    else:
        content = export_as_html(export_data)
        headers = {"Content-Disposition": f'attachment; filename="claimlens-{case_id}.html"'}
        return Response(content=content, media_type="text/html", headers=headers)


@router.get("/{case_id}/trace")
async def get_trace(
    case_id: str,
    session: Session = Depends(get_session),
):
    return get_case_trace(case_id, session)
