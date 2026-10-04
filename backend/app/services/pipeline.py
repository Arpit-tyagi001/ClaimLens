import asyncio
from dataclasses import dataclass
from enum import Enum
import inspect
import json
import logging
import os
import random
from typing import Awaitable, Callable, Optional, Set, Dict, Any

from sqlmodel import Session, select, func

import backend.app.db.session as db_session
from backend.app.db.session import engine
from backend.app.db.models import Case, PipelineEvent

logger = logging.getLogger("claimlens.pipeline")


class Stage(str, Enum):
    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    AWAITING_FACTS = "AWAITING_FACTS"
    INVESTIGATING = "INVESTIGATING"
    VERIFYING = "VERIFYING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"


PIPELINE_ORDER: list[Stage] = [
    Stage.UPLOADED,
    Stage.EXTRACTING,
    Stage.AWAITING_FACTS,
    Stage.INVESTIGATING,
    Stage.VERIFYING,
    Stage.READY_FOR_REVIEW,
]


class StageStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    FALLBACK = "FALLBACK"
    WAITING = "WAITING"


@dataclass
class StageContext:
    case_id: str
    emit: Callable[..., None]


StageFn = Callable[[StageContext], None | Awaitable[None]]


@dataclass
class StageSpec:
    fn: StageFn
    timeout_s: float
    max_retries: int
    backoff_base_s: float
    fallback: StageFn | None = None


# Failure injection settings for dev / debugging
FAILURE_INJECTION: Dict[Stage, str] = {}
FAILURE_ATTEMPTS: Dict[Stage, int] = {}

# Concurrency guard
_running: Set[str] = set()
_running_lock = asyncio.Lock()

# Strong references for background tasks
_background_tasks: Set[asyncio.Task] = set()

# Registry global reference
STAGE_REGISTRY: Dict[Stage, StageSpec] = {}


def get_next_seq(session: Session, case_id: str) -> int:
    """Compute the next strictly monotonic sequence number for a case_id within a transaction."""
    max_seq = session.exec(
        select(func.max(PipelineEvent.seq)).where(PipelineEvent.case_id == case_id)
    ).one_or_none()
    return (max_seq or 0) + 1


def emit_event(
    case_id: str,
    stage: Stage | str,
    status: StageStatus | str,
    detail: str = "",
) -> PipelineEvent:
    stage_str = stage.value if isinstance(stage, Stage) else str(stage)
    status_str = status.value if isinstance(status, StageStatus) else str(status)
    clean_detail = str(detail)[:500]

    with Session(db_session.engine) as session:
        seq = get_next_seq(session, case_id)

        event = PipelineEvent(
            case_id=case_id,
            seq=seq,
            stage=stage_str,
            status=status_str,
            detail=clean_detail,
        )
        session.add(event)

        if status_str in (
            StageStatus.RUNNING.value,
            StageStatus.WAITING.value,
            StageStatus.COMPLETED.value,
        ):
            case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
            if case:
                case.status = stage_str
                session.add(case)
        elif status_str == StageStatus.FAILED.value:
            case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
            if case:
                case.status = "FAILED"
                session.add(case)

        session.commit()
        session.refresh(event)
        return event


def facts_confirmed(case_id: str) -> bool:
    """Check if policy facts have been confirmed for the case."""
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
        if case and hasattr(case, "facts_confirmed"):
            return bool(case.facts_confirmed)
    return False


def determine_resume_stage(case_id: str) -> Stage | None:
    """Read the latest event row(s) for the case to determine next stage."""
    with Session(db_session.engine) as session:
        events = session.exec(
            select(PipelineEvent)
            .where(PipelineEvent.case_id == case_id)
            .order_by(PipelineEvent.seq.desc())
        ).all()

    if not events:
        return Stage.EXTRACTING

    last_event = events[0]
    stage_raw = last_event.stage
    status_raw = last_event.status

    if stage_raw in ("UPLOAD", "UPLOADED"):
        current_stage = Stage.UPLOADED
    else:
        try:
            current_stage = Stage(stage_raw)
        except ValueError:
            return Stage.EXTRACTING

    if current_stage == Stage.READY_FOR_REVIEW and status_raw == StageStatus.COMPLETED.value:
        return None

    if current_stage == Stage.AWAITING_FACTS and status_raw == StageStatus.WAITING.value:
        if facts_confirmed(case_id):
            return Stage.AWAITING_FACTS
        else:
            return None

    if status_raw in (
        StageStatus.RUNNING.value,
        StageStatus.RETRYING.value,
        StageStatus.FALLBACK.value,
        StageStatus.FAILED.value,
    ):
        return current_stage

    if status_raw == StageStatus.COMPLETED.value:
        try:
            idx = PIPELINE_ORDER.index(current_stage)
            if idx + 1 < len(PIPELINE_ORDER):
                return PIPELINE_ORDER[idx + 1]
            return None
        except ValueError:
            return Stage.EXTRACTING

    return current_stage


def build_default_registry() -> Dict[Stage, StageSpec]:
    mock_docs = os.getenv("MOCK_DOCS", "true").lower() in ("true", "1")
    mock_ai = os.getenv("MOCK_AI", "true").lower() in ("true", "1")

    from backend.app.services import mock_stages

    extracting_fn = (
        mock_stages.mock_extracting_stage
        if mock_docs
        else mock_stages.mock_extracting_stage  # TODO(wire): replace with real docs import
    )
    investigating_fn = (
        mock_stages.mock_investigating_stage
        if mock_ai
        else mock_stages.mock_investigating_stage  # TODO(wire): replace with real ai import
    )
    verifying_fn = (
        mock_stages.mock_verifying_stage
        if mock_ai
        else mock_stages.mock_verifying_stage  # TODO(wire): replace with real ai import
    )

    registry: Dict[Stage, StageSpec] = {
        Stage.EXTRACTING: StageSpec(
            fn=extracting_fn,
            timeout_s=60.0,
            max_retries=2,
            backoff_base_s=2.0,
        ),
        Stage.INVESTIGATING: StageSpec(
            fn=investigating_fn,
            timeout_s=120.0,
            max_retries=2,
            backoff_base_s=2.0,
            fallback=mock_stages.mock_investigating_fallback,
        ),
        Stage.VERIFYING: StageSpec(
            fn=verifying_fn,
            timeout_s=120.0,
            max_retries=2,
            backoff_base_s=2.0,
        ),
    }
    return registry


async def _execute_stage(case_id: str, stage: Stage, spec: StageSpec) -> None:
    """
    STAGE IDEMPOTENCY CONTRACT:
    Stage functions must be idempotent. The runner may execute a stage function multiple times
    due to retries, crash recovery, or explicit resume calls. Stage functions must handle being
    re-executed without duplicating side-effects or corrupting state.
    """
    from backend.app.main import PipelineException

    def _emit_progress(status_or_detail: Any, detail: Any = None) -> None:
        if detail is not None:
            st = status_or_detail if isinstance(status_or_detail, (str, StageStatus)) else StageStatus.RUNNING
            dt = json.dumps(detail) if isinstance(detail, dict) else str(detail)
        else:
            st = StageStatus.RUNNING
            dt = json.dumps(status_or_detail) if isinstance(status_or_detail, dict) else str(status_or_detail)
        emit_event(case_id, stage, st, dt)

    ctx = StageContext(case_id=case_id, emit=_emit_progress)
    total_attempts = spec.max_retries + 1
    last_exc: Optional[Exception] = None

    for attempt in range(1, total_attempts + 1):
        emit_event(
            case_id,
            stage,
            StageStatus.RUNNING,
            f"Executing stage {stage.value} (attempt {attempt}/{total_attempts})",
        )
        try:
            if inspect.iscoroutinefunction(spec.fn):
                await asyncio.wait_for(spec.fn(ctx), timeout=spec.timeout_s)
            else:
                await asyncio.wait_for(asyncio.to_thread(spec.fn, ctx), timeout=spec.timeout_s)

            emit_event(case_id, stage, StageStatus.COMPLETED, f"Stage {stage.value} completed")
            return
        except Exception as exc:
            last_exc = exc
            logger.exception(f"Stage {stage.value} failed attempt {attempt}/{total_attempts}")

            if isinstance(exc, PipelineException) and getattr(exc, "retryable", True) is False:
                break

            if attempt < total_attempts:
                backoff = spec.backoff_base_s * (2 ** (attempt - 1))
                jitter = random.uniform(0, 0.1 * backoff)
                sleep_s = backoff + jitter
                emit_event(
                    case_id,
                    stage,
                    StageStatus.RETRYING,
                    f"attempt {attempt}/{total_attempts}, retrying in {sleep_s:.2f}s",
                )
                await asyncio.sleep(sleep_s)

    # Retries exhausted: try fallback if present
    if spec.fallback is not None:
        emit_event(case_id, stage, StageStatus.FALLBACK, f"Retries exhausted for {stage.value}, running fallback")
        try:
            if inspect.iscoroutinefunction(spec.fallback):
                await asyncio.wait_for(spec.fallback(ctx), timeout=spec.timeout_s)
            else:
                await asyncio.wait_for(asyncio.to_thread(spec.fallback, ctx), timeout=spec.timeout_s)

            emit_event(case_id, stage, StageStatus.COMPLETED, "completed via fallback")
            return
        except Exception as fb_exc:
            logger.exception(f"Fallback failed for stage {stage.value}")
            last_exc = fb_exc

    # Terminal failure
    is_timeout = isinstance(last_exc, TimeoutError) or isinstance(last_exc, asyncio.TimeoutError)
    err_code = "STAGE_TIMEOUT" if is_timeout else "STAGE_FAILED"
    err_msg = f"{stage.value} timed out" if is_timeout else f"{stage.value} failed"
    detail_msg = f"{stage.value} timed out" if is_timeout else f"Stage {stage.value} failed"

    emit_event(case_id, stage, StageStatus.FAILED, detail_msg)
    raise PipelineException(code=err_code, message=err_msg, stage=stage.value)


async def run_pipeline(case_id: str, start_from: Stage | None = None) -> None:
    """
    Orchestrate pipeline execution for a given case.
    
    STAGE IDEMPOTENCY CONTRACT:
    Stage functions must be idempotent. The runner may call the same stage more than once.
    """
    async with _running_lock:
        if case_id in _running:
            logger.info(f"Pipeline already running for case {case_id}, skipping duplicate start.")
            return
        _running.add(case_id)

    try:
        if start_from is None:
            start_from = determine_resume_stage(case_id)
        if start_from is None:
            logger.info(f"No stage to run for case {case_id}.")
            return

        start_idx = PIPELINE_ORDER.index(start_from)
        registry = STAGE_REGISTRY if STAGE_REGISTRY else build_default_registry()

        for stage in PIPELINE_ORDER[start_idx:]:
            if stage == Stage.UPLOADED:
                continue

            if stage == Stage.AWAITING_FACTS:
                if not facts_confirmed(case_id):
                    emit_event(
                        case_id,
                        stage,
                        StageStatus.WAITING,
                        "Waiting for user to confirm policy facts",
                    )
                    return
                else:
                    emit_event(
                        case_id,
                        stage,
                        StageStatus.COMPLETED,
                        "Policy facts confirmed",
                    )
                    continue

            if stage == Stage.READY_FOR_REVIEW:
                emit_event(
                    case_id,
                    stage,
                    StageStatus.COMPLETED,
                    "Case packet ready for review",
                )
                return

            spec = registry.get(stage)
            if not spec:
                continue

            await _execute_stage(case_id, stage, spec)
    finally:
        async with _running_lock:
            _running.discard(case_id)


def start_pipeline(case_id: str, start_from: Stage | None = None) -> asyncio.Task:
    from backend.app.main import PipelineException

    task = asyncio.create_task(run_pipeline(case_id, start_from=start_from))
    _background_tasks.add(task)

    def _task_done_cb(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception():
            exc = t.exception()
            if isinstance(exc, PipelineException):
                logger.info(f"Pipeline task for case {case_id} completed with exception: {exc.message}")
            else:
                logger.error(
                    f"Unexpected exception in background pipeline task for case {case_id}: {exc}",
                    exc_info=exc,
                )

    task.add_done_callback(_task_done_cb)
    return task


def resume_pipeline(case_id: str) -> asyncio.Task:
    return start_pipeline(case_id, start_from=None)


def recover_incomplete_cases() -> list[str]:
    """Find cases whose last event is RUNNING/RETRYING/FALLBACK and resume each."""
    with Session(db_session.engine) as session:
        events = session.exec(
            select(PipelineEvent).order_by(PipelineEvent.case_id, PipelineEvent.seq.desc())
        ).all()

        latest_per_case: Dict[str, PipelineEvent] = {}
        for ev in events:
            if ev.case_id not in latest_per_case:
                latest_per_case[ev.case_id] = ev

        recovered: list[str] = []
        for case_id, ev in latest_per_case.items():
            if ev.status in (
                StageStatus.RUNNING.value,
                StageStatus.RETRYING.value,
                StageStatus.FALLBACK.value,
            ):
                logger.info(f"Recovering incomplete case {case_id} from stage {ev.stage}")
                resume_pipeline(case_id)
                recovered.append(case_id)
        return recovered
