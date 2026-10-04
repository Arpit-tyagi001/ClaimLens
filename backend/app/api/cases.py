import asyncio
import os
import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlmodel import Session, select

from backend.app.db.session import get_session
from backend.app.db.models import Case, Document, PipelineEvent
from backend.app.services.pipeline import start_pipeline, resume_pipeline, get_next_seq

router = APIRouter(prefix="/api/cases", tags=["Cases"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _save_case_to_db(
    case_id: str,
    policy_doc_id: str,
    letter_doc_id: str,
    policy_content: bytes,
    letter_content: bytes,
    session: Session,
) -> Case:
    with open(f"{UPLOAD_DIR}/{policy_doc_id}.pdf", "wb") as buffer:
        buffer.write(policy_content)
    with open(f"{UPLOAD_DIR}/{letter_doc_id}.pdf", "wb") as buffer:
        buffer.write(letter_content)

    new_case = Case(case_id=case_id, status="UPLOADED")
    policy_doc = Document(doc_id=policy_doc_id, case_id=case_id, doc_type="policy")
    letter_doc = Document(doc_id=letter_doc_id, case_id=case_id, doc_type="letter")

    seq = get_next_seq(session, case_id)
    event = PipelineEvent(
        case_id=case_id,
        seq=seq,
        stage="UPLOADED",
        status="COMPLETED",
        detail="Policy and rejection letter received.",
    )

    session.add(new_case)
    session.add(policy_doc)
    session.add(letter_doc)
    session.add(event)
    session.commit()
    return new_case


@router.post("", status_code=202)
async def upload_case(
    policy: UploadFile = File(...),
    letter: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    case_id = f"c_{uuid.uuid4().hex[:8]}"
    policy_doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    letter_doc_id = f"doc_{uuid.uuid4().hex[:8]}"

    policy_content = await policy.read()
    letter_content = await letter.read()

    await asyncio.to_thread(
        _save_case_to_db,
        case_id,
        policy_doc_id,
        letter_doc_id,
        policy_content,
        letter_content,
        session,
    )

    start_pipeline(case_id)
    return {"case_id": case_id, "status": "UPLOADED"}


@router.post("/{case_id}/resume", status_code=202)
async def resume_case(
    case_id: str,
    session: Session = Depends(get_session),
):
    case = await asyncio.to_thread(
        lambda: session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    resume_pipeline(case_id)
    return {"case_id": case_id, "status": case.status}