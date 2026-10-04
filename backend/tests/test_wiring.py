import asyncio
import json
import logging
import sys
import types
import pytest
from datetime import date
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
from backend.app.db.models import Case, Document, Chunk, Finding, PipelineEvent
from backend.app.main import app
from backend.app.config import get_settings
from contracts.schemas import (
    ParsedDocument,
    Chunk as SchemaChunk,
    RejectionExtraction,
    RejectionReason,
    PolicyFacts,
    WaitingPeriod,
    Finding as SchemaFinding,
    VerifiedFinding,
    Evidence,
)


def create_fake_docs(has_text_layer=True):
    fake_docs = types.ModuleType("claimlens_docs")
    call_counts = {"ingest": 0}

    def ingest_document(path: str, doc_id: str, doc_type: str) -> ParsedDocument:
        call_counts["ingest"] += 1
        return ParsedDocument(
            doc_id=doc_id,
            doc_type=doc_type,  # "policy" or "letter"
            n_pages=2,
            has_text_layer=has_text_layer if doc_type == "policy" else True,
            chunks=[
                SchemaChunk(
                    chunk_id=f"chk_{doc_id}_01",
                    doc_id=doc_id,
                    section_path="Section 1",
                    page=1,
                    text=f"Fake text content for {doc_type} SECRET_CHUNK_TEXT_MARKER_999",
                    bbox=[0.0, 0.0, 100.0, 100.0],
                    tags=["test"],
                )
            ],
        )

    def extract_rejection(doc: ParsedDocument) -> RejectionExtraction:
        return RejectionExtraction(
            claim_no="CLM_1001",
            policy_no="POL_2002",
            reasons=[
                RejectionReason(
                    reason_id="r_exclusion_1",
                    text="Exclusion clause 1",
                    category="Exclusion",
                    page=1,
                    bbox=[0.0, 0.0, 50.0, 50.0],
                )
            ],
        )

    def extract_policy_facts(doc: ParsedDocument) -> PolicyFacts:
        return PolicyFacts(
            policy_start=date(2024, 1, 1),
            policy_end=date(2025, 1, 1),
            sum_insured=300000.0,
            waiting_periods=[WaitingPeriod(kind="Pre-existing", months=24)],
            confirmed_by_user=False,
        )

    fake_docs.ingest_document = ingest_document
    fake_docs.extract_rejection = extract_rejection
    fake_docs.extract_policy_facts = extract_policy_facts
    fake_docs.call_counts = call_counts
    return fake_docs


def create_fake_ai(raise_error_once=False):
    fake_ai = types.ModuleType("claimlens_ai")
    call_counts = {"investigation": 0, "verification": 0}

    def build_index(case_id: str, chunks: list[SchemaChunk]) -> None:
        pass

    def run_investigation(case_id: str, rejection: RejectionExtraction, facts: PolicyFacts, emit) -> list[SchemaFinding]:
        call_counts["investigation"] += 1
        if raise_error_once and call_counts["investigation"] == 1:
            raise RuntimeError("Fake AI transient error")

        emit("RUNNING", "Fake AI running investigation")
        return [
            SchemaFinding(
                finding_id=f"f_{case_id}_101",
                reason_id="r_exclusion_1",
                assessment="SUPPORTED",
                confidence=0.9,
                reasoning="Exclusion 1 applies.",
                evidence=[
                    Evidence(
                        chunk_id=f"chk_doc_policy_01",
                        quote="Exclusion 1 quote",
                        page=1,
                        section_path="Section 1",
                    )
                ],
            )
        ]

    def run_verification(case_id: str, findings: list[SchemaFinding], emit) -> list[VerifiedFinding]:
        call_counts["verification"] += 1
        emit("RUNNING", "Fake AI running verification")
        return [
            VerifiedFinding(
                finding_id=f.finding_id,
                reason_id=f.reason_id,
                assessment=f.assessment,
                confidence=f.confidence,
                reasoning=f.reasoning,
                evidence=f.evidence,
                status="VERIFIED",
                final_assessment=f.assessment,
                final_confidence=f.confidence,
                challenges=[],
            )
            for f in findings
        ]

    fake_ai.build_index = build_index
    fake_ai.run_investigation = run_investigation
    fake_ai.run_verification = run_verification
    fake_ai.call_counts = call_counts
    return fake_ai


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    db_file = tmp_path / "test_claimlens_wiring.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    test_upload_dir = tmp_path / "test_uploads_wiring"
    test_upload_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db_session, "engine", test_engine)
    monkeypatch.setattr(pipeline_module, "engine", test_engine)
    monkeypatch.setattr(cases_module, "UPLOAD_DIR", str(test_upload_dir))

    settings = get_settings()
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(test_upload_dir))
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_file}")

    db_session.create_db_and_tables(test_engine)
    yield test_engine, test_upload_dir


@pytest.mark.asyncio
async def test_real_adapters_with_fakes(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)
    monkeypatch.setattr(settings, "MOCK_AI", False)

    fake_docs = create_fake_docs(has_text_layer=True)
    fake_ai = create_fake_ai(raise_error_once=False)
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload case -> pipeline runs real_extracting_stage with fake_docs
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 sample policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        # Poll until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        # Assert chunks persisted in DB
        with Session(engine) as session:
            chunks = session.exec(select(Chunk)).all()
            assert len(chunks) > 0

        # 2. PUT facts -> pipeline runs real_investigating and real_verifying
        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [{"kind": "Pre-existing", "months": 24}],
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

        # Assert findings stored with review_status PENDING
        with Session(engine) as session:
            findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()
            assert len(findings) > 0
            assert findings[0].review_status == "PENDING"


@pytest.mark.asyncio
async def test_no_text_layer_stops_pipeline(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)

    fake_docs = create_fake_docs(has_text_layer=False)
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 scanned policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # Poll until FAILED
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "FAILED":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
            assert c.status == "FAILED"
            events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == case_id)).all()
            failed_ev = next(e for e in events if e.status == "FAILED")
            assert "NO_TEXT_LAYER" in failed_ev.detail or "readable text layer" in failed_ev.detail

        # Assert stage was NOT retried (called once per document = 2 calls)
        assert fake_docs.call_counts["ingest"] == 2


@pytest.mark.asyncio
async def test_transient_error_in_real_adapter_retries(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)
    monkeypatch.setattr(settings, "MOCK_AI", False)

    fake_docs = create_fake_docs(has_text_layer=True)
    fake_ai = create_fake_ai(raise_error_once=True)
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # Wait until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        # PUT facts -> investigation fails once, retries, then succeeds
        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        # Wait until READY_FOR_REVIEW
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == case_id)).all()
            retrying_events = [e for e in events if e.status == "RETRYING" and e.stage == "INVESTIGATING"]
            assert len(retrying_events) >= 1
            assert fake_ai.call_counts["investigation"] == 2


@pytest.mark.asyncio
async def test_missing_package_fallback_and_no_fallback(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    # Force missing package
    monkeypatch.setitem(sys.modules, "claimlens_ai", None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: ALLOW_MOCK_FALLBACK = True -> fallback event emitted, mock stage runs
        monkeypatch.setattr(settings, "ALLOW_MOCK_FALLBACK", True)

        # Set case at AWAITING_FACTS directly
        with Session(engine) as session:
            c1 = Case(case_id="c_fallback_true", status="AWAITING_FACTS", rejection_json="{}")
            session.add(c1)
            session.commit()

        pipeline_module.start_pipeline("c_fallback_true", start_from=pipeline_module.Stage.INVESTIGATING)

        # Wait until READY_FOR_REVIEW
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == "c_fallback_true")).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == "c_fallback_true")).all()
            fallback_events = [e for e in events if e.status == "FALLBACK"]
            assert len(fallback_events) > 0
            assert "not available" in fallback_events[0].detail

        # Case 2: ALLOW_MOCK_FALLBACK = False -> fails with MODULE_UNAVAILABLE
        monkeypatch.setattr(settings, "ALLOW_MOCK_FALLBACK", False)

        with Session(engine) as session:
            c2 = Case(case_id="c_fallback_false", status="AWAITING_FACTS", rejection_json="{}")
            session.add(c2)
            session.commit()

        pipeline_module.start_pipeline("c_fallback_false", start_from=pipeline_module.Stage.INVESTIGATING)

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == "c_fallback_false")).one_or_none()
                if c and c.status == "FAILED":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == "c_fallback_false")).all()
            failed_ev = next(e for e in events if e.status == "FAILED")
            assert "MODULE_UNAVAILABLE" in failed_ev.detail or "not installed" in failed_ev.detail


@pytest.mark.asyncio
async def test_idempotent_resume_verifying_does_not_reinvestigate(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    fake_ai = create_fake_ai()
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    # Set up case with existing unverified_findings_json
    finding_obj = SchemaFinding(
        finding_id="f_pre_stored",
        reason_id="r_1",
        assessment="SUPPORTED",
        confidence=0.8,
        reasoning="Pre-stored unverified finding",
        evidence=[],
    )
    with Session(engine) as session:
        c = Case(
            case_id="c_resume_verifying",
            status="INVESTIGATING",
            unverified_findings_json=json.dumps([finding_obj.model_dump(mode="json")]),
        )
        session.add(c)
        session.commit()

    # Resume directly from VERIFYING
    pipeline_module.start_pipeline("c_resume_verifying", start_from=pipeline_module.Stage.VERIFYING)

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 5.0:
        with Session(engine) as session:
            case_obj = session.exec(select(Case).where(Case.case_id == "c_resume_verifying")).one_or_none()
            if case_obj and case_obj.status == "READY_FOR_REVIEW":
                break
        await asyncio.sleep(0.05)

    # Assert run_investigation was NOT called, only run_verification was called
    assert fake_ai.call_counts["investigation"] == 0
    assert fake_ai.call_counts["verification"] == 1


@pytest.mark.asyncio
async def test_honest_mode_label_and_no_text_in_logs(monkeypatch, caplog, setup_test_environment):
    caplog.set_level(logging.INFO)
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)
    monkeypatch.setattr(settings, "MOCK_AI", False)
    monkeypatch.setattr(settings, "LLM_REPLAY", True)

    fake_docs = create_fake_docs(has_text_layer=True)
    fake_ai = create_fake_ai()
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload case
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        # GET /api/cases/{case_id} -> check mode dictionary
        case_detail = (await client.get(f"/api/cases/{case_id}")).json()
        assert "mode" in case_detail
        assert case_detail["mode"] == {"mock_docs": False, "mock_ai": False, "replay_cache": True}

        # Poll until AWAITING_FACTS
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        # PUT policy facts
        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [],
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

        # Export JSON -> check mode dictionary
        exp_res = await client.get(f"/api/cases/{case_id}/export?format=json")
        exp_data = exp_res.json()
        assert "mode" in exp_data
        assert exp_data["mode"] == {"mock_docs": False, "mock_ai": False, "replay_cache": True}

        # Check logs: SECRET_CHUNK_TEXT_MARKER_999 MUST NEVER appear in any log output
        for record in caplog.records:
            assert "SECRET_CHUNK_TEXT_MARKER_999" not in record.getMessage()
