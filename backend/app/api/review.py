import json
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.db.models import Case, Finding, AuditLog, PipelineEvent, utc_now
from backend.app.db.session import get_session
from backend.app.services.findings import load_findings

logger = logging.getLogger("claimlens.review")

router = APIRouter(prefix="/api", tags=["Review"])


class ReviewPayload(BaseModel):
    action: str
    note: Optional[str] = None
    edited_reasoning: Optional[str] = None


@router.get("/cases/{case_id}/findings")
async def get_case_findings(
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

    return load_findings(case_id)


from backend.app.services.rate_limiter import rate_limit

@router.post("/findings/{finding_id}/review", dependencies=[Depends(rate_limit("review", lambda s: s.RATE_LIMIT_REVIEW_PER_MIN))])
async def review_finding(
    finding_id: str,
    raw_request: Request,
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    finding = session.exec(select(Finding).where(Finding.finding_id == finding_id)).one_or_none()
    if not finding:
        raise PipelineException(
            code="FINDING_NOT_FOUND",
            message=f"Finding {finding_id} not found",
            stage=None,
            status_code=404,
        )

    # Check case stage & status: MUST be READY_FOR_REVIEW + COMPLETED
    events = session.exec(
        select(PipelineEvent)
        .where(PipelineEvent.case_id == finding.case_id)
        .order_by(PipelineEvent.seq.desc())
    ).all()
    latest_ev = events[0] if events else None

    if not (latest_ev and latest_ev.stage == "READY_FOR_REVIEW" and latest_ev.status == "COMPLETED"):
        raise PipelineException(
            code="WRONG_STAGE",
            message="Review actions are only allowed when case is at READY_FOR_REVIEW with status COMPLETED",
            stage=latest_ev.stage if latest_ev else None,
            status_code=409,
        )

    try:
        body_json = await raw_request.json()
        payload = ReviewPayload.model_validate(body_json)
    except Exception as exc:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message=f"Invalid review request: {exc}",
            stage="READY_FOR_REVIEW",
            status_code=422,
        )

    action = payload.action.upper()
    if action not in ("APPROVE", "REJECT", "EDIT"):
        raise PipelineException(
            code="VALIDATION_ERROR",
            message=f"Invalid action '{payload.action}'. Must be APPROVE, REJECT, or EDIT.",
            stage="READY_FOR_REVIEW",
            status_code=422,
        )

    if payload.note and len(payload.note) > 1000:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="note max length is 1000 characters",
            stage="READY_FOR_REVIEW",
            status_code=422,
        )

    if payload.edited_reasoning and len(payload.edited_reasoning) > 5000:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="edited_reasoning max length is 5000 characters",
            stage="READY_FOR_REVIEW",
            status_code=422,
        )

    if action == "EDIT" and not (payload.edited_reasoning and payload.edited_reasoning.strip()):
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="EDIT action requires a non-empty edited_reasoning",
            stage="READY_FOR_REVIEW",
            status_code=422,
        )

    before_state = {
        "review_status": finding.review_status,
        "edited_reasoning": finding.edited_reasoning,
    }

    if action == "APPROVE":
        finding.review_status = "APPROVED"
    elif action == "REJECT":
        finding.review_status = "REJECTED"
    elif action == "EDIT":
        finding.review_status = "EDITED"
        finding.edited_reasoning = payload.edited_reasoning

    finding.updated_at = utc_now()

    after_state = {
        "review_status": finding.review_status,
        "edited_reasoning": finding.edited_reasoning,
    }

    audit_entry = AuditLog(
        case_id=finding.case_id,
        finding_id=finding.finding_id,
        actor="reviewer",
        action=action,
        before_json=json.dumps(before_state),
        after_json=json.dumps(after_state),
        note=payload.note,
        ts=utc_now(),
    )

    session.add(finding)
    session.add(audit_entry)
    session.commit()
    session.refresh(finding)

    payload_data = json.loads(finding.payload_json) if finding.payload_json else {}
    return {
        "finding_id": finding.finding_id,
        "payload": payload_data,
        "review_status": finding.review_status,
        "edited_reasoning": finding.edited_reasoning,
    }


@router.get("/cases/{case_id}/audit")
async def get_case_audit(
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

    logs = session.exec(
        select(AuditLog)
        .where(AuditLog.case_id == case_id)
        .order_by(AuditLog.id.asc())
    ).all()

    return [
        {
            "id": log.id,
            "case_id": log.case_id,
            "finding_id": log.finding_id,
            "actor": log.actor,
            "action": log.action,
            "before": json.loads(log.before_json) if log.before_json else {},
            "after": json.loads(log.after_json) if log.after_json else {},
            "note": log.note,
            "ts": log.ts.isoformat(),
        }
        for log in logs
    ]


@router.get("/cases/{case_id}/review-summary")
async def get_review_summary(
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

    findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()

    total = len(findings)
    pending = sum(1 for f in findings if f.review_status == "PENDING")
    approved = sum(1 for f in findings if f.review_status == "APPROVED")
    rejected = sum(1 for f in findings if f.review_status == "REJECTED")
    edited = sum(1 for f in findings if f.review_status == "EDITED")
    can_draft = bool(approved > 0 or edited > 0)

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "edited": edited,
        "can_draft": can_draft,
    }
