from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any
from datetime import date

class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    section_path: str
    page: int
    text: str
    bbox: List[float] # [x0,y0,x1,y1] in PDF points
    tags: List[str] = []

class ParsedDocument(BaseModel):
    doc_id: str
    doc_type: Literal["policy", "letter"]
    n_pages: int
    has_text_layer: bool
    chunks: List[Chunk]

class RejectionReason(BaseModel):
    reason_id: str
    text: str
    category: str
    page: int
    bbox: List[float]

class RejectionExtraction(BaseModel):
    claim_no: Optional[str] = None
    policy_no: Optional[str] = None
    admission_date: Optional[date] = None
    discharge_date: Optional[date] = None
    claimed_amount: Optional[float] = None
    rejected_amount: Optional[float] = None
    reasons: List[RejectionReason]

class Evidence(BaseModel):
    chunk_id: str
    quote: str
    page: int
    section_path: str
    
class Fact(BaseModel):
    name: str
    value: Any
    source: Optional[str] = None


class RuleCheck(BaseModel):
    rule: str
    passed: bool
    details: str


class CitationCheck(BaseModel):
    chunk_id: str
    grounded: bool
    details: str


class FactCheck(BaseModel):
    fact: str
    passed: bool
    details: str


class Draft(BaseModel):
    case_id: str
    text: str
    finding_ids: List[str]

class Finding(BaseModel):
    finding_id: str
    reason_id: str
    assessment: Literal["SUPPORTED", "NOT_SUPPORTED", "PARTIAL", "INSUFFICIENT_EVIDENCE"]
    confidence: float
    reasoning: str
    evidence: List[Evidence]
    facts_used: List[Fact] = [] # Placeholder for now
    rule_checks: List[RuleCheck] = [] # Placeholder for now

class Challenge(BaseModel):
    round: int
    attacker_argument: str
    attacker_chunk_ids: List[str]
    rebuttal: Optional[str] = None
    outcome: Literal["UPHELD", "WEAKENED", "OVERTURNED"]

class VerifiedFinding(Finding):
    citation_checks: List[CitationCheck] = [] 
    fact_checks: List[FactCheck] = []
    challenges: List[Challenge]
    status: Literal["VERIFIED", "DOWNGRADED", "NEEDS_HUMAN", "REJECTED_UNGROUNDED"]
    final_assessment: str
    final_confidence: float