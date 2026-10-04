import os
import logging
from sqlmodel import Session, select
from backend.app.db.models import Case, Document, Chunk, Finding, PipelineEvent, AuditLog
from backend.app.config import get_settings

logger = logging.getLogger("claimlens.delete_case")


def delete_case_data(case_id: str, session: Session) -> None:
    """
    Deletes all case records and associated PDF files from disk.
    Appends a 'CASE_DELETED' entry to audit_log to preserve append-only trigger guarantee.
    """
    from backend.app.main import PipelineException
    from backend.app.services.pipeline import _running

    case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
    if not case:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    # Refuse deletion if pipeline is actively running for this case
    if case_id in _running:
        raise PipelineException(
            code="CASE_BUSY",
            message=f"Case {case_id} is currently running in pipeline",
            stage=None,
            status_code=409,
        )

    # 1. Collect document IDs for file removal & chunk cleanup
    docs = session.exec(select(Document).where(Document.case_id == case_id)).all()
    doc_ids = [d.doc_id for d in docs]

    # 2. Append final CASE_DELETED audit row before row deletions
    audit_entry = AuditLog(
        case_id=case_id,
        finding_id=None,
        actor="system",
        action="CASE_DELETED",
        before_json="{}",
        after_json="{}",
        note=f"Case {case_id} deleted via API",
    )
    session.add(audit_entry)

    # 3. Delete related database rows (chunks, documents, findings, events, case)
    for doc_id in doc_ids:
        chunks = session.exec(select(Chunk).where(Chunk.doc_id == doc_id)).all()
        for chunk in chunks:
            session.delete(chunk)

    for doc in docs:
        session.delete(doc)

    findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()
    for finding in findings:
        session.delete(finding)

    events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == case_id)).all()
    for ev in events:
        session.delete(ev)

    session.delete(case)
    session.commit()

    # 4. Remove PDF files from uploads directory (with path traversal verification)
    upload_dir = get_settings().UPLOAD_DIR
    uploads_dir_abs = os.path.abspath(upload_dir)

    for doc_id in doc_ids:
        target_path = os.path.abspath(os.path.join(uploads_dir_abs, f"{doc_id}.pdf"))
        if target_path.startswith(uploads_dir_abs + os.sep) or target_path == uploads_dir_abs:
            if os.path.isfile(target_path):
                try:
                    os.remove(target_path)
                except Exception as exc:
                    logger.warning(f"Failed to remove file {target_path}: {exc}")
