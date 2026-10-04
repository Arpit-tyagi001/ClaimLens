from fastapi import APIRouter, Query
from typing import Optional
from backend.app.services.pipeline import FAILURE_INJECTION, FAILURE_ATTEMPTS, Stage

router = APIRouter(prefix="/debug", tags=["Debug"])


@router.get("/fail-stage")
async def set_fail_stage(
    stage: Stage = Query(..., description="Stage to inject failure into"),
    mode: str = Query(..., pattern="^(fail|timeout|once)$", description="Failure mode: fail, timeout, or once"),
):
    FAILURE_INJECTION[stage] = mode
    FAILURE_ATTEMPTS[stage] = 0
    return {"status": "ok", "stage": stage.value, "mode": mode}


@router.delete("/fail-stage")
async def clear_fail_stage(
    stage: Optional[Stage] = Query(None, description="Stage to clear, or all if omitted"),
):
    if stage:
        FAILURE_INJECTION.pop(stage, None)
        FAILURE_ATTEMPTS.pop(stage, None)
    else:
        FAILURE_INJECTION.clear()
        FAILURE_ATTEMPTS.clear()
    return {"status": "ok", "cleared": stage.value if stage else "all"}
