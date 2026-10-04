import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
from backend.app.db.models import Case, Document, Finding, PipelineEvent, AuditLog, Draft
from backend.app.main import app
from backend.app.config import get_settings
from backend.app.services import mock_stages


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    db_file = tmp_path / "test_claimlens_draft_export.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    test_upload_dir = tmp_path / "test_uploads_draft"
    test_upload_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db_session, "engine", test_engine)
    monkeypatch.setattr(pipeline_module, "engine", test_engine)
    monkeypatch.setattr(cases_module, "UPLOAD_DIR", str(test_upload_dir))

    settings = get_settings()
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(test_upload_dir))
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_file}")

    db_session.create_db_and_tables(test_engine)
    yield test_engine, test_upload_dir


async def _setup_case_at_ready_for_review(engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # Poll until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        # PUT facts to resume pipeline
        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 500000.0,
            "waiting_periods": [{"kind": "Pre-existing diseases", "months": 36}],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        # Poll until READY_FOR_REVIEW
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        return case_id


@pytest.mark.asyncio
async def test_draft_wrong_stage_and_no_approved_findings(setup_test_environment):
    engine, _ = setup_test_environment
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload case (status: UPLOADED / EXTRACTING)
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # 2. Try POST /draft before READY_FOR_REVIEW -> 409 WRONG_STAGE
        draft_err = await client.post(f"/api/cases/{case_id}/draft")
        assert draft_err.status_code == 409
        assert draft_err.json()["error"]["code"] == "WRONG_STAGE"

    # 3. Advance case to READY_FOR_REVIEW but no findings approved yet -> 409 NO_APPROVED_FINDINGS
    ready_case_id = await _setup_case_at_ready_for_review(engine)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_app_res = await client.post(f"/api/cases/{ready_case_id}/draft")
        assert no_app_res.status_code == 409
        assert no_app_res.json()["error"]["code"] == "NO_APPROVED_FINDINGS"


@pytest.mark.asyncio
async def test_draft_generation_approve_and_edit(setup_test_environment):
    engine, _ = setup_test_environment
    case_id = await _setup_case_at_ready_for_review(engine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Fetch findings
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]
        f2_id = findings[1]["finding_id"]

        # Approve finding 1
        await client.post(f"/api/findings/{f1_id}/review", json={"action": "APPROVE"})

        # Edit finding 2
        edited_text = "Custom edited reasoning for finding 2 clause check."
        await client.post(
            f"/api/findings/{f2_id}/review",
            json={"action": "EDIT", "edited_reasoning": edited_text},
        )

        # Generate draft
        res = await client.post(f"/api/cases/{case_id}/draft")
        assert res.status_code == 200
        data = res.json()

        assert "draft_id" in data
        assert data["draft_id"].startswith("draft_")
        assert len(data["citations"]) > 0
        assert edited_text in data["text"]
        assert "This draft is based on evidence from the uploaded documents and is not legal advice." in data["text"]

        # Verify AuditLog row DRAFT_GENERATED exists in same commit
        with Session(engine) as session:
            audit_entry = session.exec(
                select(AuditLog)
                .where(AuditLog.case_id == case_id)
                .where(AuditLog.action == "DRAFT_GENERATED")
            ).first()
            assert audit_entry is not None
            assert audit_entry.actor == "reviewer"
            assert data["draft_id"] in audit_entry.after_json


@pytest.mark.asyncio
async def test_latest_draft_returns_newest(setup_test_environment):
    engine, _ = setup_test_environment
    case_id = await _setup_case_at_ready_for_review(engine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]
        await client.post(f"/api/findings/{f1_id}/review", json={"action": "APPROVE"})

        # First draft
        res1 = await client.post(f"/api/cases/{case_id}/draft")
        draft1_id = res1.json()["draft_id"]

        # Second draft
        res2 = await client.post(f"/api/cases/{case_id}/draft")
        draft2_id = res2.json()["draft_id"]
        assert draft1_id != draft2_id

        # GET /latest -> returns second draft
        latest_res = await client.get(f"/api/cases/{case_id}/draft/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["draft_id"] == draft2_id


@pytest.mark.asyncio
async def test_export_json_and_html_and_validation(setup_test_environment):
    engine, _ = setup_test_environment
    case_id = await _setup_case_at_ready_for_review(engine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Edit finding with script tag to test HTML escaping
        findings = (await client.get(f"/api/cases/{case_id}/findings")).json()
        f1_id = findings[0]["finding_id"]
        xss_payload = "<script>alert(1)</script>"
        await client.post(
            f"/api/findings/{f1_id}/review",
            json={"action": "EDIT", "edited_reasoning": xss_payload},
        )

        # 1. Invalid format -> 422
        bad_fmt = await client.get(f"/api/cases/{case_id}/export?format=xml")
        assert bad_fmt.status_code == 422
        assert bad_fmt.json()["error"]["code"] == "VALIDATION_ERROR"

        # 2. JSON export
        res_json = await client.get(f"/api/cases/{case_id}/export?format=json")
        assert res_json.status_code == 200
        assert "application/json" in res_json.headers["Content-Type"]
        assert f'filename="claimlens-{case_id}.json"' in res_json.headers["Content-Disposition"]

        json_data = res_json.json()
        assert json_data["case_id"] == case_id
        assert "notice" in json_data
        assert "findings" in json_data
        assert "audit_log" in json_data

        # Verify NO filesystem path keys exist
        raw_json_str = res_json.text
        assert "file_path" not in json_data
        assert "uploads_dir" not in json_data

        # 3. HTML export
        res_html = await client.get(f"/api/cases/{case_id}/export?format=html")
        assert res_html.status_code == 200
        assert "text/html" in res_html.headers["Content-Type"]
        assert f'filename="claimlens-{case_id}.html"' in res_html.headers["Content-Disposition"]

        html_text = res_html.text
        # Assert XSS script tag is HTML-escaped and NO raw script tag exists
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
        assert "<script>alert(1)</script>" not in html_text


@pytest.mark.asyncio
async def test_trace_endpoint_metrics(setup_test_environment):
    engine, _ = setup_test_environment

    # Enable failure injection "once" on INVESTIGATING
    pipeline_module.FAILURE_INJECTION[pipeline_module.Stage.INVESTIGATING] = "once"
    pipeline_module.FAILURE_ATTEMPTS[pipeline_module.Stage.INVESTIGATING] = 0

    case_id = await _setup_case_at_ready_for_review(engine)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(f"/api/cases/{case_id}/trace")
        assert res.status_code == 200
        data = res.json()

        assert data["case_id"] == case_id
        assert data["total_duration_ms"] >= 0
        assert "stages" in data

        stages_dict = {s["stage"]: s for s in data["stages"]}
        assert "EXTRACTING" in stages_dict
        assert "INVESTIGATING" in stages_dict
        assert "VERIFYING" in stages_dict

        inv = stages_dict["INVESTIGATING"]
        assert inv["retries"] == 1
        assert inv["attempts"] >= 2
        assert inv["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_eval_latest_report_endpoint(tmp_path, monkeypatch):
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing file -> available: False
        monkeypatch.setattr(settings, "EVAL_REPORT_PATH", str(tmp_path / "nonexistent_report.json"))
        res_missing = await client.get("/api/eval/latest")
        assert res_missing.status_code == 200
        assert res_missing.json() == {"available": False, "message": "No evaluation report has been generated yet."}

        # 2. Valid report sample
        sample_path = tmp_path / "report.sample.json"
        sample_data = {"synthetic_benchmark": True, "n_cases": 8, "extraction_correct": 7}
        sample_path.write_text(json.dumps(sample_data), encoding="utf-8")

        monkeypatch.setattr(settings, "EVAL_REPORT_PATH", str(sample_path))
        res_valid = await client.get("/api/eval/latest")
        assert res_valid.status_code == 200
        assert res_valid.json()["synthetic_benchmark"] is True
        assert res_valid.json()["n_cases"] == 8
