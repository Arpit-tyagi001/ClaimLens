import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.db.models import Case, Document, PipelineEvent
from backend.app.db.session import get_session
from backend.app.services.facts import load_facts, save_facts
from backend.app.services.pipeline import resume_pipeline
from contracts.schemas import PolicyFacts

router = APIRouter(prefix="/api/cases", tags=["Facts"])


@router.get("/{case_id}/policy-facts")
async def get_policy_facts(
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

    facts = load_facts(case_id)
    if facts is None:
        raise PipelineException(
            code="FACTS_NOT_READY",
            message=f"Policy facts not ready for case {case_id}",
            stage=None,
            status_code=409,
        )

    return facts


@router.put("/{case_id}/policy-facts")
async def update_policy_facts(
    case_id: str,
    raw_request: Request,
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

    events = session.exec(
        select(PipelineEvent)
        .where(PipelineEvent.case_id == case_id)
        .order_by(PipelineEvent.seq.desc())
    ).all()
    latest_event = events[0] if events else None

    if not (latest_event and latest_event.stage == "AWAITING_FACTS" and latest_event.status == "WAITING"):
        raise PipelineException(
            code="WRONG_STAGE",
            message="Policy facts can only be updated when case is in AWAITING_FACTS stage with status WAITING",
            stage=latest_event.stage if latest_event else None,
            status_code=409,
        )

    try:
        body_json = await raw_request.json()
        facts = PolicyFacts.model_validate(body_json)
    except Exception as exc:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message=f"Invalid PolicyFacts payload: {exc}",
            stage="AWAITING_FACTS",
            status_code=422,
        )

    # Custom validation rules
    if facts.policy_start and facts.policy_end and facts.policy_end <= facts.policy_start:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="policy_end must be after policy_start",
            stage="AWAITING_FACTS",
            status_code=422,
        )

    if facts.sum_insured is not None and facts.sum_insured < 0:
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="sum_insured must be >= 0",
            stage="AWAITING_FACTS",
            status_code=422,
        )

    if any(wp.months < 0 for wp in facts.waiting_periods):
        raise PipelineException(
            code="VALIDATION_ERROR",
            message="waiting period months must be >= 0",
            stage="AWAITING_FACTS",
            status_code=422,
        )

    saved_facts = save_facts(case_id, facts, confirmed=True)
    resume_pipeline(case_id)
    return saved_facts


@router.get("/{case_id}")
async def get_case_detail(
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

    docs = session.exec(select(Document).where(Document.case_id == case_id)).all()
    documents_list = [
        {
            "doc_id": d.doc_id,
            "doc_type": d.doc_type,
            "file_url": f"/api/cases/{case_id}/documents/{d.doc_id}/file",
        }
        for d in docs
    ]

    events = session.exec(
        select(PipelineEvent)
        .where(PipelineEvent.case_id == case_id)
        .order_by(PipelineEvent.seq.desc())
    ).all()

    if events:
        latest_stage = events[0].stage
        last_seq = events[0].seq
    else:
        latest_stage = case.status
        last_seq = 0

    from backend.app.config import get_case_mode

    return {
        "case_id": case.case_id,
        "status": case.status,
        "stage": latest_stage,
        "facts_confirmed": case.facts_confirmed,
        "documents": documents_list,
        "last_seq": last_seq,
        "mode": get_case_mode(),
    }
