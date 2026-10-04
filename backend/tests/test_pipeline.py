import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
from backend.app.services.pipeline import (
    Stage,
    StageStatus,
    StageSpec,
    StageContext,
    emit_event,
    determine_resume_stage,
    run_pipeline,
    start_pipeline,
    resume_pipeline,
    facts_confirmed,
    FAILURE_INJECTION,
    FAILURE_ATTEMPTS,
)
from backend.app.db.models import Case, PipelineEvent
from backend.app.main import app, PipelineException
from backend.app.services import mock_stages


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_claimlens.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    orig_engine = db_session.engine
    orig_pipe_engine = pipeline_module.engine

    db_session.engine = test_engine
    pipeline_module.engine = test_engine

    SQLModel.metadata.create_all(test_engine)

    FAILURE_INJECTION.clear()
    FAILURE_ATTEMPTS.clear()

    fast_registry = {
        Stage.EXTRACTING: StageSpec(
            fn=mock_stages.mock_extracting_stage,
            timeout_s=2.0,
            max_retries=1,
            backoff_base_s=0.01,
        ),
        Stage.INVESTIGATING: StageSpec(
            fn=mock_stages.mock_investigating_stage,
            timeout_s=2.0,
            max_retries=1,
            backoff_base_s=0.01,
            fallback=mock_stages.mock_investigating_fallback,
        ),
        Stage.VERIFYING: StageSpec(
            fn=mock_stages.mock_verifying_stage,
            timeout_s=2.0,
            max_retries=1,
            backoff_base_s=0.01,
        ),
    }
    pipeline_module.STAGE_REGISTRY = fast_registry

    yield test_engine

    db_session.engine = orig_engine
    pipeline_module.engine = orig_pipe_engine
    pipeline_module.STAGE_REGISTRY.clear()


@pytest.mark.asyncio
async def test_happy_path(setup_test_db):
    case_id = "c_happy"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=False)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded files")

    # Run phase 1: from upload to AWAITING_FACTS
    await run_pipeline(case_id)

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        seqs = [e.seq for e in events]
        assert seqs == list(range(1, len(seqs) + 1))
        assert events[-1].stage == Stage.AWAITING_FACTS.value
        assert events[-1].status == StageStatus.WAITING.value

        case = session.exec(select(Case).where(Case.case_id == case_id)).one()
        assert case.status == Stage.AWAITING_FACTS.value

    # Phase 2: confirm facts and resume
    with Session(setup_test_db) as session:
        case = session.exec(select(Case).where(Case.case_id == case_id)).one()
        case.facts_confirmed = True
        session.add(case)
        session.commit()

    await run_pipeline(case_id)

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        seqs = [e.seq for e in events]
        assert seqs == list(range(1, len(seqs) + 1))
        assert events[-1].stage == Stage.READY_FOR_REVIEW.value
        assert events[-1].status == StageStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_retry_then_success(setup_test_db):
    case_id = "c_retry"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    FAILURE_INJECTION[Stage.INVESTIGATING] = "once"

    await run_pipeline(case_id)

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        statuses = [e.status for e in events if e.stage == Stage.INVESTIGATING.value]
        assert StageStatus.RETRYING.value in statuses
        assert events[-1].stage == Stage.READY_FOR_REVIEW.value
        assert events[-1].status == StageStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_plain_runtime_error_retried_max_retries(setup_test_db):
    case_id = "c_runtime_error"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    call_count = 0

    async def failing_stage(ctx: StageContext):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Plain runtime error")

    max_retries = 2
    pipeline_module.STAGE_REGISTRY[Stage.EXTRACTING] = StageSpec(
        fn=failing_stage,
        timeout_s=2.0,
        max_retries=max_retries,
        backoff_base_s=0.01,
        fallback=None,
    )

    with pytest.raises(PipelineException):
        await run_pipeline(case_id)

    assert call_count == max_retries + 1


@pytest.mark.asyncio
async def test_pipeline_exception_non_retryable_called_once(setup_test_db):
    case_id = "c_non_retryable"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    call_count = 0

    async def non_retryable_stage(ctx: StageContext):
        nonlocal call_count
        call_count += 1
        raise PipelineException(code="NON_RETRY", message="Non-retryable error", stage=Stage.EXTRACTING.value, retryable=False)

    pipeline_module.STAGE_REGISTRY[Stage.EXTRACTING] = StageSpec(
        fn=non_retryable_stage,
        timeout_s=2.0,
        max_retries=2,
        backoff_base_s=0.01,
        fallback=None,
    )

    with pytest.raises(PipelineException):
        await run_pipeline(case_id)

    assert call_count == 1


@pytest.mark.asyncio
async def test_upload_and_poll_until_awaiting_facts(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        # Poll database until case reaches AWAITING_FACTS or timeout
        start_time = asyncio.get_event_loop().time()
        reached = False
        while asyncio.get_event_loop().time() - start_time < 10.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    reached = True
                    break
            await asyncio.sleep(0.1)

        assert reached is True


@pytest.mark.asyncio
async def test_pause_and_terminal_stages_no_mock_call(setup_test_db):
    case_id = "c_no_mock"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=False)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    # Run pipeline first time -> reaches AWAITING_FACTS
    await run_pipeline(case_id)

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        last_ev = events[-1]
        assert last_ev.stage == Stage.AWAITING_FACTS.value
        assert last_ev.status == StageStatus.WAITING.value
        assert "Waiting for user to confirm policy facts" in last_ev.detail

    # Neither AWAITING_FACTS nor READY_FOR_REVIEW should be in STAGE_REGISTRY
    assert Stage.AWAITING_FACTS not in pipeline_module.STAGE_REGISTRY
    assert Stage.READY_FOR_REVIEW not in pipeline_module.STAGE_REGISTRY


@pytest.mark.asyncio
async def test_upload_sequence_numbers_no_gaps(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        # Wait briefly for background execution to hit AWAITING_FACTS
        await asyncio.sleep(0.5)

        with Session(setup_test_db) as session:
            events = session.exec(
                select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
            ).all()
            assert len(events) >= 3
            seqs = [e.seq for e in events]
            assert seqs[:3] == [1, 2, 3]
            assert events[0].stage == Stage.UPLOADED.value
            assert events[0].seq == 1


@pytest.mark.asyncio
async def test_retries_exhausted_with_fallback(setup_test_db):
    case_id = "c_fallback"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    FAILURE_INJECTION[Stage.INVESTIGATING] = "fail"

    await run_pipeline(case_id)

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        inv_events = [e for e in events if e.stage == Stage.INVESTIGATING.value]
        inv_statuses = [e.status for e in inv_events]
        assert StageStatus.FALLBACK.value in inv_statuses
        assert any("completed via fallback" in e.detail for e in inv_events)
        assert events[-1].stage == Stage.READY_FOR_REVIEW.value
        assert events[-1].status == StageStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_retries_exhausted_without_fallback(setup_test_db):
    case_id = "c_fail"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    async def sensitive_failing_stage(ctx: StageContext):
        raise RuntimeError("Sensitive PII data and secret_key=xyz")

    pipeline_module.STAGE_REGISTRY[Stage.VERIFYING] = StageSpec(
        fn=sensitive_failing_stage,
        timeout_s=2.0,
        max_retries=1,
        backoff_base_s=0.01,
        fallback=None,
    )

    with pytest.raises(PipelineException) as exc_info:
        await run_pipeline(case_id)

    assert exc_info.value.code == "STAGE_FAILED"
    assert exc_info.value.stage == Stage.VERIFYING.value

    with Session(setup_test_db) as session:
        events = session.exec(
            select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
        ).all()
        assert events[-1].status == StageStatus.FAILED.value
        for e in events:
            assert "secret_key" not in e.detail
            assert "Sensitive PII" not in e.detail

        case = session.exec(select(Case).where(Case.case_id == case_id)).one()
        assert case.status == "FAILED"


@pytest.mark.asyncio
async def test_timeout_path(setup_test_db):
    case_id = "c_timeout"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="UPLOADED", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    async def slow_stage(ctx: StageContext):
        await asyncio.sleep(1.0)

    pipeline_module.STAGE_REGISTRY[Stage.EXTRACTING] = StageSpec(
        fn=slow_stage,
        timeout_s=0.05,
        max_retries=1,
        backoff_base_s=0.01,
        fallback=None,
    )

    with pytest.raises(PipelineException) as exc_info:
        await run_pipeline(case_id)

    assert exc_info.value.code == "STAGE_TIMEOUT"
    assert exc_info.value.stage == Stage.EXTRACTING.value


@pytest.mark.asyncio
async def test_resume_after_simulated_crash(setup_test_db):
    case_id = "c_crash"
    with Session(setup_test_db) as session:
        c = Case(case_id=case_id, status="INVESTIGATING", facts_confirmed=True)
        session.add(c)
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    emit_event(case_id, Stage.EXTRACTING, StageStatus.COMPLETED, "Extracting done")
    emit_event(case_id, Stage.AWAITING_FACTS, StageStatus.COMPLETED, "Facts done")
    emit_event(case_id, Stage.INVESTIGATING, StageStatus.RUNNING, "Investigating crashed")

    stage = determine_resume_stage(case_id)
    assert stage == Stage.INVESTIGATING

    called_stages = []

    async def track_ext(ctx: StageContext):
        called_stages.append(Stage.EXTRACTING)

    async def track_inv(ctx: StageContext):
        called_stages.append(Stage.INVESTIGATING)

    pipeline_module.STAGE_REGISTRY[Stage.EXTRACTING] = StageSpec(
        fn=track_ext, timeout_s=2.0, max_retries=0, backoff_base_s=0.01
    )
    pipeline_module.STAGE_REGISTRY[Stage.INVESTIGATING] = StageSpec(
        fn=track_inv, timeout_s=2.0, max_retries=0, backoff_base_s=0.01
    )

    await run_pipeline(case_id, start_from=stage)
    assert Stage.EXTRACTING not in called_stages
    assert Stage.INVESTIGATING in called_stages


@pytest.mark.asyncio
async def test_duplicate_start_guard(setup_test_db):
    case_id = "c_dup"
    async with pipeline_module._running_lock:
        pipeline_module._running.add(case_id)

    try:
        await run_pipeline(case_id)
        with Session(setup_test_db) as session:
            events = session.exec(
                select(PipelineEvent).where(PipelineEvent.case_id == case_id)
            ).all()
            assert len(events) == 0
    finally:
        async with pipeline_module._running_lock:
            pipeline_module._running.discard(case_id)
