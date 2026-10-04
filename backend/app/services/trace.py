import logging
from typing import Dict, List, Any
from sqlmodel import Session, select
import backend.app.db.session as db_session
from backend.app.db.models import Case, PipelineEvent, StageMetric
from backend.app.services.pipeline import PIPELINE_ORDER, Stage

logger = logging.getLogger("claimlens.trace")


def get_case_trace(case_id: str, session: Session) -> Dict[str, Any]:
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
        .order_by(PipelineEvent.seq.asc())
    ).all()

    metrics = session.exec(
        select(StageMetric).where(StageMetric.case_id == case_id)
    ).all()
    metrics_by_stage: Dict[str, StageMetric] = {m.stage: m for m in metrics}

    events_by_stage: Dict[str, List[PipelineEvent]] = {}
    for ev in events:
        events_by_stage.setdefault(ev.stage, []).append(ev)

    stages_result = []

    for stage_enum in PIPELINE_ORDER:
        stage_name = stage_enum.value
        stage_events = events_by_stage.get(stage_name, [])

        if not stage_events:
            continue

        first_ev = stage_events[0]
        last_ev = stage_events[-1]

        started_at = first_ev.ts.isoformat()
        finished_at = last_ev.ts.isoformat()
        duration_ms = max(0, int((last_ev.ts - first_ev.ts).total_seconds() * 1000))

        attempts = sum(1 for ev in stage_events if ev.status == "RUNNING")
        retries = sum(1 for ev in stage_events if ev.status == "RETRYING")
        used_fallback = any(ev.status == "FALLBACK" for ev in stage_events)
        final_status = last_ev.status

        metric = metrics_by_stage.get(stage_name)
        model = metric.model if metric else None
        tokens_in = metric.tokens_in if metric else None
        tokens_out = metric.tokens_out if metric else None

        stages_result.append(
            {
                "stage": stage_name,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "attempts": attempts,
                "retries": retries,
                "used_fallback": used_fallback,
                "final_status": final_status,
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
        )

    if events:
        total_duration_ms = max(0, int((events[-1].ts - events[0].ts).total_seconds() * 1000))
    else:
        total_duration_ms = 0

    return {
        "case_id": case_id,
        "total_duration_ms": total_duration_ms,
        "stages": stages_result,
    }
