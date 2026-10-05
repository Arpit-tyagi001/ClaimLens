import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from docint.claimlens_docs.security import (
    MAX_UPLOAD_BYTES,
    UploadRejected,
    is_expired,
    purge_expired,
    redact,
    validate_upload,
)

# All values below are made up for testing.


# --- redact ---

@pytest.mark.parametrize("text, label", [
    ("Call me on 9876543210", "PHONE"),
    ("Call me on +91 98765 43210", "PHONE"),
    ("Call me on 098765-43210", "PHONE"),
    ("Mail test.user@example.com now", "EMAIL"),
    ("Aadhaar 2345 6789 0123", "AADHAAR"),
    ("Aadhaar 234567890123", "AADHAAR"),
    ("PAN ABCDE1234F", "PAN"),
])
def test_redact_masks_each_type(text, label):
    result = redact(text)
    assert f"[{label}]" in result.text
    assert result.counts == {label: 1}


def test_redact_keeps_claim_data_the_pipeline_needs():
    text = "Claim No: CLM/2025/0001, amount Rs. 1,25,000/-, admitted on 10/01/2025, Clause 4.2"
    assert redact(text).text == text


def test_redact_counts_multiple():
    result = redact("9876543210 and 9123456780, ABCDE1234F")
    assert result.counts == {"PHONE": 2, "PAN": 1}
    assert "9876543210" not in result.text


# --- validate_upload ---

PDF = b"%PDF-1.7\n...rest of file..."


def test_valid_pdf_passes():
    validate_upload("policy.pdf", PDF)
    validate_upload("POLICY.PDF", PDF)


@pytest.mark.parametrize("filename, data, message", [
    ("policy.pdf", b"", "empty"),
    ("policy.docx", PDF, "Only PDF"),
    ("fake.pdf", b"MZ\x90\x00 an exe renamed to pdf", "not a PDF"),
    ("fake.pdf", b"<html>not a pdf</html>", "not a PDF"),
])
def test_bad_uploads_are_rejected_with_clear_message(filename, data, message):
    with pytest.raises(UploadRejected, match=message):
        validate_upload(filename, data)


def test_oversize_upload_rejected():
    big = PDF + b"0" * MAX_UPLOAD_BYTES
    with pytest.raises(UploadRejected, match="limit is 10 MB"):
        validate_upload("big.pdf", big)


# --- purge ---

def test_is_expired():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    assert is_expired(now - timedelta(hours=25), now)
    assert not is_expired(now - timedelta(hours=23), now)


def test_purge_deletes_only_old_files(tmp_path):
    old = tmp_path / "case1" / "old.pdf"
    new = tmp_path / "case2" / "new.pdf"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_bytes(PDF)
    new.write_bytes(PDF)
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))

    deleted = purge_expired(tmp_path, retention=timedelta(hours=24))

    assert deleted == [old]
    assert not old.exists()
    assert new.exists()
