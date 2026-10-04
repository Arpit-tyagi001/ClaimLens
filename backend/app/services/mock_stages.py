import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING

from contracts.schemas import PolicyFacts, WaitingPeriod, VerifiedFinding, Evidence, Challenge
from backend.app.services.facts import save_facts
from backend.app.services.findings import save_findings

if TYPE_CHECKING:
    from backend.app.services.pipeline import StageContext

logger = logging.getLogger("claimlens.pipeline")


async def mock_extracting_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs.extract_policy_facts
    synthetic_facts = PolicyFacts(
        policy_start=date(2024, 1, 1),
        policy_end=date(2025, 1, 1),
        sum_insured=500000.0,
        waiting_periods=[
            WaitingPeriod(kind="Pre-existing diseases", months=36),
            WaitingPeriod(kind="Specific illness", months=24),
        ],
        confirmed_by_user=False,
    )
    save_facts(ctx.case_id, synthetic_facts, confirmed=False)

    ctx.emit("RUNNING", "Extracting text and tables from policy document")
    await asyncio.sleep(0.05)
    ctx.emit("RUNNING", "Extracting text and tables from rejection letter")
    await asyncio.sleep(0.05)
    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="mock")


async def mock_investigating_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    from backend.app.services.pipeline import FAILURE_INJECTION, FAILURE_ATTEMPTS, Stage

    mode = FAILURE_INJECTION.get(Stage.INVESTIGATING)
    if mode == "fail":
        raise RuntimeError("Simulated failure in INVESTIGATING")
    elif mode == "timeout":
        await asyncio.sleep(999.0)
    elif mode == "once":
        attempts = FAILURE_ATTEMPTS.get(Stage.INVESTIGATING, 0)
        FAILURE_ATTEMPTS[Stage.INVESTIGATING] = attempts + 1
        if attempts == 0:
            raise RuntimeError("Simulated failure in INVESTIGATING (once)")

    ctx.emit("RUNNING", "Analyzing policy coverage against rejection reasons")
    await asyncio.sleep(0.05)
    ctx.emit("RUNNING", "Evaluating exclusions and precedent cases")
    await asyncio.sleep(0.05)
    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="mock")


async def mock_investigating_fallback(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("FALLBACK", "using fallback: retrieve-then-reason")
    await asyncio.sleep(0.05)


async def mock_verifying_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_ai.run_investigation / run_verification
    synthetic_findings = [
        VerifiedFinding(
            finding_id=f"f_{ctx.case_id}_001",
            reason_id="r_exclusion_3.1",
            assessment="SUPPORTED",
            confidence=0.95,
            reasoning="Pre-existing condition exclusion clause 3.1 clearly applies.",
            evidence=[Evidence(chunk_id="chk_001", quote="Exclusion 3.1 for hypertension", page=4, section_path="Exclusions/3.1")],
            facts_used=[],
            rule_checks=[],
            citation_checks=[],
            fact_checks=[],
            challenges=[],
            status="VERIFIED",
            final_assessment="SUPPORTED",
            final_confidence=0.95,
        ),
        VerifiedFinding(
            finding_id=f"f_{ctx.case_id}_002",
            reason_id="r_waiting_period_4.2",
            assessment="PARTIAL",
            confidence=0.60,
            reasoning="Waiting period clause 4.2 partially applicable; missing admission records.",
            evidence=[Evidence(chunk_id="chk_002", quote="Waiting period clause 4.2", page=6, section_path="WaitingPeriods/4.2")],
            facts_used=[],
            rule_checks=[],
            citation_checks=[],
            fact_checks=[],
            challenges=[Challenge(round=1, attacker_argument="Claim lacked discharge summary", attacker_chunk_ids=["chk_002"], rebuttal="Partial record provided", outcome="WEAKENED")],
            status="DOWNGRADED",
            final_assessment="PARTIAL",
            final_confidence=0.60,
        ),
        VerifiedFinding(
            finding_id=f"f_{ctx.case_id}_003",
            reason_id="r_special_condition_8",
            assessment="INSUFFICIENT_EVIDENCE",
            confidence=0.40,
            reasoning="Ambiguous clause 8 requires human reviewer verification.",
            evidence=[Evidence(chunk_id="chk_003", quote="Special condition section 8", page=10, section_path="SpecialConditions/8")],
            facts_used=[],
            rule_checks=[],
            citation_checks=[],
            fact_checks=[],
            challenges=[],
            status="NEEDS_HUMAN",
            final_assessment="INSUFFICIENT_EVIDENCE",
            final_confidence=0.40,
        ),
    ]
    save_findings(ctx.case_id, synthetic_findings)

    ctx.emit("RUNNING", "Verifying citations in finding report")
    await asyncio.sleep(0.05)
    ctx.emit("RUNNING", "Performing adversarial consistency check")
    await asyncio.sleep(0.05)
    if hasattr(ctx, "record_metrics"):
        ctx.record_metrics(model="mock")
