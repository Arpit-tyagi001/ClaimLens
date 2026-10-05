"""Parsers for Indian dates and rupee amounts.

Indian documents write the day first: "05/03/2025" means 5 March 2025,
never May 3. Amounts use lakh grouping: "Rs. 1,25,000/-" is 125000.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# 05/03/2025, 5-3-2025, 05.03.2025, 05/03/25
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
# 5 March 2025, 05-Mar-2025, 5th Mar, 2025
_TEXT_DATE = re.compile(
    r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/]*([A-Za-z]+)\.?[\s\-/,]*(\d{4})$"
)
# 2025-03-05 (ISO, year first, unambiguous)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_indian_date(text: str) -> date:
    """Parse a day-first date. Raises ValueError if it is not a real date."""
    s = text.strip()

    m = _ISO_DATE.match(s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = _NUMERIC_DATE.match(s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        return date(year, month, day)  # date() rejects 31/02 or 29/02 in a non-leap year

    m = _TEXT_DATE.match(s)
    if m:
        day, month_word, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month_number = MONTHS.get(month_word[:4]) or MONTHS.get(month_word[:3])
        if month_number is None:
            raise ValueError(f"Unknown month name in date: {text!r}")
        return date(year, month_number, day)

    raise ValueError(f"Not a recognised date: {text!r}")


# Optional currency prefix, then digits with commas, optional paise, optional "/-"
_AMOUNT = re.compile(
    r"^(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*)(\.\d{1,2})?\s*(?:/-)?$",
    re.IGNORECASE,
)


def parse_inr_amount(text: str) -> Decimal:
    """Parse "Rs. 1,25,000/-", "₹ 50,000.50" or "INR 2,00,000" into a Decimal."""
    s = text.strip()
    m = _AMOUNT.match(s)
    if not m:
        raise ValueError(f"Not a recognised rupee amount: {text!r}")
    whole = m.group(1).replace(",", "")
    paise = m.group(2) or ""
    try:
        return Decimal(whole + paise)
    except InvalidOperation as exc:
        raise ValueError(f"Not a recognised rupee amount: {text!r}") from exc
