import logging
import os
from typing import Optional

logger = logging.getLogger("claimlens.upload_validation")

MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))


def validate_pdf(
    filename: Optional[str],
    content_type: Optional[str],
    data: bytes,
    label: str,
) -> None:
    """
    Validate PDF content bytes and metadata.
    Raises PipelineException with stage='UPLOADED' on validation failures.
    """
    from backend.app.main import PipelineException

    if not data or len(data) == 0:
        raise PipelineException(
            code="EMPTY_FILE",
            message=f"The {label} file is empty.",
            stage="UPLOADED",
            status_code=400,
        )

    if len(data) > MAX_UPLOAD_BYTES:
        raise PipelineException(
            code="FILE_TOO_LARGE",
            message=f"The {label} file exceeds maximum allowed size.",
            stage="UPLOADED",
            status_code=413,
        )

    # Magic bytes check: MUST start with b"%PDF-"
    if not data.startswith(b"%PDF-"):
        raise PipelineException(
            code="INVALID_FILE_TYPE",
            message=f"The {label} file must be a PDF.",
            stage="UPLOADED",
            status_code=415,
        )
