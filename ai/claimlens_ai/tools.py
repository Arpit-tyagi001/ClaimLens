from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .retrieval import InMemoryRetriever, Hit


@dataclass
class RuleCheckResult:
    rule: str
    passed: bool
    details: str


class ClaimLensTools:
    """
    Thin tool layer used by the investigator.

    Later these functions will call M4's real
    policy retrieval and rule-checking functions.
    """

    def __init__(self, retriever: InMemoryRetriever):
        self.retriever = retriever

    def search_policy(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[Hit]:

        return self.retriever.search(
            query=query,
            top_k=top_k,
        )

    def get_section(
        self,
        section_path: str,
    ) -> list[Hit]:

        return [
            chunk
            for chunk in self.retriever.chunks
            if chunk.section_path.lower() == section_path.lower()
        ]

    def get_definition(
        self,
        term: str,
    ) -> list[Hit]:

        query = f"definition {term}"

        return self.search_policy(
            query=query,
            top_k=3,
        )

    def run_rule_check(
        self,
        rule: str,
        facts: dict[str, Any],
    ) -> RuleCheckResult:

        # Temporary deterministic placeholder.
        # M4's actual rule functions will replace this.

        return RuleCheckResult(
            rule=rule,
            passed=False,
            details="Rule engine not connected yet.",
        )