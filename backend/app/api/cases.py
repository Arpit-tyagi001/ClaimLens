from fastapi import APIRouter, UploadFile, File, Depends
from sqlmodel import Session
from backend.app.db.session import get_session
from backend.app.db.models import Case, Document, PipelineEvent
import uuid
import shutil
import os

router = APIRouter(prefix="/api/cases", tags=["Cases"])

# Ensure an upload directory exists for the hackathon MVP
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("", status_code=202)
async def upload_case(
    policy: UploadFile = File(...),
    letter: UploadFile = File(...),
    session: Session = Depends(get_session)
):
    # 1. Generate unique IDs
    case_id = f"c_{uuid.uuid4().hex[:8]}"
    policy_doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    letter_doc_id = f"doc_{uuid.uuid4().hex[:8]}"

    # 2. Save files locally
    with open(f"{UPLOAD_DIR}/{policy_doc_id}.pdf", "wb") as buffer:
        shutil.copyfileobj(policy.file, buffer)
    with open(f"{UPLOAD_DIR}/{letter_doc_id}.pdf", "wb") as buffer:
        shutil.copyfileobj(letter.file, buffer)

    # 3. Create Database Records
    new_case = Case(case_id=case_id, status="UPLOADED")
    policy_doc = Document(doc_id=policy_doc_id, case_id=case_id, doc_type="policy")
    letter_doc = Document(doc_id=letter_doc_id, case_id=case_id, doc_type="letter")
    
    event = PipelineEvent(
        case_id=case_id, 
        seq=1, 
        stage="UPLOAD", 
        status="COMPLETED", 
        detail="Policy and rejection letter received."
    )

    # Save to SQLite
    session.add(new_case)
    session.add(policy_doc)
    session.add(letter_doc)
    session.add(event)
    session.commit()

    # 4. Return the exact 202 response the PRD asks for
    return {"case_id": case_id, "status": "UPLOADED"}