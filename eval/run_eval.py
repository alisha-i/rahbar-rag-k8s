#!/usr/bin/env python3
"""Score a running RAHBAR against a golden set, and gate CI on the result (section 5).

    python eval/run_eval.py --api http://<ingress>                 # your corpus
    python eval/run_eval.py --api ... --golden eval/fixtures/golden.fixture.yaml
    python eval/run_eval.py --api ... --update-baseline            # after a deliberate improvement

Exit code 0 when every gate passes, 1 when any fails. CI relies on that exit code.

Given: talking to the API, reading the golden set, writing results.json, the exit
code. Yours: the evaluation functions (recall@k, citation validity, summarise, and gate).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent

# The gate from section 5.4. Change these only with a reason written in docs/PIPELINE.md.
MAX_RECALL5_DROP = 0.05        # recall@5 may fall at most 5 points below the baseline
MIN_CITATION_VALIDITY = 0.95   # at least 95% of citations must match retrieved passages
REQUIRED_ABSTENTION = 1.0      # every must-abstain question must be declined


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not questions:
        sys.exit(f"no questions in {path}")
    return questions


def ask(client: httpx.Client, api: str, question: str) -> dict:
    """POST one question and return the AskResponse as a dict (see api/app/schemas.py)."""
    response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
    response.raise_for_status()
    return response.json()


def _match_section(expected_sec: str, candidate_sec: str) -> bool:
    """Case-insensitive substring or normalized match for section headings."""
    exp = expected_sec.strip().lower()
    cand = candidate_sec.strip().lower()
    return exp in cand or cand in exp


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    """Fraction of the relevant (source, section) pairs found in the top k retrieved.
    Return None when the question has no relevant passages (must-abstain questions)."""
    if not relevant:
        return None

    top_k_retrieved = retrieved[:k]
    matches = 0
    for rel in relevant:
        rel_source = rel.get("source", "").lower()
        rel_sec = rel.get("section", "")
        for ret in top_k_retrieved:
            ret_source = ret.get("source", "").lower()
            ret_sec = ret.get("section", "")
            if rel_source == ret_source and _match_section(rel_sec, ret_sec):
                matches += 1
                break

    return matches / len(relevant)


def citation_validity(response: dict) -> float | None:
    """Fraction of the response's citations that match a passage in response['retrieved'].
    Return None when there are no citations."""
    citations = response.get("citations") or []
    if not citations:
        return None

    retrieved = response.get("retrieved") or []
    retrieved_pairs = {(r.get("source", "").lower(), r.get("section", "").strip().lower()) for r in retrieved}

    valid = 0
    for c in citations:
        c_src = c.get("source", "").lower()
        c_sec = c.get("section", "").strip().lower()
        matched = False
        for r_src, r_sec in retrieved_pairs:
            if c_src == r_src and (c_sec in r_sec or r_sec in c_sec):
                matched = True
                break
        if matched:
            valid += 1

    return valid / len(citations)


def summarise(rows: list[dict]) -> dict:
    """Overall recall@3, recall@5, citation validity and abstention correctness, plus
    recall@5 broken down by question type. This dict becomes results.json."""
    r3_scores: list[float] = []
    r5_scores: list[float] = []
    r5_by_type: dict[str, list[float]] = {}
    cit_scores: list[float] = []
    abstain_correct = 0
    abstain_total = 0

    for row in rows:
        resp = row.get("response", {})
        retrieved = resp.get("retrieved", [])
        rel = row.get("relevant", [])
        q_type = row.get("type", "ordinary")

        # Abstention check
        if row.get("must_abstain"):
            abstain_total += 1
            if not resp.get("answered_from_context", True):
                abstain_correct += 1

        # Recall computation
        r3 = recall_at_k(rel, retrieved, 3)
        r5 = recall_at_k(rel, retrieved, 5)
        if r3 is not None:
            r3_scores.append(r3)
        if r5 is not None:
            r5_scores.append(r5)
            r5_by_type.setdefault(q_type, []).append(r5)

        # Citation validity
        cv = citation_validity(resp)
        if cv is not None:
            cit_scores.append(cv)

    mean_r3 = round(sum(r3_scores) / len(r3_scores), 4) if r3_scores else 0.0
    mean_r5 = round(sum(r5_scores) / len(r5_scores), 4) if r5_scores else 0.0
    mean_cv = round(sum(cit_scores) / len(cit_scores), 4) if cit_scores else 1.0
    abstention_rate = round(abstain_correct / abstain_total, 4) if abstain_total else 1.0

    breakdown = {t: round(sum(vals) / len(vals), 4) for t, vals in r5_by_type.items()}

    return {
        "recall@3": mean_r3,
        "recall@5": mean_r5,
        "recall@5_by_type": breakdown,
        "citation_validity": mean_cv,
        "abstention_correctness": abstention_rate,
    }


def gate(results: dict, baseline: dict | None) -> list[str]:
    """Return one message per failed gate (section 5.4); an empty list means pass."""
    failures: list[str] = []

    # Gate 1: Regression check against baseline recall@5
    if baseline and "recall@5" in baseline:
        allowed_floor = baseline["recall@5"] - MAX_RECALL5_DROP
        if results.get("recall@5", 0.0) < allowed_floor:
            failures.append(
                f"recall@5 ({results.get('recall@5'):.2%}) dropped more than {MAX_RECALL5_DROP:.0%} "
                f"below baseline ({baseline['recall@5']:.2%}; floor is {allowed_floor:.2%})"
            )

    # Gate 2: Citation validity check
    if results.get("citation_validity", 1.0) < MIN_CITATION_VALIDITY:
        failures.append(
            f"citation validity ({results.get('citation_validity'):.2%}) below minimum {MIN_CITATION_VALIDITY:.0%}"
        )

    # Gate 3: Abstention correctness check
    if results.get("abstention_correctness", 1.0) < REQUIRED_ABSTENTION:
        failures.append(
            f"abstention correctness ({results.get('abstention_correctness'):.2%}) below required {REQUIRED_ABSTENTION:.0%}"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a golden set.")
    parser.add_argument("--api", required=True, help="base URL, e.g. http://192.168.49.2")
    parser.add_argument("--host", help="Host header, when your Ingress rule names a host")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write these results as the new baseline")
    args = parser.parse_args()

    questions = load_golden(args.golden)
    rows = []
    headers = {"Host": args.host} if args.host else {}
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for q in questions:
            started = time.perf_counter()
            response = ask(client, args.api, q["question"])
            rows.append({
                "id": q["id"], "type": q.get("type", "ordinary"),
                "must_abstain": bool(q.get("must_abstain", False)),
                "relevant": q.get("relevant") or [],
                "expect": q.get("expect"),
                "response": response,
                "seconds": round(time.perf_counter() - started, 2),
            })
            print(f"  {q['id']:<5} {rows[-1]['seconds']:>6.2f}s  {q['question'][:60]}")

    try:
        results = summarise(rows)
        baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
        failures = gate(results, baseline)
    except NotImplementedError as exc:
        print(f"\nnot implemented yet: {exc}")
        return 2

    results["golden"] = str(args.golden)
    results["questions"] = len(rows)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {args.out}")
    if args.update_baseline:
        args.baseline.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"baseline updated: {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\ngate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
