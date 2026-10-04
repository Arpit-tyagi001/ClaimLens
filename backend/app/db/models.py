from sqlmodel import SQLModel, Field, UniqueConstraint
from typing import Optional
from datetime import datetime, timezone
import json

def utc_now():
    return datetime.now(timezone.utc)

class Case(SQLModel, table=True):
    __tablename__ = "cases"
    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: str = Field(index=True, unique=True)
    status: str = Field(default="UPLOADED")
    facts_confirmed: bool = Field(default=False)
    facts_json: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)

class Document(SQLModel, table=True):
    __tablename__ = "documents"
    id: Optional[int] = Field(default=None, primary_key=True)
    doc_id: str = Field(index=True, unique=True)
    case_id: str = Field(foreign_key="cases.case_id")
    doc_type: str 
    filename: Optional[str] = Field(default=None)
    n_pages: Optional[int] = None
    has_text_layer: bool = True

class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"
    id: Optional[int] = Field(default=None, primary_key=True)
    chunk_id: str = Field(index=True, unique=True)
    doc_id: str = Field(foreign_key="documents.doc_id")
    section_path: str
    page: int
    text: str
    bbox_json: str = Field(default="[]") 
    tags_json: str = Field(default="[]")

class Finding(SQLModel, table=True):
    __tablename__ = "findings"
    id: Optional[int] = Field(default=None, primary_key=True)
    finding_id: str = Field(index=True, unique=True)
    case_id: str = Field(foreign_key="cases.case_id")
    reason_id: str
    assessment: str
    confidence: float
    reasoning: str
    status: str = Field(default="PENDING_VERIFICATION")
    evidence_json: str = Field(default="[]")

class PipelineEvent(SQLModel, table=True):
    __tablename__ = "pipeline_events"
    __table_args__ = (UniqueConstraint("case_id", "seq", name="uq_case_id_seq"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="cases.case_id", index=True)
    seq: int = Field(index=True)
    stage: str
    status: str 
    detail: str
    ts: datetime = Field(default_factory=utc_now)