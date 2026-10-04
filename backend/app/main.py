from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uuid
import time
from contextlib import asynccontextmanager


# Import the DB initialization function
from backend.app.db.session import create_db_and_tables

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the SQLite tables on startup
    create_db_and_tables()
    yield

# Add the lifespan to the app factory
app = FastAPI(title="ClaimLens API", version="1.0", lifespan=lifespan)

#allowing front end to hit API (Crucial for member 2)
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

#custom exception for pipeline Errors required by PRD
class PipelineException(Exception):
    def __init__(self, code:str, message: str, stage: str):
        self.code = code
        self.message = message
        self.stage = stage
#uniform error handler
@app.exception_handler(PipelineException)
async def pipeline_exception_handler(request: Request, exc: PipelineException):
    return JSONResponse(
        status_code=400,
        content={"error": {"code":exc.code, "message": exc.message, "stage": exc.stage}},
    )

#Request ID and Middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)
    return response
#basic health check to prove running server
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

from backend.app.api import cases

app.include_router(cases.router)