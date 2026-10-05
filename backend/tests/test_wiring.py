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
from backend.app.db.models import Case, Document, Chunk, Finding, PipelineEvent, StageMetric
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


class LLMError(Exception):
    """Custom LLM exception for testing retries."""
    pass


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


def create_fake_ai(raise_error_once=False, status="VERIFIED"):
    fake_ai = types.ModuleType("claimlens_ai")
    call_counts = {"investigation": 0, "verification": 0}

    def build_index(case_id: str, chunks: list[SchemaChunk]) -> None:
        pass

    def run_investigation(case_id: str, rejection: RejectionExtraction, facts: PolicyFacts, emit) -> list[SchemaFinding]:
        call_counts["investigation"] += 1
        if raise_error_once and call_counts["investigation"] == 1:
            raise LLMError("Fake AI transient error")

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
                        chunk_id="chk_doc_policy_01",
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
                status=status,
                final_assessment=f.assessment,
                final_confidence=f.confidence,
                challenges=[],
            )
            for f in findings
        ]

    def draft_review_request(case_id: str, approved: list[VerifiedFinding]):
        return types.SimpleNamespace(
            case_id=case_id,
            finding_ids=[f.finding_id for f in approved],
            text="Draft review request text from M3 fake."
        )

    fake_ai.build_index = build_index
    fake_ai.run_investigation = run_investigation
    fake_ai.run_verification = run_verification
    fake_ai.draft_review_request = draft_review_request
    fake_ai.call_counts = call_counts
    return fake_ai


def create_nested_fake_ai(monkeypatch, raise_error_once=False, status="VERIFIED", draft_fails=False):
    ai_mod = types.ModuleType("ai")
    claimlens_ai_mod = types.ModuleType("ai.claimlens_ai")
    retrieval_mod = types.ModuleType("ai.claimlens_ai.retrieval")
    investigator_mod = types.ModuleType("ai.claimlens_ai.investigator")
    verifier_mod = types.ModuleType("ai.claimlens_ai.verifier")
    drafter_mod = types.ModuleType("ai.claimlens_ai.drafter")

    call_counts = {"investigation": 0, "verification": 0, "draft": 0}

    def build_index(case_id: str, chunks: list[SchemaChunk]) -> None:
        pass

    def run_investigation(case_id: str, rejection: RejectionExtraction, facts: PolicyFacts, emit) -> list[SchemaFinding]:
        call_counts["investigation"] += 1
        if raise_error_once and call_counts["investigation"] == 1:
            raise LLMError("Fake AI transient LLMError")

        emit("RUNNING", {"event": "investigator_step", "step": 1, "max_steps": 6})
        return [
            SchemaFinding(
                finding_id=f"f_{case_id}_101",
                reason_id="r_exclusion_1",
                assessment="SUPPORTED",
                confidence=0.9,
                reasoning="Exclusion 1 applies.",
                evidence=[
                    Evidence(
                        chunk_id="chk_doc_policy_01",
                        quote="Exclusion 1 quote",
                        page=1,
                        section_path="Section 1",
                    )
                ],
            )
        ]

    def run_verification(case_id: str, findings: list[SchemaFinding], emit) -> list[VerifiedFinding]:
        call_counts["verification"] += 1
        emit("RUNNING", {"event": "verifier_step", "step": 1, "max_steps": 1})
        return [
            VerifiedFinding(
                finding_id=f.finding_id,
                reason_id=f.reason_id,
                assessment=f.assessment,
                confidence=f.confidence,
                reasoning=f.reasoning,
                evidence=f.evidence,
                status=status,
                final_assessment=f.assessment,
                final_confidence=f.confidence,
                challenges=[],
            )
            for f in findings
        ]

    def draft_review_request(case_id: str, approved: list[VerifiedFinding]):
        call_counts["draft"] += 1
        if draft_fails:
            raise RuntimeError("M3 draft generation failed")
        return types.SimpleNamespace(
            case_id=case_id,
            finding_ids=[f.finding_id for f in approved],
            text="Official M3 draft review request text."
        )

    retrieval_mod.build_index = build_index
    investigator_mod.run_investigation = run_investigation
    verifier_mod.run_verification = run_verification
    drafter_mod.draft_review_request = draft_review_request

    monkeypatch.setitem(sys.modules, "ai", ai_mod)
    monkeypatch.setitem(sys.modules, "ai.claimlens_ai", claimlens_ai_mod)
    monkeypatch.setitem(sys.modules, "ai.claimlens_ai.retrieval", retrieval_mod)
    monkeypatch.setitem(sys.modules, "ai.claimlens_ai.investigator", investigator_mod)
    monkeypatch.setitem(sys.modules, "ai.claimlens_ai.verifier", verifier_mod)
    monkeypatch.setitem(sys.modules, "ai.claimlens_ai.drafter", drafter_mod)

    return call_counts


def create_nested_fake_docs(monkeypatch, has_text_layer=True):
    docint_mod = types.ModuleType("docint")
    claimlens_docs_mod = types.ModuleType("docint.claimlens_docs")
    call_counts = {"ingest": 0}

    def ingest_document(path: str, doc_id: str, doc_type: str) -> ParsedDocument:
        call_counts["ingest"] += 1
        return ParsedDocument(
            doc_id=doc_id,
            doc_type=doc_type,
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

    claimlens_docs_mod.ingest_document = ingest_document
    claimlens_docs_mod.extract_rejection = extract_rejection
    claimlens_docs_mod.extract_policy_facts = extract_policy_facts
    claimlens_docs_mod.call_counts = call_counts

    monkeypatch.setitem(sys.modules, "docint", docint_mod)
    monkeypatch.setitem(sys.modules, "docint.claimlens_docs", claimlens_docs_mod)

    return claimlens_docs_mod


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
    fake_docint = types.ModuleType("docint")
    fake_docint.claimlens_docs = fake_docs
    monkeypatch.setitem(sys.modules, "docint", fake_docint)
    monkeypatch.setitem(sys.modules, "docint.claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 sample policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            chunks = session.exec(select(Chunk)).all()
            assert len(chunks) > 0

        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [{"kind": "Pre-existing", "months": 24}],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        with Session(engine) as session:
            findings = session.exec(select(Finding).where(Finding.case_id == case_id)).all()
            assert len(findings) > 0
            assert findings[0].review_status == "PENDING"


@pytest.mark.asyncio
async def test_nested_import_paths_ai_and_docs(monkeypatch, setup_test_environment):
    """Test that ai.claimlens_ai.* and docint.claimlens_docs imports are tried first and used."""
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)
    monkeypatch.setattr(settings, "MOCK_AI", False)

    # Remove top level modules from sys.modules
    sys.modules.pop("claimlens_docs", None)
    sys.modules.pop("claimlens_ai", None)

    create_nested_fake_docs(monkeypatch, has_text_layer=True)
    ai_counts = create_nested_fake_ai(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 sample policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        assert ai_counts["investigation"] == 1
        assert ai_counts["verification"] == 1


@pytest.mark.asyncio
async def test_emit_adapter_formatting_and_metrics(monkeypatch, setup_test_environment):
    """Test emit adapter shortens details, caps at 200 chars, and routes llm_metrics to record_metrics."""
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    fake_ai = types.ModuleType("claimlens_ai")

    def build_index(case_id: str, chunks: list) -> None:
        pass

    def run_investigation(case_id: str, rejection: RejectionExtraction, facts: PolicyFacts, emit) -> list[SchemaFinding]:
        # Emit step dict
        emit("RUNNING", {"event": "investigator_step", "step": 2, "max_steps": 5})
        # Emit long message dict (>200 chars)
        emit("RUNNING", {"message": "A" * 300})
        # Emit llm_metrics dict
        emit("RUNNING", {"event": "llm_metrics", "model": "groq-llama3", "tokens_in": 150, "tokens_out": 45})

        return [
            SchemaFinding(
                finding_id=f"f_{case_id}_1",
                reason_id="r1",
                assessment="SUPPORTED",
                confidence=0.8,
                reasoning="Reasoning",
                evidence=[],
            )
        ]

    def run_verification(case_id: str, findings: list, emit) -> list[VerifiedFinding]:
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
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    with Session(engine) as session:
        c = Case(case_id="c_emit_test", status="AWAITING_FACTS", rejection_json=RejectionExtraction(reasons=[]).model_dump_json())
        session.add(c)
        session.commit()

    pipeline_module.start_pipeline("c_emit_test", start_from=pipeline_module.Stage.INVESTIGATING)

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 5.0:
        with Session(engine) as session:
            c = session.exec(select(Case).where(Case.case_id == "c_emit_test")).one_or_none()
            if c and c.status == "READY_FOR_REVIEW":
                break
        await asyncio.sleep(0.05)

    with Session(engine) as session:
        events = session.exec(select(PipelineEvent).where(PipelineEvent.case_id == "c_emit_test")).all()
        details = [e.detail for e in events]
        assert any("investigator_step 2/5" in d for d in details)
        assert any(len(d) <= 200 for d in details if "A" in d)
        # Ensure llm_metrics text is NOT in PipelineEvents
        assert not any("llm_metrics" in d for d in details)

        metrics = session.exec(select(StageMetric).where(StageMetric.case_id == "c_emit_test")).all()
        assert len(metrics) > 0
        m = next(m for m in metrics if m.model == "groq-llama3")
        assert m.tokens_in == 150
        assert m.tokens_out == 45


@pytest.mark.asyncio
async def test_no_text_layer_stops_pipeline(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_DOCS", False)

    fake_docs = create_fake_docs(has_text_layer=False)
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)
    fake_docint = types.ModuleType("docint")
    fake_docint.claimlens_docs = fake_docs
    monkeypatch.setitem(sys.modules, "docint", fake_docint)
    monkeypatch.setitem(sys.modules, "docint.claimlens_docs", fake_docs)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 scanned policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

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
    fake_docint = types.ModuleType("docint")
    fake_docint.claimlens_docs = fake_docs
    monkeypatch.setitem(sys.modules, "docint", fake_docint)
    monkeypatch.setitem(sys.modules, "docint.claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

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
async def test_rejected_ungrounded_finding_stored(monkeypatch, setup_test_environment):
    """Test that a REJECTED_UNGROUNDED status verified finding is stored as a finding without failing."""
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    fake_ai = create_fake_ai(status="REJECTED_UNGROUNDED")
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    with Session(engine) as session:
        c = Case(case_id="c_ungrounded", status="AWAITING_FACTS", rejection_json=RejectionExtraction(reasons=[]).model_dump_json())
        session.add(c)
        session.commit()

    pipeline_module.start_pipeline("c_ungrounded", start_from=pipeline_module.Stage.INVESTIGATING)

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 5.0:
        with Session(engine) as session:
            c = session.exec(select(Case).where(Case.case_id == "c_ungrounded")).one_or_none()
            if c and c.status == "READY_FOR_REVIEW":
                break
        await asyncio.sleep(0.05)

    with Session(engine) as session:
        c = session.exec(select(Case).where(Case.case_id == "c_ungrounded")).one_or_none()
        assert c.status == "READY_FOR_REVIEW"
        findings = session.exec(select(Finding).where(Finding.case_id == "c_ungrounded")).all()
        assert len(findings) == 1
        payload = json.loads(findings[0].payload_json)
        assert payload.get("status") == "REJECTED_UNGROUNDED"


@pytest.mark.asyncio
async def test_draft_m3_and_fallback(monkeypatch, setup_test_environment):
    """Test draft generation calls M3 draft_review_request and falls back to template when it raises."""
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    ai_counts = create_nested_fake_ai(monkeypatch, draft_fails=False)

    with Session(engine) as session:
        c = Case(case_id="c_draft_test", status="READY_FOR_REVIEW")
        ev = PipelineEvent(case_id="c_draft_test", seq=1, stage="READY_FOR_REVIEW", status="COMPLETED", detail="Done")
        f = Finding(
            finding_id="f_d1",
            case_id="c_draft_test",
            reason_id="r1",
            payload_json=json.dumps({
                "finding_id": "f_d1",
                "reason_id": "r1",
                "assessment": "SUPPORTED",
                "confidence": 0.9,
                "reasoning": "Original reasoning",
                "evidence": [{"chunk_id": "c1", "quote": "Quote 1", "page": 1, "section_path": "Sec 1"}],
                "status": "VERIFIED",
                "final_assessment": "SUPPORTED",
                "final_confidence": 0.9,
                "challenges": [],
            }),
            review_status="APPROVED",
        )
        session.add(c)
        session.add(ev)
        session.add(f)
        session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Successful M3 call
        res = await client.post("/api/cases/c_draft_test/draft")
        assert res.status_code == 200
        data = res.json()
        assert data["generated_by"] == "m3"
        assert "Official M3 draft review request text." in data["text"]
        assert "This draft is based on evidence from the uploaded documents and is not legal advice." in data["text"]
        assert len(data["citations"]) == 1
        assert ai_counts["draft"] == 1

        # 2. Falling back when M3 draft call raises exception
        create_nested_fake_ai(monkeypatch, draft_fails=True)
        res2 = await client.post("/api/cases/c_draft_test/draft")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["generated_by"] == "template"
        assert "Regarding reason 'r1': Original reasoning" in data2["text"]


@pytest.mark.asyncio
async def test_missing_package_fallback_and_no_fallback(monkeypatch, setup_test_environment):
    engine, _ = setup_test_environment
    settings = get_settings()
    monkeypatch.setattr(settings, "MOCK_AI", False)

    monkeypatch.setitem(sys.modules, "ai.claimlens_ai", None)
    monkeypatch.setitem(sys.modules, "claimlens_ai", None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        monkeypatch.setattr(settings, "ALLOW_MOCK_FALLBACK", True)

        with Session(engine) as session:
            c1 = Case(case_id="c_fallback_true", status="AWAITING_FACTS", rejection_json="{}")
            session.add(c1)
            session.commit()

        pipeline_module.start_pipeline("c_fallback_true", start_from=pipeline_module.Stage.INVESTIGATING)

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

    pipeline_module.start_pipeline("c_resume_verifying", start_from=pipeline_module.Stage.VERIFYING)

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 5.0:
        with Session(engine) as session:
            case_obj = session.exec(select(Case).where(Case.case_id == "c_resume_verifying")).one_or_none()
            if case_obj and case_obj.status == "READY_FOR_REVIEW":
                break
        await asyncio.sleep(0.05)

    assert fake_ai.call_counts["investigation"] == 0
    assert fake_ai.call_counts["verification"] == 1


@pytest.mark.asyncio
async def test_honest_mode_label_and_api_keys_privacy(monkeypatch, caplog, setup_test_environment):
    """Test mode label in case responses and verify secret API key strings never leak into logs or API responses."""
    caplog.set_level(logging.INFO)
    engine, _ = setup_test_environment
    settings = get_settings()

    secret_groq = "SECRET_GROQ_KEY_999888777"
    secret_gemini = "SECRET_GEMINI_KEY_111222333"

    monkeypatch.setattr(settings, "MOCK_DOCS", False)
    monkeypatch.setattr(settings, "MOCK_AI", False)
    monkeypatch.setattr(settings, "LLM_REPLAY", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", secret_groq)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", secret_gemini)

    fake_docs = create_fake_docs(has_text_layer=True)
    fake_ai = create_fake_ai()
    monkeypatch.setitem(sys.modules, "claimlens_docs", fake_docs)
    fake_docint = types.ModuleType("docint")
    fake_docint.claimlens_docs = fake_docs
    monkeypatch.setitem(sys.modules, "docint", fake_docint)
    monkeypatch.setitem(sys.modules, "docint.claimlens_docs", fake_docs)
    monkeypatch.setitem(sys.modules, "claimlens_ai", fake_ai)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        case_detail_res = await client.get(f"/api/cases/{case_id}")
        case_detail = case_detail_res.json()
        assert "mode" in case_detail
        assert case_detail["mode"] == {"mock_docs": False, "mock_ai": False, "replay_cache": True}

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "AWAITING_FACTS":
                    break
            await asyncio.sleep(0.05)

        facts_payload = {
            "policy_start": "2024-01-01",
            "policy_end": "2025-01-01",
            "sum_insured": 300000.0,
            "waiting_periods": [],
            "confirmed_by_user": True,
        }
        await client.put(f"/api/cases/{case_id}/policy-facts", json=facts_payload)

        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            with Session(engine) as session:
                c = session.exec(select(Case).where(Case.case_id == case_id)).one_or_none()
                if c and c.status == "READY_FOR_REVIEW":
                    break
            await asyncio.sleep(0.05)

        exp_res = await client.get(f"/api/cases/{case_id}/export?format=json")
        exp_data = exp_res.json()
        assert "mode" in exp_data
        assert exp_data["mode"] == {"mock_docs": False, "mock_ai": False, "replay_cache": True}

        # Check logs: SECRET_CHUNK_TEXT_MARKER_999 and API keys MUST NEVER appear in log output or API responses
        for record in caplog.records:
            log_msg = record.getMessage()
            assert "SECRET_CHUNK_TEXT_MARKER_999" not in log_msg
            assert secret_groq not in log_msg
            assert secret_gemini not in log_msg

        assert secret_groq not in case_detail_res.text
        assert secret_gemini not in case_detail_res.text
        assert secret_groq not in exp_res.text
        assert secret_gemini not in exp_res.text
