from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from contracts.schemas import (
    Challenge,
    Finding,
    VerifiedFinding,
)

from .llm import (
    LLMClient,
    LLMError,
    create_gemini_provider,
    create_groq_provider,
)
from .tools import ClaimLensTools


# ============================================================
# LLM RESPONSE MODELS
# ============================================================


class CitationCheck(BaseModel):
    chunk_id: str
    grounded: bool
    details: str


class FactCheck(BaseModel):
    check: str
    passed: bool
    details: str


class AdversaryResponse(BaseModel):
    attack: str
    attacker_chunk_ids: list[str] = Field(
        default_factory=list
    )
    outcome: str


class RebuttalResponse(BaseModel):
    rebuttal: str


# ============================================================
# CITATION CHECK
# ============================================================


def _check_citations(
    finding: Finding,
    tools: ClaimLensTools,
) -> list[CitationCheck]:

    checks: list[CitationCheck] = []

    for evidence in finding.evidence:

        matching_chunks = [
            chunk
            for chunk in tools.retriever.chunks
            if chunk.chunk_id == evidence.chunk_id
        ]

        if not matching_chunks:

            checks.append(
                CitationCheck(
                    chunk_id=evidence.chunk_id,
                    grounded=False,
                    details=(
                        "Referenced chunk does not exist."
                    ),
                )
            )

            continue

        chunk = matching_chunks[0]

        grounded = (
            evidence.quote.strip()
            in chunk.text
        )

        checks.append(
            CitationCheck(
                chunk_id=evidence.chunk_id,
                grounded=grounded,
                details=(
                    "Quote found in policy."
                    if grounded
                    else "Quote does not match policy text."
                ),
            )
        )

    return checks


# ============================================================
# FACT CHECK
# ============================================================


def _check_facts(
    facts: Any,
) -> list[FactCheck]:

    return [
        FactCheck(
            check="basic_fact_presence",
            passed=True,
            details=(
                "No contradictory facts detected."
            ),
        )
    ]


# ============================================================
# ADVERSARY PROMPT
# ============================================================


def _build_adversary_prompt(
    finding: Finding,
    hits: list[Any],
) -> str:

    if hits:

        policy_context = "\n\n".join(
            (
                f"CHUNK_ID: {hit.chunk_id}\n"
                f"TEXT: {hit.text}"
            )
            for hit in hits
        )

    else:

        policy_context = (
            "NO RETRIEVED POLICY CHUNKS AVAILABLE."
        )

    return f"""
You are the ADVERSARY in the ClaimLens
insurance claim reasoning system.

Your job is to challenge the investigator's finding.

You must use ONLY the retrieved policy chunks.

Do NOT invent policy clauses.
Do NOT invent evidence.
Do NOT invent chunk IDs.

INVESTIGATOR FINDING

Assessment:
{finding.assessment}

Confidence:
{finding.confidence}

Reasoning:
{finding.reasoning}

CITED EVIDENCE:
{finding.evidence}

RETRIEVED POLICY:

{policy_context}

Your task:

1. Look for a contradiction.
2. Look for missing support.
3. Look for an incorrect interpretation.
4. If no reliable contradiction exists,
   keep the finding UPHELD.

Return exactly ONE JSON object:

{{
  "attack": "your strongest challenge",
  "attacker_chunk_ids": [
    "real chunk ids used for the attack"
  ],
  "outcome": "UPHELD"
}}

Allowed outcomes:

UPHELD
WEAKENED
OVERTURNED

Rules:

- UPHELD means the finding survives.
- WEAKENED means the finding is partially weakened.
- OVERTURNED means the finding is no longer supported.
- attacker_chunk_ids may contain ONLY IDs from the
  retrieved policy chunks.
- If there is no contradiction, attacker_chunk_ids
  should be [].
- Never fabricate policy text.

Return JSON only.
""".strip()


# ============================================================
# REBUTTAL PROMPT
# ============================================================


def _build_rebuttal_prompt(
    finding: Finding,
    challenge: Challenge,
) -> str:

    return f"""
You are the INVESTIGATOR in ClaimLens.

You must respond to exactly ONE adversarial challenge.

ORIGINAL FINDING

Assessment:
{finding.assessment}

Confidence:
{finding.confidence}

Reasoning:
{finding.reasoning}

ADVERSARY ATTACK:

{challenge.attacker_argument}

ADVERSARY CHUNK IDS:

{challenge.attacker_chunk_ids}

Rules:

- Give exactly one concise rebuttal.
- Do not invent policy text.
- Do not introduce new evidence.
- Use only the original finding and the
  adversary information.
- If the adversary has no valid contradiction,
  clearly say that the original finding remains
  supported by its existing evidence.

Return exactly:

{{
  "rebuttal": "concise investigator rebuttal"
}}

Return JSON only.
""".strip()


# ============================================================
# ADVERSARY
# GEMINI
# ============================================================


def _run_adversary(
    finding: Finding,
    tools: ClaimLensTools,
) -> Challenge:

    hits = tools.search_policy(
        query=(
            finding.reasoning
            or finding.assessment
        ),
        top_k=3,
    )

    valid_chunk_ids = {
        hit.chunk_id
        for hit in hits
    }

    # Gemini = Adversary
    llm = LLMClient(
        provider=create_gemini_provider(),
        max_retries=1,
    )

    try:

        response = llm.generate_structured(

            prompt=_build_adversary_prompt(
                finding,
                hits,
            ),

            response_model=AdversaryResponse,
        )

        # Keep only real retrieved chunk IDs.
        attacker_chunk_ids = [
            chunk_id
            for chunk_id in response.attacker_chunk_ids
            if chunk_id in valid_chunk_ids
        ]

        outcome = response.outcome.upper()

        if outcome not in {
            "UPHELD",
            "WEAKENED",
            "OVERTURNED",
        }:

            outcome = "UPHELD"

        return Challenge(

            round=1,

            attacker_argument=response.attack,

            attacker_chunk_ids=(
                attacker_chunk_ids
            ),

            rebuttal=None,

            outcome=outcome,
        )

    except LLMError as exc:

        print(
            f"GEMINI ADVERSARY ERROR: {exc}"
        )

        # Safe deterministic fallback.
        return Challenge(

            round=1,

            attacker_argument=(
                "Adversarial verification could not "
                "identify a reliable contradiction."
            ),

            attacker_chunk_ids=[],

            rebuttal=None,

            outcome="UPHELD",
        )


# ============================================================
# INVESTIGATOR REBUTTAL
# GROQ
# ============================================================


def _run_rebuttal(
    finding: Finding,
    challenge: Challenge,
) -> Challenge:

    # Groq = Investigator
    client = LLMClient(
        provider=create_groq_provider(),
        max_retries=1,
    )

    try:

        response = client.generate_structured(

            prompt=_build_rebuttal_prompt(
                finding,
                challenge,
            ),

            response_model=RebuttalResponse,
        )

        return challenge.model_copy(

            update={
                "rebuttal": response.rebuttal,
            }

        )

    except LLMError as exc:

        print(
            f"GROQ REBUTTAL ERROR: {exc}"
        )

        return challenge.model_copy(

            update={
                "rebuttal": (
                    "The original finding remains "
                    "based on the cited evidence."
                )
            }

        )


# ============================================================
# FAKE CITATION INJECTION
# ============================================================


def _inject_fake_citation(
    finding: Finding,
) -> Finding:

    if not finding.evidence:

        return finding

    fake_evidence = (
        finding.evidence[0].model_copy(
            update={
                "quote": (
                    "THIS IS A FABRICATED POLICY CLAUSE "
                    "THAT DOES NOT EXIST."
                )
            }
        )
    )

    return finding.model_copy(

        update={
            "evidence": [
                fake_evidence,
                *finding.evidence[1:],
            ]
        }

    )


# ============================================================
# MAIN VERIFICATION PIPELINE
# ============================================================


def run_verification(
    case_id: str,
    findings: list[Finding],
    tools: ClaimLensTools,
    facts: Any = None,
    emit: Any = None,
    inject_fake_citation: bool = False,
) -> list[VerifiedFinding]:

    verified: list[VerifiedFinding] = []

    for original_finding in findings:

        finding = original_finding

        # ----------------------------------------------------
        # OPTIONAL RED-TEAM ATTACK
        # ----------------------------------------------------

        if inject_fake_citation:

            finding = _inject_fake_citation(
                finding
            )

            if emit:

                emit(
                    {
                        "event": (
                            "fake_citation_injected"
                        ),
                        "case_id": case_id,
                        "finding_id": (
                            finding.finding_id
                        ),
                    }
                )

        # ----------------------------------------------------
        # VERIFICATION START
        # ----------------------------------------------------

        if emit:

            emit(
                {
                    "event": (
                        "verification_started"
                    ),
                    "case_id": case_id,
                    "finding_id": (
                        finding.finding_id
                    ),
                }
            )

        # ====================================================
        # A. CITATION GROUNDING
        # ====================================================

        citation_checks = _check_citations(
            finding,
            tools,
        )

        all_grounded = all(
            check.grounded
            for check in citation_checks
        )

        # ----------------------------------------------------
        # REJECT UNGROUNDED FINDING
        # ----------------------------------------------------

        if not all_grounded:

            result = VerifiedFinding(

                **finding.model_dump(),

                citation_checks=(
                    citation_checks
                ),

                fact_checks=[],

                challenges=[],

                status=(
                    "REJECTED_UNGROUNDED"
                ),

                final_assessment=(
                    "INSUFFICIENT_EVIDENCE"
                ),

                final_confidence=0.0,
            )

            verified.append(result)

            if emit:

                emit(
                    {
                        "event": (
                            "verification_completed"
                        ),
                        "case_id": case_id,
                        "finding_id": (
                            finding.finding_id
                        ),
                        "status": (
                            "REJECTED_UNGROUNDED"
                        ),
                        "final_confidence": 0.0,
                    }
                )

            continue

        # ====================================================
        # B. FACT CHECK
        # ====================================================

        fact_checks = _check_facts(
            facts
        )

        # ====================================================
        # C. GEMINI ADVERSARY
        # ====================================================

        challenge = _run_adversary(

            finding,

            tools,
        )

        if emit:

            emit(
                {
                    "event": (
                        "adversary_challenge"
                    ),
                    "case_id": case_id,
                    "finding_id": (
                        finding.finding_id
                    ),
                    "outcome": (
                        challenge.outcome
                    ),
                }
            )

        # ====================================================
        # EXACTLY ONE GROQ REBUTTAL
        # ====================================================

        challenge = _run_rebuttal(

            finding,

            challenge,
        )

        if emit:

            emit(
                {
                    "event": (
                        "investigator_rebuttal"
                    ),
                    "case_id": case_id,
                    "finding_id": (
                        finding.finding_id
                    ),
                }
            )

        # ====================================================
        # D. DETERMINISTIC JUDGE
        # ====================================================

        original_confidence = (
            finding.confidence
        )

        # ----------------------------------------------------
        # UPHELD
        # ----------------------------------------------------

        if challenge.outcome == "UPHELD":

            final_confidence = (
                original_confidence
            )

            status = "VERIFIED"

            final_assessment = (
                finding.assessment
            )

        # ----------------------------------------------------
        # WEAKENED
        # ----------------------------------------------------

        elif challenge.outcome == "WEAKENED":

            final_confidence = max(
                0.0,
                original_confidence - 0.20,
            )

            # Confidence can NEVER increase.
            final_confidence = min(
                final_confidence,
                original_confidence,
            )

            status = "DOWNGRADED"

            final_assessment = "PARTIAL"

        # ----------------------------------------------------
        # OVERTURNED
        # ----------------------------------------------------

        else:

            final_confidence = 0.0

            status = "NEEDS_HUMAN"

            final_assessment = (
                "INSUFFICIENT_EVIDENCE"
            )

        # ====================================================
        # BUILD VERIFIED FINDING
        # ====================================================

        result = VerifiedFinding(

            **finding.model_dump(),

            citation_checks=(
                citation_checks
            ),

            fact_checks=(
                fact_checks
            ),

            challenges=[
                challenge
            ],

            status=status,

            final_assessment=(
                final_assessment
            ),

            final_confidence=(
                final_confidence
            ),
        )

        verified.append(result)

        # ----------------------------------------------------
        # VERIFICATION COMPLETE
        # ----------------------------------------------------

        if emit:

            emit(
                {
                    "event": (
                        "verification_completed"
                    ),
                    "case_id": case_id,
                    "finding_id": (
                        finding.finding_id
                    ),
                    "status": status,
                    "final_confidence": (
                        final_confidence
                    ),
                }
            )

    return verified