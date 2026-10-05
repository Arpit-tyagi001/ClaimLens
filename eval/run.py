"""Run the ClaimLens evaluation on the synthetic cases.

    python -m eval.run
    python -m eval.run --predictions eval/predictions.json
    python -m eval.run --grounding exact          # old exact-match check, for comparison
    python -m eval.run --gate                     # exit 1 if a metric is below target (CI)

Without --predictions, retrieval and verdict metrics are reported as
"not measured" instead of being guessed.
"""

import argparse
import importlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from docint.claimlens_docs.grounding import quote_in_text
from docint.claimlens_docs.letter import extract_letter, extract_rejection
from docint.claimlens_docs.policy_facts import extract_policy_facts

from .faults import exact_match_grounding, run_benchmark
from .metrics import Count, case_verdict_agrees, letter_mismatches, recall_at_k

EVAL_DIR = Path(__file__).parent
CASES_DIR = EVAL_DIR / "cases"
POLICY_PATH = EVAL_DIR / "data" / "synthetic_policy.txt"
GOLD_FACTS_PATH = EVAL_DIR / "data" / "gold_policy_facts.json"

TARGETS = {  # from PRD section 5
    "letter_extraction": 7 / 8,
    "retrieval_recall_at_3": 0.80,
    "verdict_agreement": 6 / 8,
    "fault_catch_rate": 0.90,
}


def load_cases() -> list[dict]:
    cases = []
    for folder in sorted(p for p in CASES_DIR.iterdir() if p.is_dir()):
        gold = json.loads((folder / "gold.json").read_text(encoding="utf-8"))
        gold["letter"] = (folder / "letter.txt").read_text(encoding="utf-8")
        cases.append(gold)
    return cases


def eval_letters(cases: list[dict]) -> dict:
    count, failures = Count(), {}
    for case in cases:
        extracted = extract_letter(case["letter"]).to_dict()
        problems = letter_mismatches(extracted, case)
        count.add(not problems)
        if problems:
            failures[case["case_id"]] = problems
    return {"matched": str(count), "ratio": count.ratio, "failures": failures}


def _amount(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if value == int(value) else str(value)


def eval_letters_pdf(cases: list[dict]) -> dict:
    """Same check as eval_letters, but each letter goes PDF -> ingest -> extract_rejection."""
    try:
        from docint.claimlens_docs.ingest import ingest_document

        from .make_pdfs import render_text_pdf
    except ImportError as exc:
        return {"measured": False, "note": f"PyMuPDF not installed ({exc})"}

    count, failures = Count(), {}
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            pdf = render_text_pdf(case["letter"], Path(tmp) / f"{case['case_id']}.pdf")
            found = extract_rejection(ingest_document(str(pdf), case["case_id"], "letter"))
            as_letter = {
                "claim_number": found.claim_no,
                "policy_number": found.policy_no,
                "admission_date": found.admission_date,
                "discharge_date": found.discharge_date,
                "amount_claimed": _amount(found.claimed_amount),
                "reasons": [{"category": r.category} for r in found.reasons],
            }
            problems = letter_mismatches(as_letter, case)
            count.add(not problems)
            if problems:
                failures[case["case_id"]] = problems
    return {"measured": True, "matched": str(count), "ratio": count.ratio, "failures": failures}


def eval_policy_facts() -> dict:
    gold = json.loads(GOLD_FACTS_PATH.read_text(encoding="utf-8"))
    facts = extract_policy_facts(POLICY_PATH.read_text(encoding="utf-8"))
    count, failures = Count(), {}
    for name, expected in gold.items():
        fact = getattr(facts, name)
        if isinstance(expected, dict):
            got = None if fact is None else {"value": fact.value, "unit": fact.unit}
        else:
            got = None if fact is None else str(fact.value)
        count.add(got == expected)
        if got != expected:
            failures[name] = {"expected": expected, "got": got}
    return {"matched": str(count), "ratio": count.ratio, "failures": failures}


def eval_predictions(cases: list[dict], predictions: dict | None) -> dict:
    if predictions is None:
        return {"measured": False, "note": "Pass --predictions with pipeline output to measure these."}
    recall, verdicts, failures = Count(), Count(), {}
    for case in cases:
        pred = predictions.get(case["case_id"])
        if pred is None:
            failures[case["case_id"]] = "no prediction for this case"
            verdicts.add(False)
            continue
        pred_reasons = pred.get("reasons", [])
        for i, gold_reason in enumerate(case["reasons"]):
            retrieved = pred_reasons[i].get("retrieved_clause_ids", []) if i < len(pred_reasons) else []
            hit = recall_at_k(retrieved, gold_reason["gold_clause_ids"], k=3)
            if hit is not None:
                recall.add(hit)
        predicted_verdicts = [r.get("verdict") for r in pred_reasons]
        agrees = case_verdict_agrees(predicted_verdicts, case["reasons"])
        verdicts.add(agrees)
        if not agrees:
            failures[case["case_id"]] = {
                "expected": [r["gold_verdict"] for r in case["reasons"]],
                "got": predicted_verdicts,
            }
    return {
        "measured": True,
        "recall_at_3": str(recall),
        "recall_at_3_ratio": recall.ratio,
        "verdict_agreement": str(verdicts),
        "verdict_agreement_ratio": verdicts.ratio,
        "failures": failures,
    }


def load_grounding(spec: str | None):
    if not spec:
        return quote_in_text, "claimlens_docs.grounding.locate_quote (fuzzy match + key-word/number check)"
    if spec == "exact":
        return exact_match_grounding, "fallback exact-match check (not the real verifier)"
    module_name, func_name = spec.split(":")
    return getattr(importlib.import_module(module_name), func_name), spec


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--predictions", type=Path, help="JSON of pipeline output per case")
    parser.add_argument("--grounding", help="'exact', or module:function taking (quote, policy_text)")
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "report.json")
    parser.add_argument("--gate", action="store_true", help="exit 1 if any metric is below its target")
    args = parser.parse_args(argv)

    cases = load_cases()
    predictions = json.loads(args.predictions.read_text(encoding="utf-8")) if args.predictions else None
    grounding_fn, grounding_name = load_grounding(args.grounding)
    policy_text = POLICY_PATH.read_text(encoding="utf-8")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synthetic_data_only": True,
        "num_cases": len(cases),
        "supported_cases": sum(any(r["gold_verdict"] == "SUPPORTED" for r in c["reasons"]) for c in cases),
        "letter_extraction": eval_letters(cases),
        "letter_extraction_pdf": eval_letters_pdf(cases),
        "policy_facts": eval_policy_facts(),
        "pipeline": eval_predictions(cases, predictions),
        "fault_benchmark": {"grounding_fn": grounding_name, **run_benchmark(policy_text, grounding_fn)},
        "targets": TARGETS,
    }
    report = {**summary_fields(report), **report}
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"\nFull report written to {args.out}")
    if args.gate:
        failures = gate_failures(report)
        if failures:
            print("\nEVAL GATE FAILED:")
            for failure in failures:
                print(f"  - {failure}")
            raise SystemExit(1)
        print("\nEval gate passed.")
    return report


def _hits(count: str) -> tuple[int, int]:
    """'7 of 8' -> (7, 8)."""
    hit, total = count.split(" of ")
    return int(hit), int(total)


def summary_fields(report: dict) -> dict:
    """Flat fields in the shape of eval/report.sample.json, served by GET /api/eval/latest.

    Metrics that need the AI pipeline's output are None until --predictions is passed.
    """
    extraction = _hits(report["letter_extraction"]["matched"])
    faults = report["fault_benchmark"]
    grounding = _hits(faults["real_quotes_accepted"])
    injected = _hits(faults["fakes_caught"])
    pipe = report["pipeline"]
    recall = _hits(pipe["recall_at_3"]) if pipe["measured"] else (None, None)
    verdict = _hits(pipe["verdict_agreement"]) if pipe["measured"] else (None, None)
    notes = (
        f"Synthetic benchmark: {report['num_cases']} cases written by the team, "
        f"{report['supported_cases']} where the rejection is supported. "
        "extraction = rejection-letter fields and reason categories match gold. "
        "grounding = real policy quotes (verbatim, reformatted, one typo) accepted by locate_quote. "
        "injected = fabricated quotes (invented, number changed, negated, spliced) rejected by locate_quote."
    )
    if not pipe["measured"]:
        notes += " Retrieval recall@3 and verdict agreement are not measured yet (need the AI pipeline output)."
    return {
        "synthetic_benchmark": True,
        "n_cases": report["num_cases"],
        "extraction_correct": extraction[0],
        "extraction_total": extraction[1],
        "recall_at_3_hits": recall[0],
        "recall_at_3_total": recall[1],
        "grounding_ok": grounding[0],
        "grounding_total": grounding[1],
        "injected_caught": injected[0],
        "injected_total": injected[1],
        "verdict_agree": verdict[0],
        "verdict_total": verdict[1],
        "notes": notes,
    }


def gate_failures(report: dict) -> list[str]:
    """Every metric that is below its target. Used by CI as a regression gate."""
    t = report["targets"]
    failures = []
    if report["letter_extraction"]["ratio"] < t["letter_extraction"]:
        failures.append(f"letter extraction {report['letter_extraction']['matched']}")
    pdf = report["letter_extraction_pdf"]
    if pdf["measured"] and pdf["ratio"] < t["letter_extraction"]:
        failures.append(f"letter extraction from PDF {pdf['matched']}")
    faults = report["fault_benchmark"]
    if faults["catch_rate"] < t["fault_catch_rate"]:
        failures.append(f"fake citations caught {faults['fakes_caught']}")
    if faults["false_alarms"]:
        failures.append(f"{faults['false_alarms']} real quote(s) rejected as fake")
    pipe = report["pipeline"]
    if pipe["measured"]:
        if pipe["recall_at_3_ratio"] < t["retrieval_recall_at_3"]:
            failures.append(f"retrieval recall@3 {pipe['recall_at_3']}")
        if pipe["verdict_agreement_ratio"] < t["verdict_agreement"]:
            failures.append(f"verdict agreement {pipe['verdict_agreement']}")
    return failures


def print_summary(report: dict) -> None:
    def status(ratio, target):
        return "PASS" if ratio >= target else "BELOW TARGET"

    letters = report["letter_extraction"]
    letters_pdf = report["letter_extraction_pdf"]
    facts = report["policy_facts"]
    faults = report["fault_benchmark"]
    pipe = report["pipeline"]
    t = report["targets"]

    print(f"ClaimLens eval on {report['num_cases']} synthetic cases "
          f"({report['supported_cases']} where the rejection is supported)\n")
    print(f"{'Metric':<34}{'Result':<14}{'Target':<10}Status")
    print("-" * 70)
    print(f"{'Letter extraction matches gold':<34}{letters['matched']:<14}{'>= 7/8':<10}"
          f"{status(letters['ratio'], t['letter_extraction'])}")
    if letters_pdf["measured"]:
        print(f"{'  same, from rendered PDF':<34}{letters_pdf['matched']:<14}{'>= 7/8':<10}"
              f"{status(letters_pdf['ratio'], t['letter_extraction'])}")
    print(f"{'Policy facts match gold':<34}{facts['matched']:<14}{'-':<10}")
    if pipe["measured"]:
        print(f"{'Retrieval recall@3':<34}{pipe['recall_at_3']:<14}{'>= 80%':<10}"
              f"{status(pipe['recall_at_3_ratio'], t['retrieval_recall_at_3'])}")
        print(f"{'Verdict agrees with gold':<34}{pipe['verdict_agreement']:<14}{'>= 6/8':<10}"
              f"{status(pipe['verdict_agreement_ratio'], t['verdict_agreement'])}")
    else:
        print(f"{'Retrieval recall@3':<34}{'not measured':<14}{'>= 80%':<10}needs --predictions")
        print(f"{'Verdict agrees with gold':<34}{'not measured':<14}{'>= 6/8':<10}needs --predictions")
    print(f"{'Fake citations caught':<34}{faults['fakes_caught']:<14}{'>= 90%':<10}"
          f"{status(faults['catch_rate'], t['fault_catch_rate'])}")
    print(f"{'Real quotes accepted':<34}{faults['real_quotes_accepted']:<14}{'all':<10}")
    print(f"\nGrounding check used: {faults['grounding_fn']}")

    for title, section in (("Letter extraction", letters), ("Letter extraction (PDF)", letters_pdf),
                           ("Policy facts", facts)):
        if section.get("failures"):
            print(f"\n{title} failures:")
            for key, problem in section["failures"].items():
                print(f"  {key}: {problem}")


if __name__ == "__main__":
    main(sys.argv[1:])
