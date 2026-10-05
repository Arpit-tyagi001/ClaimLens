"""Injected-fault benchmark: fabricated citations the verifier must catch.

We build a fixed set of probes from the policy text. Real quotes must pass the
grounding check; fake ones must fail. Everything is deterministic so the
numbers are the same on every run.
"""

import re
from dataclasses import dataclass

CLAUSE_LINE = re.compile(r"^(\d+\.\d+)\s+(.+)$", re.M)

# Clauses that sound plausible but do not exist in the synthetic policy.
INVENTED = [
    "Claims for any hospitalisation in the first policy year are not payable.",
    "All treatment taken outside a network hospital is permanently excluded.",
    "The Company may reject any claim submitted more than 7 days after discharge.",
    "Day care procedures are covered only up to Rs. 10,000/- per policy year.",
    "Any disease diagnosed at any time before the policy started is a pre-existing disease.",
]

# Word swaps that flip the meaning of a real sentence.
NEGATIONS = [
    ("shall be excluded", "shall be covered"),
    ("unless it is for", "including when it is for"),
    ("may reject the claim only if", "may reject the claim if"),
    ("the rest of the claim remains payable", "the whole claim is not payable"),
]


@dataclass(frozen=True)
class Probe:
    kind: str  # real, real_reformatted, real_typo, invented, number_changed, negated, spliced
    clause_id: str | None
    quote: str
    is_fake: bool


def _clauses(policy_text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2).strip()) for m in CLAUSE_LINE.finditer(policy_text)]


def _change_first_number(text: str) -> str | None:
    m = re.search(r"\d+", text)
    if not m:
        return None
    changed = str(int(m.group(0)) * 2)
    return text[: m.start()] + changed + text[m.end():]


def _strip_punctuation(text: str) -> str:
    """How a quote looks after an LLM drops punctuation and casing."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # "40,000" -> "40000", still one number
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", text)).strip().lower()


def _swap_letters(text: str) -> str | None:
    """A one-character slip (OCR or typing) in the longest plain word."""
    words = [w for w in re.findall(r"[A-Za-z]{7,}", text)]
    if not words:
        return None
    word = max(words, key=len)
    typo = word[:2] + word[3] + word[2] + word[4:]
    return text.replace(word, typo, 1)


def build_probes(policy_text: str) -> list[Probe]:
    clauses = _clauses(policy_text)
    probes = [Probe("real", cid, text, False) for cid, text in clauses]

    # Real quotes with harmless differences: these must still be accepted.
    probes += [Probe("real_reformatted", cid, _strip_punctuation(text), False) for cid, text in clauses]
    for cid, text in clauses:
        typo = _swap_letters(text)
        if typo:
            probes.append(Probe("real_typo", cid, typo, False))

    probes += [Probe("invented", None, text, True) for text in INVENTED]

    for cid, text in clauses:
        changed = _change_first_number(text)
        if changed:
            probes.append(Probe("number_changed", cid, changed, True))

    for cid, text in clauses:
        for old, new in NEGATIONS:
            if old in text:
                probes.append(Probe("negated", cid, text.replace(old, new), True))

    # First half of one clause glued to the second half of the next one.
    for (cid_a, a), (_, b) in zip(clauses, clauses[1:], strict=False):
        a_words, b_words = a.split(), b.split()
        spliced = " ".join(a_words[: len(a_words) // 2] + b_words[len(b_words) // 2:])
        probes.append(Probe("spliced", cid_a, spliced, True))

    return probes


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("–", "-")
    return re.sub(r"\s+", " ", text).strip()


def exact_match_grounding(quote: str, policy_text: str) -> bool:
    """Fallback grounding check: is the quote in the policy, ignoring case and spacing?

    This is NOT Lead A's verifier. Pass the real locate_quote to run.py with
    --grounding to measure the actual system.
    """
    return _normalise(quote) in _normalise(policy_text)


def run_benchmark(policy_text: str, grounding_fn=exact_match_grounding) -> dict:
    probes = build_probes(policy_text)
    caught = missed = real_passed = false_alarms = 0
    missed_examples, false_alarm_examples = [], []
    by_kind: dict[str, list[int]] = {}
    real_by_kind: dict[str, list[int]] = {}

    for probe in probes:
        grounded = bool(grounding_fn(probe.quote, policy_text))
        if probe.is_fake:
            hit = not grounded
            caught += hit
            missed += not hit
            if not hit:
                missed_examples.append({"kind": probe.kind, "quote": probe.quote})
            stats = by_kind.setdefault(probe.kind, [0, 0])
            stats[0] += hit
            stats[1] += 1
        else:
            real_passed += grounded
            false_alarms += not grounded
            if not grounded:
                false_alarm_examples.append({"kind": probe.kind, "quote": probe.quote})
            stats = real_by_kind.setdefault(probe.kind, [0, 0])
            stats[0] += grounded
            stats[1] += 1

    fakes = caught + missed
    reals = real_passed + false_alarms
    return {
        "fakes_caught": f"{caught} of {fakes}",
        "catch_rate": caught / fakes if fakes else 0.0,
        "real_quotes_accepted": f"{real_passed} of {reals}",
        "false_alarms": false_alarms,
        "by_kind": {k: f"{v[0]} of {v[1]}" for k, v in by_kind.items()},
        "real_by_kind": {k: f"{v[0]} of {v[1]}" for k, v in real_by_kind.items()},
        "missed_examples": missed_examples,
        "false_alarm_examples": false_alarm_examples,
    }
