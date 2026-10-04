import asyncio
import json
import logging
import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select, text

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
from backend.app.db.models import Case, Document, Finding, PipelineEvent, AuditLog, utc_now
from backend.app.main import app, PipelineException
from backend.app.config import get_settings, Settings
from backend.app.services.rate_limiter import limiter
from backend.app.services.purge import purge_expired_cases


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path, monkeypatch):
    db_file = tmp_path / "test_claimlens_hygiene.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    test_upload_dir = tmp_path / "test_uploads_hygiene"
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
async def test_settings_override_and_properties(monkeypatch):
    settings = get_settings()
    assert settings.ENV in ("dev", "production", "test")

    # Override CORS_ORIGINS as string with multiple domains
    monkeypatch.setattr(settings, "CORS_ORIGINS", "http://localhost:3000, http://example.com")
    assert settings.cors_origins_list == ["http://localhost:3000", "http://example.com"]

    # Override RETENTION_HOURS
    monkeypatch.setattr(settings, "RETENTION_HOURS", 48)
    assert settings.RETENTION_HOURS == 48


@pytest.mark.asyncio
async def test_healthz_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_healthy_and_unhealthy_503(monkeypatch, setup_test_environment):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Healthy path
        res = await client.get("/readyz")
        assert res.status_code == 200
        assert res.json() == {"status": "ready"}

        # Simulate DB connection failure
        def mock_engine_connect():
            raise Exception("Database connection refused")

        monkeypatch.setattr(db_session.engine, "connect", mock_engine_connect)

        res_unhealthy = await client.get("/readyz")
        assert res_unhealthy.status_code == 503
        data = res_unhealthy.json()
        assert data["error"]["code"] == "NOT_READY"
        assert data["error"]["message"] == "Service not ready"
        assert data["error"]["stage"] is None
        # Verify no internal exception details leaked
        assert "connection refused" not in data["error"]["message"].lower()


@pytest.mark.asyncio
async def test_json_logging_contains_request_id(caplog):
    caplog.set_level(logging.INFO)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/healthz")
        req_id = res.headers["X-Request-ID"]

        # Parse captured records
        log_json_records = []
        for record in caplog.records:
            try:
                data = json.loads(record.getMessage())
                log_json_records.append(data)
            except (json.JSONDecodeError, TypeError):
                pass

        # If caplog intercepted record after formatting, verify json keys
        # Alternatively, verify custom JSONFormatter directly
        from backend.app.logging_config import JSONFormatter, request_id_ctx
        formatter = JSONFormatter()
        token = request_id_ctx.set(req_id)
        try:
            log_record = logging.LogRecord("claimlens.test", logging.INFO, "test.py", 10, "Test log message", (), None)
            formatted_json = json.loads(formatter.format(log_record))
            assert formatted_json["request_id"] == req_id
            assert formatted_json["level"] == "INFO"
            assert formatted_json["message"] == "Test log message"
            assert "ts" in formatted_json
        finally:
            request_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_delete_case_success_and_unknown_case(setup_test_environment):
    engine, upload_dir = setup_test_environment
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload a case (immediately starts pipeline task, setting case_id in _running)
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 sample policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        # Test 409 CASE_BUSY while pipeline is running
        if case_id in pipeline_module._running:
            busy_res = await client.delete(f"/api/cases/{case_id}")
            assert busy_res.status_code == 409
            assert busy_res.json()["error"]["code"] == "CASE_BUSY"

        # Wait until background pipeline tasks complete/pause (reaches AWAITING_FACTS)
        while case_id in pipeline_module._running:
            await asyncio.sleep(0.05)

        # Get document IDs
        case_detail = (await client.get(f"/api/cases/{case_id}")).json()
        doc_id = case_detail["documents"][0]["doc_id"]

        # Verify file exists on disk
        file_path = upload_dir / f"{doc_id}.pdf"
        assert file_path.exists()

        # 2. Add an audit log entry for this case to test retention
        with Session(engine) as session:
            audit = AuditLog(
                case_id=case_id,
                finding_id=None,
                actor="user",
                action="INITIAL_REVIEW",
                before_json="{}",
                after_json="{}",
                note="Initial note",
            )
            session.add(audit)
            session.commit()

        # 3. DELETE /api/cases/{case_id}
        del_res = await client.delete(f"/api/cases/{case_id}")
        assert del_res.status_code == 204

        # 4. Assert DB rows deleted (except audit log)
        with Session(engine) as session:
            assert session.exec(select(Case).where(Case.case_id == case_id)).one_or_none() is None
            assert session.exec(select(Document).where(Document.case_id == case_id)).all() == []
            assert session.exec(select(Finding).where(Finding.case_id == case_id)).all() == []
            assert session.exec(select(PipelineEvent).where(PipelineEvent.case_id == case_id)).all() == []

            # Audit log rows REMAIN, plus final CASE_DELETED row
            audit_rows = session.exec(select(AuditLog).where(AuditLog.case_id == case_id)).all()
            assert len(audit_rows) == 2
            actions = [a.action for a in audit_rows]
            assert "INITIAL_REVIEW" in actions
            assert "CASE_DELETED" in actions

            # Audit triggers still protect append-only guarantee for API paths
            with pytest.raises(Exception) as exc:
                session.exec(text("UPDATE audit_log SET note = 'hacked' WHERE case_id = :cid"), params={"cid": case_id})
                session.commit()
            assert "append-only" in str(exc.value)

        # 5. Assert PDF files deleted from disk
        assert not file_path.exists()

        # 6. Assert subsequent GET endpoints return 404
        get_case_res = await client.get(f"/api/cases/{case_id}")
        assert get_case_res.status_code == 404
        assert get_case_res.json()["error"]["code"] == "CASE_NOT_FOUND"

        get_doc_res = await client.get(f"/api/cases/{case_id}/documents/{doc_id}/file")
        assert get_doc_res.status_code == 404
        assert get_doc_res.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        # 7. DELETE unknown case -> 404
        unknown_del_res = await client.delete("/api/cases/c_nonexistent_999")
        assert unknown_del_res.status_code == 404
        assert unknown_del_res.json()["error"]["code"] == "CASE_NOT_FOUND"


@pytest.mark.asyncio
async def test_ttl_purge_expired_cases(setup_test_environment):
    engine, upload_dir = setup_test_environment

    # Create two cases directly in DB
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=48)  # Retention default is 24h

    with Session(engine) as session:
        old_case = Case(case_id="c_expired_001", status="UPLOADED", created_at=old_time)
        old_doc = Document(doc_id="doc_expired_001", case_id="c_expired_001", doc_type="policy")
        new_case = Case(case_id="c_active_002", status="UPLOADED", created_at=now)
        new_doc = Document(doc_id="doc_active_002", case_id="c_active_002", doc_type="policy")

        session.add(old_case)
        session.add(old_doc)
        session.add(new_case)
        session.add(new_doc)
        session.commit()

    # Create dummy files for both
    old_file = upload_dir / "doc_expired_001.pdf"
    new_file = upload_dir / "doc_active_002.pdf"
    old_file.write_bytes(b"%PDF-1.4 old file")
    new_file.write_bytes(b"%PDF-1.4 new file")

    # Run purge
    purged_count = purge_expired_cases(now=now)
    assert purged_count == 1

    # Verify old case and file are gone, new case remains
    with Session(engine) as session:
        assert session.exec(select(Case).where(Case.case_id == "c_expired_001")).one_or_none() is None
        assert session.exec(select(Case).where(Case.case_id == "c_active_002")).one_or_none() is not None

    assert not old_file.exists()
    assert new_file.exists()


@pytest.mark.asyncio
async def test_rate_limiting_upload_and_review(monkeypatch, setup_test_environment):
    limiter.reset()
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_UPLOAD_PER_MIN", 2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 sample policy", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 sample letter", "application/pdf"),
        }

        # Request 1: OK
        res1 = await client.post("/api/cases", files=files)
        assert res1.status_code == 202

        # Request 2: OK
        res2 = await client.post("/api/cases", files=files)
        assert res2.status_code == 202

        # Request 3: Rate limited -> 429
        res3 = await client.post("/api/cases", files=files)
        assert res3.status_code == 429
        assert "Retry-After" in res3.headers
        data = res3.json()
        assert data["error"]["code"] == "RATE_LIMITED"
        assert data["error"]["stage"] is None

        # Reset limiter -> Request 4 succeeds
        limiter.reset()
        res4 = await client.post("/api/cases", files=files)
        assert res4.status_code == 202
