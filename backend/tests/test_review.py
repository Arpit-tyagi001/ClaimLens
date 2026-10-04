import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select, text

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
import backend.app.api.events as events_module
import backend.app.api.facts as facts_module
import backend.app.api.review as review_module
from backend.app.db.session import create_db_and_tables
from backend.app.services.pipeline import (
    Stage,
    StageStatus,
    StageSpec,
    StageContext,
    emit_event,
    FAILURE_INJECTION,
    FAILURE_ATTEMPTS,
)
from backend.app.db.models import Case, Finding, AuditLog, PipelineEvent
from backend.app.main import app
from backend.app.services import mock_stages


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_claimlens_review.db"
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

    create_db_and_tables(test_engine)

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


async def _setup_case_at_ready_for_review(engine) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 dummy policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 dummy letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # Poll until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.AWAITING_FACTS.value:
                    break
            await asyncio.sleep(0.05)

        # Confirm facts
        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 500000.0,
            "waiting_periods": [],
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        # Poll until READY_FOR_REVIEW
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                case = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if case and case.status == Stage.READY_FOR_REVIEW.value:
                    break
            await asyncio.sleep(0.05)

        return case_id


@pytest.mark.asyncio
async def test_get_findings_after_ready_for_review(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/cases/{case_id}/findings")
        assert res.status_code == 200
        findings = res.json()
        assert len(findings) == 3
        for f in findings:
            assert f["review_status"] == "PENDING"
            assert f["edited_reasoning"] is None
            assert "payload" in f
            assert "finding_id" in f["payload"]


@pytest.mark.asyncio
async def test_approve_writes_audit_log_and_second_review_appends(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings_res = await client.get(f"/api/cases/{case_id}/findings")
        fid = findings_res.json()[0]["finding_id"]

        # Review 1: APPROVE
        rev1 = await client.post(
            f"/api/findings/{fid}/review",
            json={"action": "APPROVE", "note": "First approve note"},
        )
        assert rev1.status_code == 200
        assert rev1.json()["review_status"] == "APPROVED"

        audit_res1 = await client.get(f"/api/cases/{case_id}/audit")
        assert audit_res1.status_code == 200
        logs1 = audit_res1.json()
        assert len(logs1) == 1
        assert logs1[0]["finding_id"] == fid
        assert logs1[0]["action"] == "APPROVE"
        assert logs1[0]["before"]["review_status"] == "PENDING"
        assert logs1[0]["after"]["review_status"] == "APPROVED"
        assert logs1[0]["note"] == "First approve note"

        # Review 2: REJECT on same finding
        rev2 = await client.post(
            f"/api/findings/{fid}/review",
            json={"action": "REJECT", "note": "Changed mind to reject"},
        )
        assert rev2.status_code == 200
        assert rev2.json()["review_status"] == "REJECTED"

        audit_res2 = await client.get(f"/api/cases/{case_id}/audit")
        logs2 = audit_res2.json()
        assert len(logs2) == 2
        # Verify first log remains unchanged
        assert logs2[0]["action"] == "APPROVE"
        assert logs2[0]["after"]["review_status"] == "APPROVED"
        # Verify second log appended
        assert logs2[1]["action"] == "REJECT"
        assert logs2[1]["before"]["review_status"] == "APPROVED"
        assert logs2[1]["after"]["review_status"] == "REJECTED"


@pytest.mark.asyncio
async def test_reject_and_edit_reviews_and_validation(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]
        f2_id = findings[1]["finding_id"]

        # REJECT
        res_rej = await client.post(f"/api/findings/{f1_id}/review", json={"action": "REJECT"})
        assert res_rej.status_code == 200
        assert res_rej.json()["review_status"] == "REJECTED"

        # EDIT valid
        res_edit = await client.post(
            f"/api/findings/{f2_id}/review",
            json={"action": "EDIT", "edited_reasoning": "Updated evidence reasoning"},
        )
        assert res_edit.status_code == 200
        assert res_edit.json()["review_status"] == "EDITED"
        assert res_edit.json()["edited_reasoning"] == "Updated evidence reasoning"

        # EDIT without edited_reasoning -> 422
        res_bad_edit = await client.post(
            f"/api/findings/{f2_id}/review",
            json={"action": "EDIT", "edited_reasoning": ""},
        )
        assert res_bad_edit.status_code == 422
        assert res_bad_edit.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_review_before_ready_for_review_returns_409(setup_test_db):
    case_id = "c_early"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="AWAITING_FACTS"))
        session.add(Finding(finding_id="f_early_001", case_id=case_id, reason_id="r1", payload_json="{}"))
        session.commit()

    emit_event(case_id, Stage.AWAITING_FACTS, StageStatus.WAITING, "Waiting")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/findings/f_early_001/review",
            json={"action": "APPROVE"},
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "WRONG_STAGE"


@pytest.mark.asyncio
async def test_unknown_finding_returns_404(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/findings/f_nonexistent/review",
            json={"action": "APPROVE"},
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "FINDING_NOT_FOUND"


@pytest.mark.asyncio
async def test_transaction_atomicity_rollback(setup_test_db, monkeypatch):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    # Monkeypatch AuditLog init or session add to force an error during commit
    orig_add = Session.add

    def failing_add(self, instance):
        if isinstance(instance, AuditLog):
            raise RuntimeError("Database error on audit log insert!")
        return orig_add(self, instance)

    monkeypatch.setattr(Session, "add", failing_add)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        fid = findings[0]["finding_id"]

        res = await client.post(f"/api/findings/{fid}/review", json={"action": "APPROVE"})
        assert res.status_code == 500

        # Verify finding review_status is still PENDING in DB
        with Session(setup_test_db) as session:
            f = session.exec(select(Finding).where(Finding.finding_id == fid)).one()
            assert f.review_status == "PENDING"


@pytest.mark.asyncio
async def test_audit_log_append_only_triggers(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        fid = findings[0]["finding_id"]
        await client.post(f"/api/findings/{fid}/review", json={"action": "APPROVE"})

    with Session(setup_test_db) as session:
        audit_row = session.exec(select(AuditLog).where(AuditLog.case_id == case_id)).first()
        assert audit_row is not None

        # Try raw UPDATE -> trigger raises error
        with pytest.raises(Exception) as exc_up:
            session.exec(text("UPDATE audit_log SET note = 'tampered' WHERE id = :id"), params={"id": audit_row.id})
            session.commit()
        assert "append-only" in str(exc_up.value)

        # Try raw DELETE -> trigger raises error
        with pytest.raises(Exception) as exc_del:
            session.exec(text("DELETE FROM audit_log WHERE id = :id"), params={"id": audit_row.id})
            session.commit()
        assert "append-only" in str(exc_del.value)


@pytest.mark.asyncio
async def test_idempotent_mock_stage_rerun_preserves_reviews(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]

        # Review f1 as APPROVED
        await client.post(f"/api/findings/{f1_id}/review", json={"action": "APPROVE"})

    # Re-run VERIFYING mock stage
    ctx = StageContext(case_id=case_id, emit=lambda *a, **kw: None)
    await mock_stages.mock_verifying_stage(ctx)

    with Session(setup_test_db) as session:
        f1 = session.exec(select(Finding).where(Finding.finding_id == f1_id)).one()
        assert f1.review_status == "APPROVED"

        all_findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()
        assert len(all_findings) == 3


@pytest.mark.asyncio
async def test_review_summary_counts_and_can_draft(setup_test_db):
    case_id = await _setup_case_at_ready_for_review(setup_test_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial summary: all PENDING, can_draft=false
        s1 = (await client.get(f"/api/cases/{case_id}/review-summary")).json()
        assert s1 == {
            "total": 3,
            "pending": 3,
            "approved": 0,
            "rejected": 0,
            "edited": 0,
            "can_draft": False,
        }

        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]
        f2_id = findings[1]["finding_id"]

        # Approve f1
        await client.post(f"/api/findings/{f1_id}/review", json={"action": "APPROVE"})

        s2 = (await client.get(f"/api/cases/{case_id}/review-summary")).json()
        assert s2["pending"] == 2
        assert s2["approved"] == 1
        assert s2["can_draft"] is True

        # Edit f2
        await client.post(
            f"/api/findings/{f2_id}/review",
            json={"action": "EDIT", "edited_reasoning": "New text"},
        )

        s3 = (await client.get(f"/api/cases/{case_id}/review-summary")).json()
        assert s3["pending"] == 1
        assert s3["approved"] == 1
        assert s3["edited"] == 1
        assert s3["can_draft"] is True
