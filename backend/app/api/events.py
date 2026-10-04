import asyncio
from datetime import timezone
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.db.session import engine
from backend.app.db.models import Case, PipelineEvent

logger = logging.getLogger("claimlens.events")

router = APIRouter(prefix="/api/cases", tags=["Events"])

POLL_INTERVAL_S: float = 0.5
KEEP_ALIVE_INTERVAL_S: float = 15.0


def _case_exists(case_id: str) -> bool:
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
        return case is not None


def _fetch_new_events(case_id: str, after_seq: int) -> list[dict]:
    with Session(db_session.engine) as session:
        events = session.exec(
            select(PipelineEvent)
            .where(PipelineEvent.case_id == case_id)
            .where(PipelineEvent.seq > after_seq)
            .order_by(PipelineEvent.seq.asc())
        ).all()

        result = []
        for ev in events:
            ts_val = ev.ts
            if ts_val.tzinfo is None:
                ts_iso = ts_val.replace(tzinfo=timezone.utc).isoformat()
            else:
                ts_iso = ts_val.isoformat()

            result.append(
                {
                    "seq": ev.seq,
                    "stage": ev.stage,
                    "status": ev.status,
                    "detail": ev.detail,
                    "ts": ts_iso,
                }
            )
        return result


@router.get("/{case_id}/events")
async def stream_case_events(
    case_id: str,
    request: Request,
    last_event_id: Optional[str] = Query(None),
):
    from backend.app.main import PipelineException

    # Pre-stream check for case existence
    exists = await asyncio.to_thread(_case_exists, case_id)
    if not exists:
        raise PipelineException(
            code="CASE_NOT_FOUND",
            message=f"Case {case_id} not found",
            stage=None,
            status_code=404,
        )

    # Resolve last_event_id: Last-Event-ID header falls back to query param, default 0
    header_last_id = request.headers.get("Last-Event-ID")
    raw_last_id = header_last_id if header_last_id is not None else last_event_id
    try:
        start_seq = int(raw_last_id) if raw_last_id is not None else 0
        if start_seq < 0:
            start_seq = 0
    except (ValueError, TypeError):
        start_seq = 0

    async def event_generator():
        yield "retry: 3000\n\n"
        last_seq = start_seq
        last_keep_alive = time.time()

        while True:
            if await request.is_disconnected():
                logger.info(f"Client disconnected from SSE stream for case {case_id}")
                break

            new_events = await asyncio.to_thread(_fetch_new_events, case_id, last_seq)

            if new_events:
                for ev in new_events:
                    last_seq = ev["seq"]
                    data_json = json.dumps(ev)
                    frame = f"id: {ev['seq']}\nevent: {ev['stage']}\ndata: {data_json}\n\n"
                    yield frame

                    is_terminal = (
                        ev["stage"] == "READY_FOR_REVIEW" and ev["status"] == "COMPLETED"
                    ) or (ev["status"] == "FAILED")
                    if is_terminal:
                        return

                last_keep_alive = time.time()
            else:
                now = time.time()
                if now - last_keep_alive >= KEEP_ALIVE_INTERVAL_S:
                    yield ": keep-alive\n\n"
                    last_keep_alive = now

            await asyncio.sleep(POLL_INTERVAL_S)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )
