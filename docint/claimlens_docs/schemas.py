"""Pydantic models returned by claimlens_docs.

Every model that also exists in contracts/schemas.py SUBCLASSES the contract
class. The backend calls contracts.schemas.ParsedDocument.model_validate(...)
on what we return, and Pydantic v2 accepts an instance of a subclass but
rejects an instance of an unrelated class with the same fields. Fields we add
are optional, which the contract rules allow.

If contracts/ is not importable (running this package on its own), local
copies of the contract classes are used instead.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

BBox = list[float]  # [x0, y0, x1, y1] in PDF points, origin top-left


# --- Local copies of the contract classes, used only when contracts/ is missing ---

class _Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    section_path: str
    page: int
    text: str
    bbox: BBox
    tags: list[str] = []


class _ParsedDocument(BaseModel):
    doc_id: str
    doc_type: Literal["policy", "letter"]
    n_pages: int
    has_text_layer: bool
    chunks: list[_Chunk]


class _RejectionReason(BaseModel):
    reason_id: str
    text: str
    category: str
    page: int
    bbox: BBox


class _RejectionExtraction(BaseModel):
    claim_no: str | None = None
    policy_no: str | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    claimed_amount: float | None = None
    rejected_amount: float | None = None
    reasons: list[_RejectionReason]


class _WaitingPeriod(BaseModel):
    kind: str
    months: int
    chunk_id: str | None = None


class _PolicyFacts(BaseModel):
    policy_start: date | None = None
    policy_end: date | None = None
    sum_insured: float | None = None
    waiting_periods: list[_WaitingPeriod] = []
    confirmed_by_user: bool = False


try:
    from contracts import schemas as _contract
except ImportError:  # running outside the monorepo
    _contract = None  # type: ignore[assignment]


def _base(name: str, local: type[BaseModel]) -> type[BaseModel]:
    return getattr(_contract, name, local) if _contract is not None else local


ChunkBase = _base("Chunk", _Chunk)
ParsedDocumentBase = _base("ParsedDocument", _ParsedDocument)
RejectionReasonBase = _base("RejectionReason", _RejectionReason)
RejectionExtractionBase = _base("RejectionExtraction", _RejectionExtraction)
WaitingPeriodBase = _base("WaitingPeriod", _WaitingPeriod)
PolicyFactsBase = _base("PolicyFacts", _PolicyFacts)


# --- What claimlens_docs returns ---

class Chunk(ChunkBase):  # type: ignore[valid-type,misc]
    pass


class ParsedDocument(ParsedDocumentBase):  # type: ignore[valid-type,misc]
    chunks: list[Chunk]  # type: ignore[assignment]
    # Optional additions
    pages_without_text: list[int] = []


class RejectionReason(RejectionReasonBase):  # type: ignore[valid-type,misc]
    # Optional additions
    clause_refs: list[str] = []


class RejectionExtraction(RejectionExtractionBase):  # type: ignore[valid-type,misc]
    reasons: list[RejectionReason] = []  # type: ignore[assignment]
    # Optional additions
    letter_date: date | None = None


class WaitingPeriod(WaitingPeriodBase):  # type: ignore[valid-type,misc]
    # kind: initial, specified_disease, pre_existing. A "30 days" wait is
    # rounded to months=1; use days when it is set.
    # Optional additions
    days: int | None = None


class PolicyFacts(PolicyFactsBase):  # type: ignore[valid-type,misc]
    waiting_periods: list[WaitingPeriod] = []  # type: ignore[assignment]
    # Optional additions
    policy_no: str | None = None
    first_inception_date: date | None = None  # waiting periods count from this date
    room_rent_limit_per_day: float | None = None
    source_chunk_ids: dict[str, str] = {}  # fact name -> chunk it was read from
    notes: list[str] = []


# --- claimlens_docs only (not in the contract) ---

class LocateResult(BaseModel):
    found: bool
    match_score: float  # 0..1
    page: int | None = None
    bbox: BBox | None = None
    chunk_id: str | None = None
    matched_text: str | None = None
    reason: str | None = None  # why a close match was still rejected


class InjectionFlag(BaseModel):
    pattern: str
    start: int
    end: int
    snippet: str


class SanitizedText(BaseModel):
    text: str  # PII masked; send this, wrapped by `delimited`, to the LLM
    delimited: str  # text wrapped in data delimiters, ready to paste into a prompt
    redactions: dict[str, int] = Field(default_factory=dict)
    injection_flags: list[InjectionFlag] = []

    @property
    def suspicious(self) -> bool:
        return bool(self.injection_flags)
