import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
import backend.app.api.events as events_module
import backend.app.api.facts as facts_module
from backend.app.services.pipeline import (
    Stage,
    StageStatus,
    StageSpec,
    StageContext,
    emit_event,
    FAILURE_INJECTION,
    FAILURE_ATTEMPTS,
)
from backend.app.db.models import Case, PipelineEvent
from backend.app.main import app
from backend.app.services import mock_stages


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_claimlens_facts.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    test_upload_dir = tmp_path / "test_uploads"
    test_upload_dir.mkdir(parents=True, exist_ok=True)

    orig_engine = db_session.engine
    orig_pipe_engine = pipeline_module.engine
    orig_events_engine = events_module.engine
    orig_upload_dir = cases_module.UPLOAD_DIR

    db_session.engine = test_engine
    pipeline_module.engine = test_engine
    events_module.engine = test_engine
    cases_module.UPLOAD_DIR = str(test_upload_dir)

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
    events_module.engine = orig_events_engine
    cases_module.UPLOAD_DIR = orig_upload_dir
    pipeline_module.STAGE_REGISTRY.clear()


@pytest.mark.asyncio
async def test_upload_and_get_prefilled_facts(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        # Poll until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    break
            await asyncio.sleep(0.05)

        # GET policy-facts
        facts_res = await client.get(f"/api/cases/{case_id}/policy-facts")
        assert facts_res.status_code == 200
        data = facts_res.json()
        assert data["policy_start"] == "2024-01-01"
        assert data["policy_end"] == "2025-01-01"
        assert data["sum_insured"] == 500000.0
        assert len(data["waiting_periods"]) == 2
        assert data["confirmed_by_user"] is False


@pytest.mark.asyncio
async def test_put_edited_facts_and_resume_pipeline(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    break
            await asyncio.sleep(0.05)

        # PUT edited facts
        payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-06-01",
            "sum_insured": 750000.0,
            "waiting_periods": [{"kind": "Pre-existing diseases", "months": 24}],
            "confirmed_by_user": True,
        }
        put_res = await client.put(f"/api/cases/{case_id}/policy-facts", json=payload)
        assert put_res.status_code == 200
        put_data = put_res.json()
        assert put_data["sum_insured"] == 750000.0
        assert put_data["confirmed_by_user"] is True

        # Poll until pipeline finishes at READY_FOR_REVIEW
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.READY_FOR_REVIEW.value:
                    break
            await asyncio.sleep(0.05)

        with Session(setup_test_db) as session:
            events = session.exec(
                select(PipelineEvent).where(PipelineEvent.case_id == case_id).order_by(PipelineEvent.seq)
            ).all()
            seqs = [e.seq for e in events]
            assert seqs == list(range(1, len(seqs) + 1))

            awaiting_completed = [
                e for e in events if e.stage == Stage.AWAITING_FACTS.value and e.status == StageStatus.COMPLETED.value
            ]
            assert len(awaiting_completed) == 1
            assert events[-1].stage == Stage.READY_FOR_REVIEW.value
            assert events[-1].status == StageStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_put_invalid_facts_returns_422(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    break
            await asyncio.sleep(0.05)

        # Invalid A: policy_end <= policy_start
        bad_a = {
            "policy_start": "2025-01-01",
            "policy_end": "2024-01-01",
            "sum_insured": 500000.0,
            "waiting_periods": [],
        }
        res_a = await client.put(f"/api/cases/{case_id}/policy-facts", json=bad_a)
        assert res_a.status_code == 422
        assert res_a.json()["error"]["code"] == "VALIDATION_ERROR"
        assert res_a.json()["error"]["stage"] == "AWAITING_FACTS"

        # Invalid B: sum_insured < 0
        bad_b = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": -500.0,
            "waiting_periods": [],
        }
        res_b = await client.put(f"/api/cases/{case_id}/policy-facts", json=bad_b)
        assert res_b.status_code == 422
        assert res_b.json()["error"]["code"] == "VALIDATION_ERROR"

        # Invalid C: waiting period months < 0
        bad_c = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 500000.0,
            "waiting_periods": [{"kind": "Pre-existing", "months": -12}],
        }
        res_c = await client.put(f"/api/cases/{case_id}/policy-facts", json=bad_c)
        assert res_c.status_code == 422
        assert res_c.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_put_when_not_at_awaiting_facts_returns_409(setup_test_db):
    case_id = "c_wrong_stage"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="UPLOADED", facts_confirmed=False))
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 500000.0,
            "waiting_periods": [],
        }
        res = await client.put(f"/api/cases/{case_id}/policy-facts", json=payload)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "WRONG_STAGE"


@pytest.mark.asyncio
async def test_unknown_case_id_returns_404(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res1 = await client.get("/api/cases/c_nonexistent/policy-facts")
        assert res1.status_code == 404
        assert res1.json()["error"]["code"] == "CASE_NOT_FOUND"

        payload = {"policy_start": "2024-01-01"}
        res2 = await client.put("/api/cases/c_nonexistent/policy-facts", json=payload)
        assert res2.status_code == 404
        assert res2.json()["error"]["code"] == "CASE_NOT_FOUND"

        res3 = await client.get("/api/cases/c_nonexistent")
        assert res3.status_code == 404
        assert res3.json()["error"]["code"] == "CASE_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_case_detail_shape_no_filesystem_paths(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(setup_test_db) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    break
            await asyncio.sleep(0.05)

        detail_res = await client.get(f"/api/cases/{case_id}")
        assert detail_res.status_code == 200
        data = detail_res.json()

        assert data["case_id"] == case_id
        assert "status" in data
        assert "stage" in data
        assert "facts_confirmed" in data
        assert "documents" in data
        assert "last_seq" in data
        assert isinstance(data["documents"], list)
        assert len(data["documents"]) == 2

        # Assert no filesystem paths in response string
        text_content = str(data)
        assert "uploads" not in text_content
        assert ".pdf" not in text_content
