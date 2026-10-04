from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import Optional
import uuid
import time
import os
import logging
from contextlib import asynccontextmanager

from backend.app.db.session import create_db_and_tables

logger = logging.getLogger("claimlens.main")


class PipelineException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        stage: Optional[str] = None,
        status_code: int = 400,
        retryable: bool = False,
    ):
        self.code = code
        self.message = message
        self.stage = stage
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    from backend.app.services.pipeline import recover_incomplete_cases
    recover_incomplete_cases()
    yield


app = FastAPI(title="ClaimLens API", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.time()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(f"[{request_id}] Exception during request handling: {exc}", exc_info=exc)
        response = JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong.", "stage": None}},
        )
    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)
    return response


# 1. PipelineException Handler
@app.exception_handler(PipelineException)
async def pipeline_exception_handler(request: Request, exc: PipelineException):
    status_code = getattr(exc, "status_code", 400)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code, "message": exc.message, "stage": exc.stage}},
    )


# 2. RequestValidationError Handler (FastAPI / Pydantic validation)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    missing_or_invalid = []
    for err in errors:
        loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
        field_str = ".".join(loc) if loc else "field"
        msg = err.get("msg", "invalid")
        missing_or_invalid.append(f"{field_str}: {msg}")

    summary = "; ".join(missing_or_invalid) if missing_or_invalid else "Invalid request payload"

    path = request.url.path
    stage = None
    if "/api/cases" in path:
        if "policy-facts" in path:
            stage = "AWAITING_FACTS"
        elif path.rstrip("/") == "/api/cases":
            stage = "UPLOADED"

    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": summary, "stage": stage}},
    )


# 3. StarletteHTTPException Handler (404, 405, etc.)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code_map = {
        400: "BAD_REQUEST",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "FILE_TOO_LARGE",
        415: "INVALID_FILE_TYPE",
        422: "VALIDATION_ERROR",
        500: "INTERNAL_ERROR",
    }
    code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = str(exc.detail) if exc.detail else "HTTP error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message, "stage": None}},
    )


# 4. Catch-all Exception Handler
@app.exception_handler(Exception)
async def catchall_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(f"[{request_id}] Unhandled Exception: {exc}", exc_info=exc)
    response = JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong.", "stage": None}},
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/healthz")
async def health_check():
    return {"status": "ok"}


from backend.app.api import cases, events, facts

app.include_router(cases.router)
app.include_router(events.router)
app.include_router(facts.router)

env = os.getenv("ENV", "dev")
if env != "production":
    from backend.app.api import debug
    app.include_router(debug.router)