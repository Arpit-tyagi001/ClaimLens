import asyncio
import os
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Depends
from fastapi.responses import FileResponse
from sqlmodel import Session, select

import backend.app.db.session as db_session
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
    policy_filename: Optional[str],
    letter_filename: Optional[str],
    session: Session,
) -> Case:
    uploads_dir_abs = os.path.abspath(UPLOAD_DIR)
    os.makedirs(uploads_dir_abs, exist_ok=True)

    with open(os.path.join(uploads_dir_abs, f"{policy_doc_id}.pdf"), "wb") as buffer:
        buffer.write(policy_content)
    with open(os.path.join(uploads_dir_abs, f"{letter_doc_id}.pdf"), "wb") as buffer:
        buffer.write(letter_content)

    new_case = Case(case_id=case_id, status="UPLOADED")
    policy_doc = Document(
        doc_id=policy_doc_id,
        case_id=case_id,
        doc_type="policy",
        filename=policy_filename,
    )
    letter_doc = Document(
        doc_id=letter_doc_id,
        case_id=case_id,
        doc_type="letter",
        filename=letter_filename,
    )

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
    policy: Optional[UploadFile] = File(None),
    letter: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException
    from backend.app.services.upload_validation import validate_pdf, MAX_UPLOAD_BYTES

    # Validate missing file parts
    if policy is None or not policy.filename:
        raise PipelineException(
            code="MISSING_FILE",
            message="The policy file is required.",
            stage="UPLOADED",
            status_code=422,
        )
    if letter is None or not letter.filename:
        raise PipelineException(
            code="MISSING_FILE",
            message="The letter file is required.",
            stage="UPLOADED",
            status_code=422,
        )

    # Read content with MAX_UPLOAD_BYTES + 1 cap
    policy_bytes = await policy.read(MAX_UPLOAD_BYTES + 1)
    letter_bytes = await letter.read(MAX_UPLOAD_BYTES + 1)

    # Validate BOTH files before saving anything to disk or DB
    validate_pdf(policy.filename, policy.content_type, policy_bytes, label="policy")
    validate_pdf(letter.filename, letter.content_type, letter_bytes, label="letter")

    case_id = f"c_{uuid.uuid4().hex[:8]}"
    policy_doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    letter_doc_id = f"doc_{uuid.uuid4().hex[:8]}"

    clean_policy_filename = os.path.basename(policy.filename) if policy.filename else None
    clean_letter_filename = os.path.basename(letter.filename) if letter.filename else None

    await asyncio.to_thread(
        _save_case_to_db,
        case_id,
        policy_doc_id,
        letter_doc_id,
        policy_bytes,
        letter_bytes,
        clean_policy_filename,
        clean_letter_filename,
        session,
    )

    start_pipeline(case_id)
    return {"case_id": case_id, "status": "UPLOADED"}


@router.post("/{case_id}/resume", status_code=202)
async def resume_case(
    case_id: str,
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    case = await asyncio.to_thread(
        lambda: session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    )
    if not case:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    resume_pipeline(case_id)
    return {"case_id": case_id, "status": case.status}


@router.get("/{case_id}/documents/{doc_id}/file")
async def serve_document_file(
    case_id: str,
    doc_id: str,
    session: Session = Depends(get_session),
):
    from backend.app.main import PipelineException

    # 1. Look up Document row verifying case_id ownership
    doc = session.exec(
        select(Document)
        .where(Document.doc_id == doc_id)
        .where(Document.case_id == case_id)
    ).one_or_none()

    if not doc:
        raise PipelineException(
            code="DOCUMENT_NOT_FOUND",
            message=f"Document {doc_id} not found for case {case_id}",
            stage=None,
            status_code=404,
        )

    # 2. Resolve path & path traversal guard
    uploads_dir_abs = os.path.abspath(UPLOAD_DIR)
    target_path = os.path.abspath(os.path.join(uploads_dir_abs, f"{doc_id}.pdf"))

    # Verify target_path is inside uploads_dir_abs
    if not target_path.startswith(uploads_dir_abs + os.sep) and target_path != uploads_dir_abs:
        raise PipelineException(
            code="DOCUMENT_NOT_FOUND",
            message=f"Document {doc_id} not found",
            stage=None,
            status_code=404,
        )

    if not os.path.isfile(target_path):
        raise PipelineException(
            code="FILE_MISSING",
            message=f"File for document {doc_id} is missing on disk",
            stage=None,
            status_code=404,
        )

    # 3. Return FileResponse
    headers = {
        "Content-Disposition": f'inline; filename="{doc_id}.pdf"',
        "Cache-Control": "private, max-age=3600",
    }
    return FileResponse(
        path=target_path,
        media_type="application/pdf",
        headers=headers,
    )