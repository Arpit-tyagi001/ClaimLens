"""Quote grounding: is a quoted clause really in the policy, and where?

locate_quote() first tries an exact match on normalised text, then a fuzzy
match (rapidfuzz). A fuzzy match is accepted only if it is close (score at
least FUZZY_THRESHOLD) AND every number and every meaning-changing word
("not", "only", "excluded", "covered", ...) is the same in the quote and in
the policy text. A string with one number changed is still 98% similar, so
the score alone would let a fabricated "48 months" pass for "24 months".

The verifier (M3) uses this to flag ungrounded citations, and the UI (M2)
uses page and bbox to draw the highlight.
"""

import re
from collections import Counter
from functools import lru_cache

from rapidfuzz import fuzz

from .schemas import Chunk, LocateResult, ParsedDocument

FUZZY_THRESHOLD = 0.90

# Words whose change flips or narrows the meaning of a clause.
CRITICAL_WORDS = {
    "not", "no", "nor", "never", "without", "except", "unless", "only", "including",
    "excluded", "exclusion", "excludes", "covered", "payable", "admissible",
    "shall", "may", "must", "before", "after", "within", "until", "maximum", "minimum",
}

_REGISTRY: dict[str, ParsedDocument] = {}


def register_document(doc: ParsedDocument) -> None:
    """Make a document available to locate_quote(doc_id, quote)."""
    _REGISTRY[doc.doc_id] = doc


def get_document(doc_id: str) -> ParsedDocument:
    try:
        return _REGISTRY[doc_id]
    except KeyError:
        raise KeyError(f"Document {doc_id!r} is not registered; call register_document first") from None


def normalise(text: str) -> str:
    """Lowercase, drop punctuation and digit grouping, collapse whitespace."""
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 1,25,000 -> 125000
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _critical_tokens(normalised: str) -> Counter:
    return Counter(t for t in normalised.split() if t.isdigit() or t in CRITICAL_WORDS)


def _expand_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and text[start - 1] != " ":
        start -= 1
    while end < len(text) and text[end] != " ":
        end += 1
    return start, end


def _differences(quote_tokens: Counter, source_tokens: Counter) -> str | None:
    if quote_tokens == source_tokens:
        return None
    only_quote = sorted((quote_tokens - source_tokens).elements())
    only_source = sorted((source_tokens - quote_tokens).elements())
    parts = []
    if only_quote:
        parts.append(f"quote has {only_quote}")
    if only_source:
        parts.append(f"policy has {only_source}")
    return "Close match, but key words or numbers differ: " + "; ".join(parts)


def locate_quote(doc: ParsedDocument | str, quote: str, threshold: float = FUZZY_THRESHOLD) -> LocateResult:
    """Contract 4.5: find a quote in a document. doc may be a ParsedDocument or a registered doc_id."""
    if isinstance(doc, str):
        doc = get_document(doc)

    q = normalise(quote)
    if not q:
        return LocateResult(found=False, match_score=0.0, reason="Empty quote")

    # One normalised string for the whole document, remembering each chunk's span.
    pieces: list[str] = []
    spans: list[tuple[int, int, Chunk]] = []
    pos = 0
    for chunk in doc.chunks:
        n = normalise(chunk.text)
        if not n:
            continue
        if pieces:
            pieces.append(" ")
            pos += 1
        pieces.append(n)
        spans.append((pos, pos + len(n), chunk))
        pos += len(n)
    corpus = "".join(pieces)

    idx = corpus.find(q)
    if idx >= 0:
        start, end, score = idx, idx + len(q), 1.0
    else:
        alignment = fuzz.partial_ratio_alignment(q, corpus)
        if alignment is None:
            return LocateResult(found=False, match_score=0.0, reason="No similar text in the document")
        start, end = _expand_to_words(corpus, alignment.dest_start, alignment.dest_end)
        score = round(alignment.score / 100, 3)

    matched = corpus[start:end]
    hit_chunks = [c for s, e, c in spans if s < end and e > start]
    first = hit_chunks[0] if hit_chunks else None
    same_page = [c for c in hit_chunks if first is not None and c.page == first.page]
    bbox = None
    if same_page:
        bbox = [
            min(c.bbox[0] for c in same_page), min(c.bbox[1] for c in same_page),
            max(c.bbox[2] for c in same_page), max(c.bbox[3] for c in same_page),
        ]

    reason = None
    if score < threshold:
        reason = f"Best match is only {score:.0%} similar (needs {threshold:.0%})"
    else:
        reason = _differences(_critical_tokens(q), _critical_tokens(matched))

    return LocateResult(
        found=reason is None,
        match_score=score,
        page=first.page if first else None,
        bbox=bbox,
        chunk_id=first.chunk_id if first else None,
        matched_text=matched,
        reason=reason,
    )


@lru_cache(maxsize=8)
def _doc_from_text(text: str) -> ParsedDocument:
    from .ingest import ingest_text

    return ingest_text(text, doc_id="_text", doc_type="policy")


def quote_in_text(quote: str, policy_text: str) -> bool:
    """Adapter for eval.run --grounding: (quote, policy_text) -> found."""
    return locate_quote(_doc_from_text(policy_text), quote).found
