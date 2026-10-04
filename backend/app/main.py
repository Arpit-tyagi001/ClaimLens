from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid
import time
import os
from contextlib import asynccontextmanager

# Import the DB initialization function
from backend.app.db.session import create_db_and_tables


# custom exception for pipeline Errors required by PRD
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
    # Create the SQLite tables on startup
    create_db_and_tables()
    from backend.app.services.pipeline import recover_incomplete_cases
    recover_incomplete_cases()
    yield


# Add the lifespan to the app factory
app = FastAPI(title="ClaimLens API", version="1.0", lifespan=lifespan)

# allowing front end to hit API (Crucial for member 2)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# uniform error handler
@app.exception_handler(PipelineException)
async def pipeline_exception_handler(request: Request, exc: PipelineException):
    status_code = getattr(exc, "status_code", 400)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code, "message": exc.message, "stage": exc.stage}},
    )


# Request ID and Middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)
    return response


# basic health check to prove running server
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}


from backend.app.api import cases, events

app.include_router(cases.router)
app.include_router(events.router)

env = os.getenv("ENV", "dev")
if env != "production":
    from backend.app.api import debug
    app.include_router(debug.router)