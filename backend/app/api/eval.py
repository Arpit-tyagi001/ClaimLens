import os
import json
import logging
from fastapi import APIRouter
from backend.app.config import get_settings

logger = logging.getLogger("claimlens.eval")

router = APIRouter(prefix="/api/eval", tags=["Evaluation"])


@router.get("/latest")
async def get_latest_eval_report():
    from backend.app.main import PipelineException

    report_path = get_settings().EVAL_REPORT_PATH
    abs_path = os.path.abspath(report_path)

    if not os.path.isfile(abs_path):
        return {"available": False, "message": "No evaluation report has been generated yet."}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as exc:
        raise PipelineException(
            code="INTERNAL_ERROR",
            message=f"Evaluation report is not valid JSON: {exc}",
            stage=None,
            status_code=500,
        )
    except Exception as exc:
        logger.error(f"Error reading evaluation report: {exc}", exc_info=exc)
        raise PipelineException(
            code="INTERNAL_ERROR",
            message="Failed to read evaluation report",
            stage=None,
            status_code=500,
        )
