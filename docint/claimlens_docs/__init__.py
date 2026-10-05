"""claimlens_docs: document intelligence for ClaimLens (Member 4).

The functions the backend calls (work division, contract 4.3 and 4.5):

    ingest_document(path, doc_id, doc_type) -> ParsedDocument
    extract_rejection(doc) -> RejectionExtraction
    extract_policy_facts(doc) -> PolicyFacts
    sanitize_for_llm(text) -> SanitizedText
    locate_quote(doc_or_doc_id, quote) -> LocateResult

claimlens_docs.policy_facts.extract_policy_facts(text) is the lower-level
text version; the package-level name takes a ParsedDocument.
"""

from .grounding import locate_quote, register_document
from .ingest import ingest_document, ingest_text
from .letter import extract_rejection
from .policy_facts import extract_policy_facts_doc as extract_policy_facts
from .security import sanitize_for_llm

__all__ = [
    "extract_policy_facts",
    "extract_rejection",
    "ingest_document",
    "ingest_text",
    "locate_quote",
    "register_document",
    "sanitize_for_llm",
]
