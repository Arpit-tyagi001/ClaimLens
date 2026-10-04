import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.services.pipeline import StageContext

logger = logging.getLogger("claimlens.pipeline")


async def mock_extracting_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("RUNNING", "Extracting text and tables from policy document")
    await asyncio.sleep(0.1)
    ctx.emit("RUNNING", "Extracting text and tables from rejection letter")
    await asyncio.sleep(0.1)


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
    await asyncio.sleep(0.1)
    ctx.emit("RUNNING", "Evaluating exclusions and precedent cases")
    await asyncio.sleep(0.1)


async def mock_investigating_fallback(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("FALLBACK", "using fallback: retrieve-then-reason")
    await asyncio.sleep(0.1)


async def mock_verifying_stage(ctx: "StageContext") -> None:
    # TODO(wire): replace with claimlens_docs / claimlens_ai calls
    ctx.emit("RUNNING", "Verifying citations in finding report")
    await asyncio.sleep(0.1)
    ctx.emit("RUNNING", "Performing adversarial consistency check")
    await asyncio.sleep(0.1)
