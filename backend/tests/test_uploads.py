import asyncio
import os
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.cases as cases_module
import backend.app.api.events as events_module
import backend.app.api.facts as facts_module
import backend.app.services.upload_validation as upload_val_module
from backend.app.db.models import Case, Document, PipelineEvent
from backend.app.main import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_claimlens_uploads.db"
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
    orig_max_bytes = upload_val_module.MAX_UPLOAD_BYTES

    db_session.engine = test_engine
    pipeline_module.engine = test_engine
    events_module.engine = test_engine
    cases_module.UPLOAD_DIR = str(test_upload_dir)
    upload_val_module.MAX_UPLOAD_BYTES = 10 * 1024 * 1024

    SQLModel.metadata.create_all(test_engine)

    yield test_engine

    db_session.engine = orig_engine
    pipeline_module.engine = orig_pipe_engine
    events_module.engine = orig_events_engine
    cases_module.UPLOAD_DIR = orig_upload_dir
    upload_val_module.MAX_UPLOAD_BYTES = orig_max_bytes
    pipeline_module.STAGE_REGISTRY.clear()


@pytest.mark.asyncio
async def test_valid_pdfs_upload(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 valid policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 valid letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        data = res.json()
        assert "case_id" in data
        assert data["status"] == "UPLOADED"


@pytest.mark.asyncio
async def test_non_pdf_file_rejected(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"Hello world text file", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 valid letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 415
        data = res.json()
        assert data["error"]["code"] == "INVALID_FILE_TYPE"
        assert data["error"]["stage"] == "UPLOADED"

        # Assert NOTHING stored in DB or filesystem
        with Session(setup_test_db) as session:
            cases = session.exec(select(Case)).all()
            assert len(cases) == 0

        upload_files = os.listdir(cases_module.UPLOAD_DIR)
        assert len(upload_files) == 0


@pytest.mark.asyncio
async def test_file_over_limit_rejected(setup_test_db):
    upload_val_module.MAX_UPLOAD_BYTES = 50

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        huge_data = b"%PDF-1.4 " + b"X" * 100
        files = {
            "policy": ("policy.pdf", huge_data, "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 short letter", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 413
        data = res.json()
        assert data["error"]["code"] == "FILE_TOO_LARGE"

        with Session(setup_test_db) as session:
            cases = session.exec(select(Case)).all()
            assert len(cases) == 0


@pytest.mark.asyncio
async def test_empty_file_and_missing_file_rejected(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Empty file
        files_empty = {
            "policy": ("policy.pdf", b"", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter", "application/pdf"),
        }
        res_empty = await client.post("/api/cases", files=files_empty)
        assert res_empty.status_code == 400
        assert res_empty.json()["error"]["code"] == "EMPTY_FILE"

        # Missing letter part
        files_missing = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy", "application/pdf"),
        }
        res_missing = await client.post("/api/cases", files=files_missing)
        assert res_missing.status_code == 422
        assert res_missing.json()["error"]["code"] == "MISSING_FILE"
        assert "letter" in res_missing.json()["error"]["message"]


@pytest.mark.asyncio
async def test_one_valid_one_invalid_rejected_completely(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 valid policy", "application/pdf"),
            "letter": ("letter.pdf", b"Invalid text letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 415
        assert res.json()["error"]["code"] == "INVALID_FILE_TYPE"

        # Assert no DB rows or files on disk
        with Session(setup_test_db) as session:
            cases = session.exec(select(Case)).all()
            assert len(cases) == 0
        assert len(os.listdir(cases_module.UPLOAD_DIR)) == 0


@pytest.mark.asyncio
async def test_get_document_file(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy pdf data", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter pdf data", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 202
        case_id = res.json()["case_id"]

        with Session(setup_test_db) as session:
            docs = session.exec(select(Document).where(Document.case_id == case_id)).all()
            assert len(docs) == 2
            doc_id = docs[0].doc_id

        # GET valid document file
        file_res = await client.get(f"/api/cases/{case_id}/documents/{doc_id}/file")
        assert file_res.status_code == 200
        assert file_res.headers["content-type"] == "application/pdf"
        assert file_res.content.startswith(b"%PDF-")
        assert "inline;" in file_res.headers["content-disposition"]
        assert "private" in file_res.headers["cache-control"]

        # GET with wrong case_id for real doc_id -> 404 DOCUMENT_NOT_FOUND
        file_wrong_case = await client.get(f"/api/cases/c_wrong/documents/{doc_id}/file")
        assert file_wrong_case.status_code == 404
        assert file_wrong_case.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        # GET unknown doc_id -> 404 DOCUMENT_NOT_FOUND
        file_unknown_doc = await client.get(f"/api/cases/{case_id}/documents/doc_unknown/file")
        assert file_unknown_doc.status_code == 404
        assert file_unknown_doc.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        # Path traversal attempt -> 404 DOCUMENT_NOT_FOUND
        file_traversal = await client.get(f"/api/cases/{case_id}/documents/../../etc/passwd/file")
        assert file_traversal.status_code == 404


@pytest.mark.asyncio
async def test_unknown_route_and_wrong_method(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unknown route -> 404 uniform shape
        res_404 = await client.get("/api/nonexistent-route-xyz")
        assert res_404.status_code == 404
        assert res_404.json()["error"]["code"] == "NOT_FOUND"
        assert res_404.json()["error"]["stage"] is None

        # Wrong method -> 405 uniform shape
        res_405 = await client.delete("/api/cases")
        assert res_405.status_code == 405
        assert res_405.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
        assert res_405.json()["error"]["stage"] is None


@pytest.mark.asyncio
async def test_unexpected_exception_500_uniform_shape(setup_test_db, monkeypatch):
    def crash_upload(*args, **kwargs):
        raise RuntimeError("Unexpected internal crash at /path/to/secret.py")

    monkeypatch.setattr(cases_module, "_save_case_to_db", crash_upload)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        assert res.status_code == 500
        assert "x-request-id" in res.headers
        data = res.json()
        assert data == {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong.",
                "stage": None,
            }
        }
        text = str(data)
        assert "Traceback" not in text
        assert "secret.py" not in text


@pytest.mark.asyncio
async def test_get_case_detail_includes_file_url(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "policy": ("policy.pdf", b"%PDF-1.4 policy content", "application/pdf"),
            "letter": ("letter.pdf", b"%PDF-1.4 letter content", "application/pdf"),
        }
        res = await client.post("/api/cases", files=files)
        case_id = res.json()["case_id"]

        detail_res = await client.get(f"/api/cases/{case_id}")
        assert detail_res.status_code == 200
        data = detail_res.json()

        assert "documents" in data
        assert len(data["documents"]) == 2
        for doc in data["documents"]:
            assert "file_url" in doc
            assert doc["file_url"].startswith(f"/api/cases/{case_id}/documents/")
            assert doc["file_url"].endswith("/file")

        text_content = str(data)
        assert "uploads" not in text_content
        assert ".pdf" not in text_content
