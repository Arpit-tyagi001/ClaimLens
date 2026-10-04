import json
import logging
from typing import Optional, List, Dict
from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.db.models import Finding, utc_now
from contracts.schemas import VerifiedFinding

logger = logging.getLogger("claimlens.findings")


def save_findings(case_id: str, findings: List[VerifiedFinding]) -> List[Finding]:
    """
    Save or update VerifiedFinding records for a case idempotently.
    Replaces PENDING findings on re-run, but never overwrites findings
    that already have a review decision (APPROVED / REJECTED / EDITED).
    """
    with Session(db_session.engine) as session:
        existing_findings = session.exec(
            select(Finding).where(Finding.case_id == case_id)
        ).all()
        existing_map: Dict[str, Finding] = {f.finding_id: f for f in existing_findings}

        saved_records: List[Finding] = []

        for vf in findings:
            validated_vf = VerifiedFinding.model_validate(vf)
            fid = validated_vf.finding_id
            payload_str = validated_vf.model_dump_json()

            if fid in existing_map:
                record = existing_map[fid]
                if record.review_status == "PENDING":
                    record.payload_json = payload_str
                    record.reason_id = validated_vf.reason_id
                    record.updated_at = utc_now()
                    session.add(record)
                    saved_records.append(record)
                else:
                    logger.info(f"Skipping update for reviewed finding {fid} ({record.review_status})")
                    saved_records.append(record)
            else:
                record = Finding(
                    finding_id=fid,
                    case_id=case_id,
                    reason_id=validated_vf.reason_id,
                    payload_json=payload_str,
                    review_status="PENDING",
                    edited_reasoning=None,
                    updated_at=utc_now(),
                )
                session.add(record)
                saved_records.append(record)

        session.commit()
        for r in saved_records:
            session.refresh(r)
        return saved_records


def load_findings(case_id: str) -> List[dict]:
    """Load findings for a case, returning validated payload, review_status, and edited_reasoning."""
    with Session(db_session.engine) as session:
        records = session.exec(
            select(Finding)
            .where(Finding.case_id == case_id)
            .order_by(Finding.finding_id.asc())
        ).all()

        results: List[dict] = []
        for r in records:
            payload = json.loads(r.payload_json) if r.payload_json else {}
            results.append(
                {
                    "finding_id": r.finding_id,
                    "payload": payload,
                    "review_status": r.review_status,
                    "edited_reasoning": r.edited_reasoning,
                }
            )
        return results
