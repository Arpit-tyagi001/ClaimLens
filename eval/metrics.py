"""Small, explicit metric functions. Results are reported as exact counts."""

from dataclasses import dataclass


@dataclass
class Count:
    hit: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.hit += int(ok)

    @property
    def ratio(self) -> float:
        return self.hit / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.hit} of {self.total}"


def letter_mismatches(extracted: dict, gold: dict) -> list[str]:
    """Compare a LetterExtraction.to_dict() with a gold.json. Empty list = match."""
    problems = []
    for name, expected in gold["letter_fields"].items():
        got = extracted.get(name)
        if str(got) != str(expected):
            problems.append(f"{name}: expected {expected!r}, got {got!r}")
    expected_categories = [r["category"] for r in gold["reasons"]]
    got_categories = [r["category"] for r in extracted["reasons"]]
    if got_categories != expected_categories:
        problems.append(f"reason categories: expected {expected_categories}, got {got_categories}")
    return problems


def recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int = 3) -> bool | None:
    """True if any gold clause is in the top k. None if there is no gold clause."""
    if not gold_ids:
        return None
    return any(cid in gold_ids for cid in retrieved_ids[:k])


def case_verdict_agrees(predicted: list[str], gold_reasons: list[dict]) -> bool:
    """A case agrees only if every reason's verdict matches its gold label."""
    expected = [r["gold_verdict"] for r in gold_reasons]
    return list(predicted) == expected
