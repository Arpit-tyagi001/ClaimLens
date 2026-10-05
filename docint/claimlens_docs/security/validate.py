"""Upload validation: PDF only, under 10 MB, checked by magic bytes."""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


class UploadRejected(ValueError):
    """Raised with a message that is safe to show to the user."""


def validate_upload(filename: str, data: bytes, max_bytes: int = MAX_UPLOAD_BYTES) -> None:
    """Raise UploadRejected if the file is not an acceptable PDF."""
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        size_mb = len(data) / (1024 * 1024)
        raise UploadRejected(
            f"The file is {size_mb:.1f} MB. The limit is {max_bytes // (1024 * 1024)} MB."
        )
    if not filename.lower().endswith(".pdf"):
        raise UploadRejected("Only PDF files are accepted.")
    # The extension can be faked; the first bytes of a real PDF cannot.
    if not data.startswith(PDF_MAGIC):
        raise UploadRejected("This file is named .pdf but is not a PDF.")
