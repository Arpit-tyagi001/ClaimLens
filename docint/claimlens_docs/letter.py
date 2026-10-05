"""Regex and heuristic extraction from a rejection letter.

Every reason keeps its character span (start, end) in the original text so
the UI can highlight exactly the sentence the insurer wrote.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

from .parsers import parse_indian_date, parse_inr_amount
from .schemas import ParsedDocument, RejectionExtraction, RejectionReason

# MVP categories from the PRD. Anything else goes to a human.
WAITING_PERIOD = "waiting_period"
PRE_EXISTING = "pre_existing"
EXCLUSION = "exclusion"
MISSING_DOCUMENTS = "missing_documents"
LIMIT_EXCEEDED = "limit_exceeded"
NEEDS_HUMAN = "needs_human"

# Checked in this order; the first category with a matching keyword wins.
# Pre-existing comes before waiting period because PED letters often say
# "pre-existing disease waiting period".
CATEGORY_KEYWORDS = [
    (PRE_EXISTING, [r"pre[\s-]?existing", r"\bPED\b"]),
    (WAITING_PERIOD, [r"waiting period"]),
    (MISSING_DOCUMENTS, [r"document", r"not (?:been )?(?:submitted|received|provided)"]),
    (LIMIT_EXCEEDED, [r"sub[\s-]?limit", r"\blimit", r"room rent", r"capped", r"exceed"]),
    (EXCLUSION, [r"exclu", r"not covered", r"not payable under"]),
]

DATE_TEXT = r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?[\s\-][A-Za-z]{3,9}\.?[\s\-,]*\d{4}"
AMOUNT_TEXT = r"(?:Rs\.?|INR|₹)\s*[0-9][0-9,]*(?:\.\d{1,2})?(?:\s*/-)?"

FIELD_PATTERNS = {
    "claim_number": re.compile(r"Claim\s*(?:No\b\.?|Number|ID\b)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]+)", re.I),
    "policy_number": re.compile(r"Policy\s*(?:No\b\.?|Number)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]+)", re.I),
    "letter_date": re.compile(rf"^\s*Date\s*[:\-]\s*({DATE_TEXT})", re.I | re.M),
    "admission_date": re.compile(rf"Date\s+of\s+Admission\s*[:\-]\s*({DATE_TEXT})", re.I),
    "discharge_date": re.compile(rf"Date\s+of\s+Discharge\s*[:\-]\s*({DATE_TEXT})", re.I),
    "amount_claimed": re.compile(rf"Amount\s+Claimed\s*[:\-]\s*({AMOUNT_TEXT})", re.I),
    "amount_rejected": re.compile(
        r"(?:Amount\s+(?:Rejected|Disallowed|Deducted)|(?:Rejected|Disallowed)\s+Amount)"
        rf"\s*[:\-]\s*({AMOUNT_TEXT})",
        re.I,
    ),
}

REASON_HEADER = re.compile(r"^.*\breasons?\b.*:\s*$", re.I | re.M)
NUMBERED_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|\([a-z0-9]\)|[a-z][.)])\s+", re.I | re.M)
# A sentence may wrap onto the next line (PDF text always does), but never
# crosses a blank line.
_SENTENCE_CHAR = r"(?:[^.\n]|\n(?!\s*\n))"
TRIGGER_SENTENCE = re.compile(
    rf"{_SENTENCE_CHAR}*\b(?:repudiated|rejected|denied|not payable|not admissible)\b{_SENTENCE_CHAR}*\.", re.I
)
CLAUSE_REF = re.compile(r"\b(?:Clause|Section)\s+(\d+(?:\.\d+)*)", re.I)


@dataclass
class Reason:
    text: str
    start: int
    end: int
    category: str
    clause_refs: list = field(default_factory=list)


@dataclass
class LetterExtraction:
    claim_number: str | None = None
    policy_number: str | None = None
    letter_date: date | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    amount_claimed: Decimal | None = None
    amount_rejected: Decimal | None = None
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for key, value in d.items():
            if isinstance(value, (date, Decimal)):
                d[key] = str(value)
        return d


def classify_reason(text: str) -> str:
    for category, patterns in CATEGORY_KEYWORDS:
        if any(re.search(p, text, re.I) for p in patterns):
            return category
    return NEEDS_HUMAN


def _make_reason(letter: str, start: int, end: int) -> Reason:
    # Trim whitespace but keep the span pointing at the real characters.
    while start < end and letter[start].isspace():
        start += 1
    while end > start and letter[end - 1].isspace():
        end -= 1
    text = letter[start:end]
    return Reason(
        text=text,
        start=start,
        end=end,
        category=classify_reason(text),
        clause_refs=CLAUSE_REF.findall(text),
    )


def extract_reasons(letter: str) -> list[Reason]:
    """Prefer a numbered list under a 'reason(s):' header; else trigger sentences."""
    header = REASON_HEADER.search(letter)
    if header:
        items = list(NUMBERED_ITEM.finditer(letter, header.end()))
        reasons = []
        previous_end = header.end()
        for i, item in enumerate(items):
            # Only whitespace may sit between the header and item 1, or between
            # two items. Any other text means the numbered list has ended.
            if letter[previous_end:item.start()].strip():
                break
            next_start = items[i + 1].start() if i + 1 < len(items) else len(letter)
            # An item ends at a blank line, or where the next item begins.
            blank = letter.find("\n\n", item.end(), next_start)
            end = blank if blank != -1 else next_start
            reasons.append(_make_reason(letter, item.end(), end))
            previous_end = end
        if reasons:
            return reasons

    return [_make_reason(letter, m.start(), m.end()) for m in TRIGGER_SENTENCE.finditer(letter)]


def extract_letter(letter: str) -> LetterExtraction:
    result = LetterExtraction()
    for name, pattern in FIELD_PATTERNS.items():
        m = pattern.search(letter)
        if not m:
            continue
        raw = m.group(1).strip()
        value: object
        try:
            if name.endswith("_date"):
                value = parse_indian_date(raw)
            elif name.startswith("amount_"):
                value = parse_inr_amount(raw)
            else:
                value = raw.rstrip("/-")
        except ValueError:
            continue  # leave the field empty; the user can fill it in
        setattr(result, name, value)
    result.reasons = extract_reasons(letter)
    return result


def extract_rejection(doc: ParsedDocument) -> RejectionExtraction:
    """Contract 4.3: extract a parsed letter, with page and bbox for every reason."""
    from .ingest import chunks_in_span, document_text

    text, spans = document_text(doc)
    found = extract_letter(text)
    reasons = []
    for i, reason in enumerate(found.reasons, start=1):
        covered = chunks_in_span(spans, reason.start, reason.end)
        first_page = covered[0].page if covered else 1
        boxes = [c.bbox for c in covered if c.page == first_page]
        bbox = [
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        ] if boxes else [0.0, 0.0, 0.0, 0.0]
        reasons.append(RejectionReason(
            reason_id=f"r{i}",
            text=" ".join(reason.text.split()),
            category=reason.category,
            page=first_page,
            bbox=bbox,
            clause_refs=reason.clause_refs,
        ))

    def as_float(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    return RejectionExtraction(
        claim_no=found.claim_number,
        policy_no=found.policy_number,
        admission_date=found.admission_date,
        discharge_date=found.discharge_date,
        claimed_amount=as_float(found.amount_claimed),
        rejected_amount=as_float(found.amount_rejected),
        reasons=reasons,
        letter_date=found.letter_date,
    )
