from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from contracts.schemas import Evidence, Finding, RejectionReason
from ai.claimlens_ai.llm import (
    LLMClient,
    LLMError,
    create_groq_provider,
)
from ai.claimlens_ai.retrieval import get_retriever
from ai.claimlens_ai.tools import ClaimLensTools


Emit = Callable[[dict], None]

MAX_TOOL_STEPS = 6


# =========================================================
# LLM OUTPUT SCHEMA
# =========================================================

class InvestigatorStep(BaseModel):
    action: Literal[
        "search_policy",
        "get_section",
        "get_definition",
        "run_rule_check",
        "finish",
    ]

    query: str = ""
    section_path: str = ""
    term: str = ""
    rule: str = ""

    assessment: Literal[
        "SUPPORTED",
        "NOT_SUPPORTED",
        "PARTIAL",
        "INSUFFICIENT_EVIDENCE",
    ] = "INSUFFICIENT_EVIDENCE"

    confidence: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
    )

    reasoning: str = ""

    evidence_chunk_ids: list[str] = Field(
        default_factory=list
    )

    facts_used: list[Any] = Field(
        default_factory=list
    )


# =========================================================
# REJECTION CLASSIFICATION
# =========================================================

def classify_reason(text: str) -> str:
    text_lower = text.lower()

    if "waiting" in text_lower:
        return "waiting_period"

    if (
        "pre-existing" in text_lower
        or "pre existing" in text_lower
    ):
        return "pre_existing_condition"

    if "document" in text_lower:
        return "missing_documents"

    if (
        "limit" in text_lower
        or "exceed" in text_lower
    ):
        return "limit_exceeded"

    if (
        "exclusion" in text_lower
        or "excluded" in text_lower
    ):
        return "exclusion"

    return "other"


# =========================================================
# HELPERS
# =========================================================

def _facts_to_dict(facts: Any) -> dict:
    if facts is None:
        return {}

    if hasattr(facts, "model_dump"):
        return facts.model_dump()

    if isinstance(facts, dict):
        return facts

    return vars(facts)


def _hit_to_dict(hit: Any) -> dict:
    return {
        "chunk_id": hit.chunk_id,
        "page": hit.page,
        "section_path": hit.section_path,
        "text": hit.text,
    }


def _build_evidence(
    hits_by_id: dict[str, Any],
    requested_ids: list[str],
) -> list[Evidence]:

    evidence: list[Evidence] = []

    for chunk_id in requested_ids:

        hit = hits_by_id.get(chunk_id)

        if hit is None:
            continue

        evidence.append(
            Evidence(
                chunk_id=hit.chunk_id,
                quote=hit.text,
                page=hit.page,
                section_path=hit.section_path,
            )
        )

    return evidence


# =========================================================
# DETERMINISTIC EVIDENCE SAFETY
# =========================================================

def _evidence_supports_category(
    category: str,
    rejection_text: str,
    evidence: list[Evidence],
) -> bool:

    if not evidence:
        return False

    rejection_lower = rejection_text.lower()

    combined_text = " ".join(
        item.quote.lower()
        for item in evidence
    )

    if category == "waiting_period":

        return (
            "waiting" in combined_text
            and (
                "waiting" in rejection_lower
                or "period" in rejection_lower
            )
        )

    if category == "pre_existing_condition":

        return (
            (
                "pre-existing" in combined_text
                or "pre existing" in combined_text
            )
            and (
                "pre-existing" in rejection_lower
                or "pre existing" in rejection_lower
            )
        )

    if category == "exclusion":

        return (
            "exclud" in combined_text
            and (
                "exclusion" in rejection_lower
                or "excluded" in rejection_lower
            )
        )

    if category == "missing_documents":

        return (
            (
                "document" in combined_text
                or "documents" in combined_text
            )
            and "document" in rejection_lower
        )

    if category == "limit_exceeded":

        return (
            (
                "limit" in combined_text
                or "maximum" in combined_text
                or "exceed" in combined_text
            )
            and (
                "limit" in rejection_lower
                or "exceed" in rejection_lower
            )
        )

    return False


def _apply_evidence_guardrail(
    finding: Finding,
    category: str,
    rejection: RejectionReason,
) -> Finding:

    if not finding.evidence:
        return finding

    supported = _evidence_supports_category(
        category=category,
        rejection_text=rejection.text,
        evidence=finding.evidence,
    )

    if not supported:
        return finding

    if finding.assessment == "INSUFFICIENT_EVIDENCE":

        finding.assessment = "SUPPORTED"

        finding.confidence = max(
            finding.confidence,
            0.75,
        )

        finding.reasoning = (
            "The rejection ground is supported by the retrieved "
            "policy evidence. The policy clause directly addresses "
            f"the {category.replace('_', ' ')} ground. "
            "The evidence is retained for adversarial verification."
        )

    elif finding.assessment == "SUPPORTED":

        finding.confidence = max(
            finding.confidence,
            0.75,
        )

        if not finding.reasoning.strip():

            finding.reasoning = (
                "The retrieved policy evidence directly supports "
                f"the {category.replace('_', ' ')} rejection ground. "
                "The finding is subject to adversarial verification."
            )

    return finding


# =========================================================
# FALLBACK FINDING
# =========================================================

def _fallback_finding(
    case_id: str,
    rejection: RejectionReason,
    evidence: list[Evidence],
    category: str,
) -> Finding:

    if evidence:

        supported = _evidence_supports_category(
            category=category,
            rejection_text=rejection.text,
            evidence=evidence,
        )

        if supported:

            assessment = "SUPPORTED"
            confidence = 0.75

            reasoning = (
                "The retrieved policy evidence directly supports "
                f"the {category.replace('_', ' ')} rejection ground. "
                "The finding is conservatively subject to verification."
            )

        else:

            assessment = "INSUFFICIENT_EVIDENCE"
            confidence = 0.30

            reasoning = (
                "Relevant policy evidence was retrieved, but it "
                "could not be deterministically confirmed as directly "
                "supporting the rejection ground."
            )

    else:

        assessment = "INSUFFICIENT_EVIDENCE"
        confidence = 0.20

        reasoning = (
            "No relevant policy evidence was retrieved. "
            "Human verification is required."
        )

    return Finding(
        finding_id=f"{case_id}-{rejection.reason_id}",
        reason_id=rejection.reason_id,
        assessment=assessment,
        confidence=confidence,
        reasoning=reasoning,
        evidence=evidence,
        facts_used=[],
        rule_checks=[],
    )


# =========================================================
# PROMPT
# =========================================================

def _build_prompt(
    rejection: RejectionReason,
    category: str,
    facts: dict,
    tool_history: list[dict],
) -> str:

    history_text = ""

    if tool_history:

        history_text = "\nTOOL RESULTS:\n"

        for item in tool_history:
            history_text += f"\n{item}\n"

    return f"""
You are the ClaimLens Investigator.

Your task is to investigate ONE insurance rejection ground.

This is NOT legal advice.
Do not predict appeal outcomes.

REJECTION:
reason_id: {rejection.reason_id}
text: {rejection.text}

CLASSIFIED CATEGORY:
{category}

CASE FACTS:
{facts}

AVAILABLE TOOLS:
1. search_policy
2. get_section
3. get_definition
4. run_rule_check

You may use at most 6 tool steps.

IMPORTANT RULES:

- Policy retrieval is required before making a conclusion.
- Never invent policy clauses.
- Evidence MUST come from retrieved chunks.
- evidence_chunk_ids MUST contain only returned chunk IDs.
- If a retrieved policy clause directly addresses the rejection
  category, prefer SUPPORTED over INSUFFICIENT_EVIDENCE.
- Use PARTIAL when the policy only partially addresses the ground.
- Use INSUFFICIENT_EVIDENCE only when the available evidence
  genuinely cannot establish the rejection ground.
- Give meaningful reasoning.
- Never leave reasoning empty when action="finish".
- Use run_rule_check when dates or amounts matter.
- On the final step use action="finish".

The system may perform the initial policy search before asking
you for your next decision. If TOOL RESULTS contain policy evidence,
analyze that evidence before finishing.

Return ONLY JSON matching the required schema.

{history_text}
""".strip()


# =========================================================
# MAIN INVESTIGATOR
# =========================================================

def run_investigation(
    case_id: str,
    rejection: RejectionReason,
    facts,
    emit: Emit,
) -> list[Finding]:

    category = classify_reason(rejection.text)

    emit(
        {
            "event": "investigator_classified",
            "case_id": case_id,
            "reason_id": rejection.reason_id,
            "category": category,
        }
    )

    retriever = get_retriever(case_id)

    tools = ClaimLensTools(retriever)

    llm = LLMClient(
        provider=create_groq_provider(),
        max_retries=1,
    )

    facts_dict = _facts_to_dict(facts)

    tool_history: list[dict] = []

    all_hits: dict[str, Any] = {}

    final_step: InvestigatorStep | None = None

    # =====================================================
    # BOUNDED INVESTIGATION LOOP
    # =====================================================

    for step_number in range(
        1,
        MAX_TOOL_STEPS + 1,
    ):

        emit(
            {
                "event": "investigator_step",
                "case_id": case_id,
                "reason_id": rejection.reason_id,
                "step": step_number,
                "max_steps": MAX_TOOL_STEPS,
            }
        )

        # =================================================
        # STEP 1: MANDATORY POLICY RETRIEVAL
        #
        # We do NOT allow the LLM to skip retrieval.
        # This prevents premature "finish" decisions.
        # =================================================

        if step_number == 1:

            query = rejection.text

            hits = tools.search_policy(
                query=query,
                top_k=5,
            )

            for hit in hits:
                all_hits[hit.chunk_id] = hit

            result = {
                "tool": "search_policy",
                "query": query,
                "results": [
                    _hit_to_dict(hit)
                    for hit in hits
                ],
            }

            tool_history.append(result)

            emit(
                {
                    "event": "tool_called",
                    "case_id": case_id,
                    "tool": "search_policy",
                    "step": step_number,
                    "result_count": len(hits),
                }
            )

            # Move to the next Investigator step.
            continue

        # =================================================
        # LLM DECISION
        # =================================================

        prompt = _build_prompt(
            rejection=rejection,
            category=category,
            facts=facts_dict,
            tool_history=tool_history,
        )

        try:

            decision = llm.generate_structured(
                prompt,
                InvestigatorStep,
            )

        except LLMError as exc:

            emit(
                {
                    "event": "investigator_llm_error",
                    "case_id": case_id,
                    "reason_id": rejection.reason_id,
                    "error": str(exc),
                }
            )

            evidence = _build_evidence(
                all_hits,
                list(all_hits.keys()),
            )

            finding = _fallback_finding(
                case_id=case_id,
                rejection=rejection,
                evidence=evidence,
                category=category,
            )

            emit(
                {
                    "event": "finding_created",
                    "case_id": case_id,
                    "finding_id": finding.finding_id,
                    "assessment": finding.assessment,
                    "confidence": finding.confidence,
                    "evidence_count": len(finding.evidence),
                }
            )

            return [finding]

        # =================================================
        # FINISH
        # =================================================

        if decision.action == "finish":

            final_step = decision

            emit(
                {
                    "event": "investigator_finished",
                    "case_id": case_id,
                    "reason_id": rejection.reason_id,
                    "step": step_number,
                }
            )

            break

        # =================================================
        # SEARCH POLICY
        # =================================================

        if decision.action == "search_policy":

            query = decision.query or rejection.text

            hits = tools.search_policy(
                query=query,
                top_k=5,
            )

            for hit in hits:
                all_hits[hit.chunk_id] = hit

            result = {
                "tool": "search_policy",
                "query": query,
                "results": [
                    _hit_to_dict(hit)
                    for hit in hits
                ],
            }

            tool_history.append(result)

            emit(
                {
                    "event": "tool_called",
                    "case_id": case_id,
                    "tool": "search_policy",
                    "step": step_number,
                    "result_count": len(hits),
                }
            )

            continue

        # =================================================
        # GET SECTION
        # =================================================

        if decision.action == "get_section":

            hits = tools.get_section(
                decision.section_path
            )

            for hit in hits:
                all_hits[hit.chunk_id] = hit

            result = {
                "tool": "get_section",
                "section_path": decision.section_path,
                "results": [
                    _hit_to_dict(hit)
                    for hit in hits
                ],
            }

            tool_history.append(result)

            emit(
                {
                    "event": "tool_called",
                    "case_id": case_id,
                    "tool": "get_section",
                    "step": step_number,
                    "result_count": len(hits),
                }
            )

            continue

        # =================================================
        # GET DEFINITION
        # =================================================

        if decision.action == "get_definition":

            hits = tools.get_definition(
                decision.term
            )

            for hit in hits:
                all_hits[hit.chunk_id] = hit

            result = {
                "tool": "get_definition",
                "term": decision.term,
                "results": [
                    _hit_to_dict(hit)
                    for hit in hits
                ],
            }

            tool_history.append(result)

            emit(
                {
                    "event": "tool_called",
                    "case_id": case_id,
                    "tool": "get_definition",
                    "step": step_number,
                    "result_count": len(hits),
                }
            )

            continue

        # =================================================
        # RULE CHECK
        # =================================================

        if decision.action == "run_rule_check":

            rule_result = tools.run_rule_check(
                rule=decision.rule,
                facts=facts_dict,
            )

            result = {
                "tool": "run_rule_check",
                "rule": rule_result.rule,
                "passed": rule_result.passed,
                "details": rule_result.details,
            }

            tool_history.append(result)

            emit(
                {
                    "event": "tool_called",
                    "case_id": case_id,
                    "tool": "run_rule_check",
                    "step": step_number,
                    "passed": rule_result.passed,
                }
            )

            continue

    # =====================================================
    # BUILD FINAL FINDING
    # =====================================================

    if final_step is None:

        evidence = _build_evidence(
            all_hits,
            list(all_hits.keys()),
        )

        finding = _fallback_finding(
            case_id=case_id,
            rejection=rejection,
            evidence=evidence,
            category=category,
        )

    else:

        requested_ids = final_step.evidence_chunk_ids

        evidence = _build_evidence(
            all_hits,
            requested_ids,
        )

        # =================================================
        # IMPORTANT:
        # If the LLM finishes without selecting evidence,
        # but retrieval found evidence, use the retrieved
        # evidence instead of silently throwing it away.
        # =================================================

        if not evidence and all_hits:

            evidence = _build_evidence(
                all_hits,
                list(all_hits.keys())[:3],
            )

        finding = Finding(
            finding_id=f"{case_id}-{rejection.reason_id}",
            reason_id=rejection.reason_id,
            assessment=final_step.assessment,
            confidence=final_step.confidence,
            reasoning=final_step.reasoning,
            evidence=evidence,
            facts_used=final_step.facts_used,
            rule_checks=[],
        )

        # =================================================
        # DETERMINISTIC EVIDENCE GUARDRAIL
        # =================================================

        finding = _apply_evidence_guardrail(
            finding=finding,
            category=category,
            rejection=rejection,
        )

    emit(
        {
            "event": "finding_created",
            "case_id": case_id,
            "finding_id": finding.finding_id,
            "assessment": finding.assessment,
            "confidence": finding.confidence,
            "evidence_count": len(finding.evidence),
        }
    )

    return [finding]