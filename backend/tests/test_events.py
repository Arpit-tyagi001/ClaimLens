import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, create_engine, Session, select

import backend.app.db.session as db_session
import backend.app.services.pipeline as pipeline_module
import backend.app.api.events as events_module
from backend.app.services.pipeline import emit_event, Stage, StageStatus
from backend.app.db.models import Case, PipelineEvent
from backend.app.main import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_claimlens_events.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )

    orig_engine = db_session.engine
    orig_pipe_engine = pipeline_module.engine
    orig_events_engine = events_module.engine

    db_session.engine = test_engine
    pipeline_module.engine = test_engine
    events_module.engine = test_engine

    SQLModel.metadata.create_all(test_engine)

    events_module.POLL_INTERVAL_S = 0.05
    events_module.KEEP_ALIVE_INTERVAL_S = 15.0

    yield test_engine

    db_session.engine = orig_engine
    pipeline_module.engine = orig_pipe_engine
    events_module.engine = orig_events_engine


def parse_sse_frames(text: str) -> list[dict]:
    frames = []
    blocks = text.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        frame_dict = {}
        for line in lines:
            line_s = line.strip()
            if line_s.startswith("id: "):
                frame_dict["id"] = int(line_s[4:].strip())
            elif line_s.startswith("event: "):
                frame_dict["event"] = line_s[7:].strip()
            elif line_s.startswith("data: "):
                frame_dict["data"] = json.loads(line_s[6:].strip())
            elif line_s.startswith(":"):
                frame_dict["comment"] = line_s[1:].strip()
            elif line_s.startswith("retry: "):
                frame_dict["retry"] = line_s[7:].strip()
        if frame_dict:
            frames.append(frame_dict)
    return frames


@pytest.mark.asyncio
async def test_pre_inserted_events(setup_test_db):
    case_id = "c_pre"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="UPLOADED"))
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    emit_event(case_id, Stage.EXTRACTING, StageStatus.RUNNING, "Extracting text")
    emit_event(case_id, Stage.EXTRACTING, StageStatus.COMPLETED, "Extracted text")
    emit_event(case_id, Stage.READY_FOR_REVIEW, StageStatus.COMPLETED, "Terminal review")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/cases/{case_id}/events")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        frames = parse_sse_frames(response.text)
        data_frames = [f for f in frames if "data" in f]
        assert len(data_frames) == 4

        seqs = [f["id"] for f in data_frames]
        assert seqs == [1, 2, 3, 4]

        # Verify JSON shape of frame 1
        d1 = data_frames[0]["data"]
        assert d1["seq"] == 1
        assert d1["stage"] == Stage.UPLOADED.value
        assert d1["status"] == StageStatus.COMPLETED.value
        assert d1["detail"] == "Uploaded"
        assert "ts" in d1


@pytest.mark.asyncio
async def test_last_event_id_resume(setup_test_db):
    case_id = "c_resume"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="UPLOADED"))
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    emit_event(case_id, Stage.EXTRACTING, StageStatus.RUNNING, "Extracting text")
    emit_event(case_id, Stage.EXTRACTING, StageStatus.COMPLETED, "Extracted text")
    emit_event(case_id, Stage.READY_FOR_REVIEW, StageStatus.COMPLETED, "Terminal review")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/cases/{case_id}/events",
            headers={"Last-Event-ID": "2"},
        )
        assert response.status_code == 200
        frames = parse_sse_frames(response.text)
        data_frames = [f for f in frames if "data" in f]
        ids = [f["id"] for f in data_frames]
        assert ids == [3, 4]


@pytest.mark.asyncio
async def test_new_events_delivered_while_connected(setup_test_db):
    case_id = "c_live"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="UPLOADED"))
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        async def insert_later():
            await asyncio.sleep(0.1)
            emit_event(case_id, Stage.INVESTIGATING, StageStatus.RUNNING, "Investigating")
            await asyncio.sleep(0.1)
            emit_event(case_id, Stage.READY_FOR_REVIEW, StageStatus.COMPLETED, "Done")

        task = asyncio.create_task(insert_later())
        response = await client.get(f"/api/cases/{case_id}/events")
        await task

        assert response.status_code == 200
        frames = parse_sse_frames(response.text)
        data_frames = [f for f in frames if "data" in f]
        ids = [f["id"] for f in data_frames]
        assert ids == [1, 2, 3]


@pytest.mark.asyncio
async def test_stream_closes_on_terminal_events(setup_test_db):
    case_id_success = "c_term_success"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id_success, status="UPLOADED"))
        session.commit()

    emit_event(case_id_success, Stage.READY_FOR_REVIEW, StageStatus.COMPLETED, "Review ready")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.get(f"/api/cases/{case_id_success}/events")
        assert resp1.status_code == 200
        frames1 = parse_sse_frames(resp1.text)
        assert len([f for f in frames1 if "data" in f]) == 1

    case_id_failed = "c_term_failed"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id_failed, status="UPLOADED"))
        session.commit()

    emit_event(case_id_failed, Stage.VERIFYING, StageStatus.FAILED, "Verifying failed")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp2 = await client.get(f"/api/cases/{case_id_failed}/events")
        assert resp2.status_code == 200
        frames2 = parse_sse_frames(resp2.text)
        assert len([f for f in frames2 if "data" in f]) == 1
        assert frames2[-1]["data"]["status"] == StageStatus.FAILED.value


@pytest.mark.asyncio
async def test_unknown_case_id_returns_404(setup_test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/cases/c_nonexistent/events")
        assert response.status_code == 404
        data = response.json()
        assert data == {
            "error": {
                "code": "CASE_NOT_FOUND",
                "message": "Case c_nonexistent not found",
                "stage": None,
            }
        }


@pytest.mark.asyncio
async def test_keep_alive_comments(setup_test_db):
    case_id = "c_ka"
    with Session(setup_test_db) as session:
        session.add(Case(case_id=case_id, status="UPLOADED"))
        session.commit()

    emit_event(case_id, Stage.UPLOADED, StageStatus.COMPLETED, "Uploaded")
    events_module.KEEP_ALIVE_INTERVAL_S = 0.1

    async def insert_terminal_later():
        await asyncio.sleep(0.25)
        emit_event(case_id, Stage.READY_FOR_REVIEW, StageStatus.COMPLETED, "Done")

    task = asyncio.create_task(insert_terminal_later())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/cases/{case_id}/events")
        await task
        assert response.status_code == 200
        assert ": keep-alive" in response.text
