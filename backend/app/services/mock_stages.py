import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING

from contracts.schemas import PolicyFacts, WaitingPeriod
from backend.app.services.facts import save_facts

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


async def mock_investigating_fallback(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("FALLBACK", "using fallback: retrieve-then-reason")
    await asyncio.sleep(0.05)


async def mock_verifying_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("RUNNING", "Verifying citations in finding report")
    await asyncio.sleep(0.05)
    ctx.emit("RUNNING", "Performing adversarial consistency check")
    await asyncio.sleep(0.05)
