import asyncio
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, Any, List

from sqlmodel import Session, select

import backend.app.db.session as db_session
from backend.app.config import get_settings
from backend.app.db.models import Case, Document, Chunk
from backend.app.services.facts import save_facts, load_facts
from backend.app.services.findings import save_findings
from contracts.schemas import (
    ParsedDocument,
    Chunk as SchemaChunk,
    RejectionExtraction,
    PolicyFacts,
    Finding as SchemaFinding,
    VerifiedFinding,
)

if TYPE_CHECKING:
    from backend.app.services.pipeline import StageContext

logger = logging.getLogger("claimlens.real_stages")


def _load_docs():
    """
    Tries importing `docint.claimlens_docs` first, then falls back to top-level `claimlens_docs`.
    Raises ModuleNotFoundError if neither is importable.
    """
    if "docint.claimlens_docs" in sys.modules and sys.modules["docint.claimlens_docs"] is None:
        raise ModuleNotFoundError("docint.claimlens_docs set to None")
    try:
        import docint.claimlens_docs as claimlens_docs
        if claimlens_docs is None:
            raise ModuleNotFoundError("docint.claimlens_docs is None")
        return claimlens_docs
    except ModuleNotFoundError:
        pass

    if "claimlens_docs" in sys.modules and sys.modules["claimlens_docs"] is None:
        raise ModuleNotFoundError("claimlens_docs set to None")

    import claimlens_docs
    if claimlens_docs is None:
        raise ModuleNotFoundError("claimlens_docs is None")
    return claimlens_docs


def _load_ai():
    """
    Tries importing M3 callables from `ai.claimlens_ai.*` first:
      from ai.claimlens_ai.retrieval import build_index
      from ai.claimlens_ai.investigator import run_investigation
      from ai.claimlens_ai.verifier import run_verification
      from ai.claimlens_ai.drafter import draft_review_request
    Falls back to top-level `claimlens_ai.*` only if the first raises ModuleNotFoundError.
    Returns tuple of (build_index, run_investigation, run_verification, draft_review_request).
    """
    if "ai.claimlens_ai" in sys.modules and sys.modules["ai.claimlens_ai"] is None:
        raise ModuleNotFoundError("ai.claimlens_ai is set to None")

    try:
        from ai.claimlens_ai.retrieval import build_index
        from ai.claimlens_ai.investigator import run_investigation
        from ai.claimlens_ai.verifier import run_verification
        from ai.claimlens_ai.drafter import draft_review_request
        return build_index, run_investigation, run_verification, draft_review_request
    except ModuleNotFoundError:
        pass

    if "claimlens_ai" in sys.modules and sys.modules["claimlens_ai"] is None:
        raise ModuleNotFoundError("claimlens_ai is set to None")

    try:
        from claimlens_ai.retrieval import build_index
        from claimlens_ai.investigator import run_investigation
        from claimlens_ai.verifier import run_verification
        from claimlens_ai.drafter import draft_review_request
        return build_index, run_investigation, run_verification, draft_review_request
    except ModuleNotFoundError:
        pass

    import claimlens_ai
    if claimlens_ai is None:
        raise ModuleNotFoundError("claimlens_ai is None")

    build_index = getattr(claimlens_ai, "build_index", getattr(getattr(claimlens_ai, "retrieval", None), "build_index", None))
    run_investigation = getattr(claimlens_ai, "run_investigation", getattr(getattr(claimlens_ai, "investigator", None), "run_investigation", None))
    run_verification = getattr(claimlens_ai, "run_verification", getattr(getattr(claimlens_ai, "verifier", None), "run_verification", None))
    draft_review_request = getattr(claimlens_ai, "draft_review_request", getattr(getattr(claimlens_ai, "drafter", None), "draft_review_request", None))

    if not all([build_index, run_investigation, run_verification]):
        raise ModuleNotFoundError("Could not resolve claimlens_ai callables")

    return build_index, run_investigation, run_verification, draft_review_request


def make_emit_adapter(ctx: "StageContext"):
    """
    Adapts M3's emit(stage: str, detail: dict) calls to ctx.emit:
    - Builds a short string representation (message, or 'event step/max_steps') capped at 200 chars.
    - Routes {"event": "llm_metrics", ...} to ctx.record_metrics.
    - Never logs/streams document text.
    """
    def emit_cb(*args, **kwargs):
        stage = "RUNNING"
        detail_dict = None

        if len(args) == 1:
            if isinstance(args[0], dict):
                detail_dict = args[0]
            elif isinstance(args[0], str):
                detail_dict = {"message": args[0]}
        elif len(args) >= 2:
            if isinstance(args[0], str):
                stage = args[0]
            if isinstance(args[1], dict):
                detail_dict = args[1]
            elif isinstance(args[1], str):
                detail_dict = {"message": args[1]}

        if "detail" in kwargs:
            d = kwargs["detail"]
            if isinstance(d, dict):
                detail_dict = d
            elif isinstance(d, str):
                detail_dict = {"message": d}

        if not detail_dict:
            return

        # Check for llm_metrics event
        event_name = detail_dict.get("event")
        if event_name == "llm_metrics":
            model = detail_dict.get("model")
            tokens_in = detail_dict.get("tokens_in")
            tokens_out = detail_dict.get("tokens_out")
            if hasattr(ctx, "record_metrics"):
                ctx.record_metrics(model=model, tokens_in=tokens_in, tokens_out=tokens_out)
            return

        # Format short event detail string
        msg = None
        if "message" in detail_dict and detail_dict["message"]:
            msg = str(detail_dict["message"])
        elif "step" in detail_dict and "max_steps" in detail_dict:
            ev = detail_dict.get("event", "step")
            msg = f"{ev} {detail_dict['step']}/{detail_dict['max_steps']}"
        elif "event" in detail_dict:
            ev = str(detail_dict["event"])
            extra = []
            if "step" in detail_dict:
                extra.append(f"step {detail_dict['step']}")
            if "finding_id" in detail_dict:
                extra.append(str(detail_dict["finding_id"]))
            if "outcome" in detail_dict:
                extra.append(str(detail_dict["outcome"]))
            if extra:
                msg = f"{ev} " + " ".join(extra)
            else:
                msg = ev
        else:
            safe_items = [f"{k}: {v}" for k, v in detail_dict.items() if k not in ("text", "content", "document", "chunks", "quote")]
            if safe_items:
                msg = " ".join(safe_items)

        if not msg:
            return

        if len(msg) > 200:
            msg = msg[:200]

        ctx.emit(stage, msg)

    return emit_cb


async def real_extracting_stage(ctx: "StageContext") -> None:
    from backend.app.main import PipelineException
    from backend.app.services import mock_stages

    settings = get_settings()

    # Lazy import of claimlens_docs via helper
    try:
        claimlens_docs = _load_docs()
    except (ImportError, Exception) as exc:
        if settings.ALLOW_MOCK_FALLBACK:
            ctx.emit("FALLBACK", "claimlens_docs not available, using mock stage")
            await mock_stages.mock_extracting_stage(ctx)
            return
        else:
            raise PipelineException(
                code="MODULE_UNAVAILABLE",
                message=f"claimlens_docs package is not installed: {exc}",
                stage="EXTRACTING",
                retryable=False,
                status_code=500,
            )

    # 1. Load documents from DB
    with Session(db_session.engine) as session:
        docs = session.exec(select(Document).where(Document.case_id == ctx.case_id)).all()

    policy_doc = next((d for d in docs if d.doc_type == "policy"), None)
    letter_doc = next((d for d in docs if d.doc_type == "letter"), None)

    if not policy_doc or not letter_doc:
        raise PipelineException(
            code="MISSING_FILE",
            message="Required policy or letter document not found for case",
            stage="EXTRACTING",
            retryable=False,
            status_code=400,
        )

    upload_dir = os.path.abspath(settings.UPLOAD_DIR)
    policy_path = os.path.join(upload_dir, f"{policy_doc.doc_id}.pdf")
    letter_path = os.path.join(upload_dir, f"{letter_doc.doc_id}.pdf")

    # 2. Call ingest_document for policy and letter
    ctx.emit("RUNNING", "Ingesting policy document")
    policy_parsed_raw = await asyncio.to_thread(
        claimlens_docs.ingest_document, policy_path, policy_doc.doc_id, "policy"
    )
    policy_parsed = ParsedDocument.model_validate(policy_parsed_raw)

    ctx.emit("RUNNING", "Ingesting rejection letter")
    letter_parsed_raw = await asyncio.to_thread(
        claimlens_docs.ingest_document, letter_path, letter_doc.doc_id, "letter"
    )
    letter_parsed = ParsedDocument.model_validate(letter_parsed_raw)

    # Check text layer of policy PDF
    if not policy_parsed.has_text_layer:
        raise PipelineException(
            code="NO_TEXT_LAYER",
            message="The policy PDF has no readable text layer.",
            stage="EXTRACTING",
            retryable=False,
            status_code=400,
        )

    # 3. Persist chunks into DB chunks table (idempotent)
    all_chunks = policy_parsed.chunks + letter_parsed.chunks
    doc_ids = [policy_doc.doc_id, letter_doc.doc_id]

    with Session(db_session.engine) as session:
        for did in doc_ids:
            existing_chunks = session.exec(select(Chunk).where(Chunk.doc_id == did)).all()
            for chk in existing_chunks:
                session.delete(chk)

        for chk in all_chunks:
            db_chunk = Chunk(
                chunk_id=chk.chunk_id,
                doc_id=chk.doc_id,
                section_path=chk.section_path,
                page=chk.page,
                text=chk.text,
                bbox_json=json.dumps(chk.bbox),
                tags_json=json.dumps(chk.tags or []),
            )
            session.add(db_chunk)
        session.commit()

    # 4. Extract rejection & policy facts
    ctx.emit("RUNNING", "Extracting rejection reasons from letter")
    rejection_raw = await asyncio.to_thread(claimlens_docs.extract_rejection, letter_parsed)
    rejection = RejectionExtraction.model_validate(rejection_raw)

    ctx.emit("RUNNING", "Extracting policy facts from policy document")
    facts_raw = await asyncio.to_thread(claimlens_docs.extract_policy_facts, policy_parsed)
    facts = PolicyFacts.model_validate(facts_raw)

    # Save rejection to Case.rejection_json
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == ctx.case_id)).one_or_none()
        if case:
            case.rejection_json = rejection.model_dump_json()
            session.add(case)
            session.commit()

    # Save policy facts with confirmed=False
    save_facts(ctx.case_id, facts, confirmed=False)

    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="real_docs")


async def real_investigating_stage(ctx: "StageContext") -> None:
    from backend.app.main import PipelineException
    from backend.app.services import mock_stages

    settings = get_settings()

    # Lazy import of claimlens_ai via helper
    try:
        build_index, run_investigation, _, _ = _load_ai()
    except (ImportError, Exception) as exc:
        if settings.ALLOW_MOCK_FALLBACK:
            ctx.emit("FALLBACK", "claimlens_ai not available, using mock stage")
            await mock_stages.mock_investigating_stage(ctx)
            return
        else:
            raise PipelineException(
                code="MODULE_UNAVAILABLE",
                message=f"claimlens_ai package is not installed: {exc}",
                stage="INVESTIGATING",
                retryable=False,
                status_code=500,
            )

    # 1. Load chunks for case from DB
    with Session(db_session.engine) as session:
        docs = session.exec(select(Document).where(Document.case_id == ctx.case_id)).all()
        doc_ids = [d.doc_id for d in docs]
        db_chunks = session.exec(select(Chunk).where(Chunk.doc_id.in_(doc_ids))).all()

    schema_chunks = []
    for c in db_chunks:
        try:
            bbox = json.loads(c.bbox_json or "[]")
        except Exception:
            bbox = []
        try:
            tags = json.loads(c.tags_json or "[]")
        except Exception:
            tags = []
        schema_chunks.append(
            SchemaChunk(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                section_path=c.section_path,
                page=c.page,
                text=c.text,
                bbox=bbox,
                tags=tags,
            )
        )

    # 2. Build index
    ctx.emit("RUNNING", "Building vector/keyword index over document chunks")
    await asyncio.to_thread(build_index, ctx.case_id, schema_chunks)

    # 3. Load rejection extraction and confirmed facts
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == ctx.case_id)).one_or_none()
        rejection_str = case.rejection_json if case else None

    if not rejection_str:
        raise PipelineException(
            code="MISSING_DATA",
            message="Rejection extraction data missing for investigation",
            stage="INVESTIGATING",
            retryable=False,
            status_code=400,
        )

    rejection = RejectionExtraction.model_validate_json(rejection_str)
    facts = load_facts(ctx.case_id)
    if not facts:
        facts = PolicyFacts()

    emit_cb = make_emit_adapter(ctx)

    # 4. Run investigation
    ctx.emit("RUNNING", "Running LLM investigation across rejection reasons")
    raw_findings = await asyncio.to_thread(
        run_investigation, case_id=ctx.case_id, rejection=rejection, facts=facts, emit=emit_cb
    )

    findings = [SchemaFinding.model_validate(f) for f in raw_findings]
    findings_json = json.dumps([f.model_dump(mode="json") for f in findings])

    # 5. Save unverified findings to Case.unverified_findings_json (idempotent)
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == ctx.case_id)).one_or_none()
        if case:
            case.unverified_findings_json = findings_json
            session.add(case)
            session.commit()

    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="real_ai")


async def real_verifying_stage(ctx: "StageContext") -> None:
    from backend.app.main import PipelineException
    from backend.app.services import mock_stages

    settings = get_settings()

    # Lazy import of claimlens_ai via helper
    try:
        _, _, run_verification, _ = _load_ai()
    except (ImportError, Exception) as exc:
        if settings.ALLOW_MOCK_FALLBACK:
            ctx.emit("FALLBACK", "claimlens_ai not available, using mock stage")
            await mock_stages.mock_verifying_stage(ctx)
            return
        else:
            raise PipelineException(
                code="MODULE_UNAVAILABLE",
                message=f"claimlens_ai package is not installed: {exc}",
                stage="VERIFYING",
                retryable=False,
                status_code=500,
            )

    # 1. Load stored unverified findings from Case.unverified_findings_json
    with Session(db_session.engine) as session:
        case = session.exec(select(Case).where(Case.case_id == ctx.case_id)).one_or_none()
        findings_json_str = case.unverified_findings_json if case else None

    if not findings_json_str:
        raise PipelineException(
            code="MISSING_DATA",
            message="Unverified findings data missing for verification stage",
            stage="VERIFYING",
            retryable=False,
            status_code=400,
        )

    raw_findings_list = json.loads(findings_json_str)
    unverified_findings = [SchemaFinding.model_validate(f) for f in raw_findings_list]

    emit_cb = make_emit_adapter(ctx)

    # 2. Run verification
    ctx.emit("RUNNING", "Running verification & challenge check on findings")
    raw_verified = await asyncio.to_thread(
        run_verification, case_id=ctx.case_id, findings=unverified_findings, emit=emit_cb
    )

    verified_findings = [VerifiedFinding.model_validate(vf) for vf in raw_verified]

    # 3. Save findings using existing save_findings
    save_findings(ctx.case_id, verified_findings)

    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="real_ai")
