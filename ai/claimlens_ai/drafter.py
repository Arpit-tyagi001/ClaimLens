from __future__ import annotations

import re
from typing import Any

from contracts.schemas import VerifiedFinding


def _is_approved(finding: VerifiedFinding) -> bool:
    """Only VERIFIED findings can be used in the draft."""
    return finding.status == "VERIFIED"


def _citation_for_evidence(finding: VerifiedFinding) -> str:
    """Create a citation using the chunk IDs attached to the finding."""
    if not finding.evidence:
        return "[citation unavailable]"

    chunk_ids = [e.chunk_id for e in finding.evidence]
    return " ".join(f"[{chunk_id}]" for chunk_id in chunk_ids)


def _sentence_with_citation(text: str, citation: str) -> str:
    """Ensure each generated sentence ends with a citation."""
    text = text.strip()

    if not text:
        return ""

    if text[-1] not in ".!?":
        text += "."

    return f"{text} {citation}"


def _build_finding_sentence(finding: VerifiedFinding) -> str:
    """Convert one verified finding into a review-request sentence."""
    citation = _citation_for_evidence(finding)

    if finding.final_assessment == "SUPPORTED":
        wording = (
            "The cited policy clause appears to support "
            "the rejection ground."
        )

    elif finding.final_assessment == "PARTIAL":
        wording = (
            "The cited policy clause appears to partially support "
            "the rejection ground."
        )

    elif finding.final_assessment == "NOT_SUPPORTED":
        wording = (
            "The cited policy clause does not appear to support "
            "the rejection ground."
        )

    else:
        wording = (
            "The available cited policy evidence is insufficient "
            "to support the rejection ground."
        )

    return _sentence_with_citation(wording, citation)


def draft_review_request(
    case_id: str,
    approved: list[VerifiedFinding],
) -> Any:
    """
    Draft a review request from approved findings only.

    Every sentence contains a chunk citation.
    """

    approved_findings = [
        finding
        for finding in approved
        if _is_approved(finding)
    ]

    if not approved_findings:
        raise ValueError(
            "No approved findings are available for drafting."
        )

    sentences: list[str] = []

    for finding in approved_findings:
        sentence = _build_finding_sentence(finding)

        if sentence:
            sentences.append(sentence)

    if not sentences:
        raise ValueError(
            "Approved findings did not contain usable evidence."
        )

    body = " ".join(sentences)

    # Keep the wording evidence-based and avoid legal advice.
    body += (
        " This review request is based only on the cited policy evidence."
    )

    # Add citation to the final sentence as well.
    last_citation = _citation_for_evidence(approved_findings[-1])
    body = re.sub(
        r"(This review request is based only on the cited policy evidence\.)$",
        rf"\1 {last_citation}",
        body,
    )

    # Try to use the project's Draft schema if it exists.
    try:
        from contracts.schemas import Draft

        return Draft(
            case_id=case_id,
            text=body,
        )

    except ImportError:
        # Temporary compatibility until Draft is added to contracts.
        return {
            "case_id": case_id,
            "text": body,
            "finding_ids": [
                finding.finding_id
                for finding in approved_findings
            ],
        }