import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlmodel import Session, select
import backend.app.db.session as db_session
from backend.app.db.models import Case
from backend.app.config import get_settings
from backend.app.services.delete_case import delete_case_data

logger = logging.getLogger("claimlens.purge")


def purge_expired_cases(now: Optional[datetime] = None) -> int:
    """
    Finds cases created older than RETENTION_HOURS and deletes them.
    Returns the number of cases purged.
    """
    settings = get_settings()
    now_time = now or datetime.now(timezone.utc)
    cutoff = now_time - timedelta(hours=settings.RETENTION_HOURS)

    purged_count = 0

    with Session(db_session.engine) as session:
        expired_cases = session.exec(select(Case).where(Case.created_at < cutoff)).all()
        case_ids = [c.case_id for c in expired_cases]

    for case_id in case_ids:
        try:
            with Session(db_session.engine) as session:
                delete_case_data(case_id, session)
                purged_count += 1
        except Exception as exc:
            logger.error(f"Failed to purge case {case_id}: {exc}", exc_info=exc)

    return purged_count
