"""Mask personal data before any text is sent to an LLM.

Patterns are deliberately broad: masking a harmless number by mistake is
cheap, while leaking a real Aadhaar or PAN is not.
"""

import re
from dataclasses import dataclass, field

# Order matters: email first so its digits are not caught as a phone number,
# Aadhaar (12 digits) before phone (10 digits) so it is not split.
PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("AADHAAR", re.compile(r"\b[2-9][0-9]{3}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[\s-]?|0)?[6-9][0-9]{4}[\s-]?[0-9]{5}(?!\d)")),
]


@dataclass
class RedactionResult:
    text: str
    counts: dict = field(default_factory=dict)


def redact(text: str) -> RedactionResult:
    """Replace each match with a tag such as [PAN] and count what was masked."""
    counts = {}
    for label, pattern in PATTERNS:
        text, n = pattern.subn(f"[{label}]", text)
        if n:
            counts[label] = n
    return RedactionResult(text=text, counts=counts)
