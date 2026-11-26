#!/usr/bin/env python3
"""Part C2: Release gate (RAGOps 4.2.6).

Reads thresholds from a YAML file (eval/gate_config.yaml locally, the
`rahbar-gate-thresholds` ConfigMap mounted at /etc/gate/gate_config.yaml on the cluster),
compares a candidate result file with a baseline result file, prints every check, and exits
0 (PASS) or 1 (FAIL). Changing a threshold in the ConfigMap changes the outcome with no rebuild.

Usage:
    python eval/gate.py --result eval/results/latest_candidate.json --baseline eval/results/latest_baseline.json
    python eval/gate.py --result eval/results/latest_failing_candidate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def load_thresholds(config_path: Path) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return dict(config.get("gate_thresholds", {}))


def evaluate_gate(
    result: dict[str, Any], baseline: dict[str, Any] | None, thresholds: dict[str, Any]
) -> tuple[bool, list[str], list[str]]:
    """Returns (passed, violations, all_check_lines)."""
    comp = result.get("component_level", {})
    e2e = result.get("end_to_end_level", {})
    b_comp = (baseline or {}).get("component_level", {})
    b_e2e = (baseline or {}).get("end_to_end_level", {})

    violations: list[str] = []
    lines: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            violations.append(f"{name}: {detail}")

    # 1. recall@5 may not drop more than N points against the baseline
    max_drop = float(thresholds.get("max_recall5_drop", 0.02))
    r5 = float(comp.get("recall_at_5", 0.0))
    if baseline:
        drop = float(b_comp.get("recall_at_5", 0.0)) - r5
        check("recall@5 drop vs baseline", drop <= max_drop + 1e-9,
              f"{drop:+.4f} (baseline {b_comp.get('recall_at_5')}, candidate {r5}, max drop {max_drop})")
    else:
        lines.append(f"[SKIP] recall@5 drop vs baseline: no baseline given (candidate recall@5 = {r5})")

    # 2. minimum faithfulness
    min_faith = float(thresholds.get("min_faithfulness", 0.85))
    faith = e2e.get("faithfulness")
    check("faithfulness", faith is not None and float(faith) >= min_faith, f"{faith} (min {min_faith})")

    # 3. p95 latency ceiling
    max_p95 = float(thresholds.get("max_p95_latency_ms", 15000))
    p95 = float(e2e.get("latency_p95_ms", 0.0))
    check("p95 latency", p95 <= max_p95, f"{p95:.0f} ms (max {max_p95:.0f} ms)")

    # 4. abstention correctness: absolute floor, and not lower than the baseline
    abst = float(e2e.get("abstention_correctness", 0.0))
    min_abst = float(thresholds.get("min_abstention_correctness", 1.0))
    check("abstention correctness", abst >= min_abst, f"{abst} (min {min_abst})")
    if baseline and thresholds.get("abstention_not_below_baseline", True):
        b_abst = float(b_e2e.get("abstention_correctness", 0.0))
        check("abstention vs baseline", abst >= b_abst - 1e-9, f"{abst} (baseline {b_abst})")

    # 5. citation validity
    min_cit = float(thresholds.get("min_citation_validity", 0.95))
    cit = float(e2e.get("citation_validity", 0.0))
    check("citation validity", cit >= min_cit, f"{cit} (min {min_cit})")

    # 6. optional MRR floor
    if "min_mrr" in thresholds:
        min_mrr = float(thresholds["min_mrr"])
        mrr = float(comp.get("mrr", 0.0))
        check("MRR", mrr >= min_mrr, f"{mrr} (min {min_mrr})")

    return (not violations), violations, lines


def apply_gate(result_path: Path, baseline_path: Path | None, config_path: Path) -> tuple[bool, list[str]]:
    """File-based wrapper, kept for the tests and the CLI."""
    result = json.loads(result_path.read_text(encoding="utf-8"))
    baseline = None
    if baseline_path and baseline_path.exists() and baseline_path.resolve() != result_path.resolve():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    passed, violations, _ = evaluate_gate(result, baseline, load_thresholds(config_path))
    return passed, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the RAGOps release gate.")
    parser.add_argument("--result", required=True, help="Candidate evaluation JSON")
    parser.add_argument("--baseline", default="eval/results/latest_baseline.json", help="Baseline evaluation JSON")
    parser.add_argument("--config", default="eval/gate_config.yaml", help="Gate thresholds YAML")
    args = parser.parse_args()

    result_path = Path(args.result)
    baseline_path = Path(args.baseline) if args.baseline else None
    config_path = Path(args.config)
    if not result_path.exists():
        print(f"Error: result file not found: {result_path}", file=sys.stderr)
        return 2

    result = json.loads(result_path.read_text(encoding="utf-8"))
    baseline = None
    if baseline_path and baseline_path.exists() and baseline_path.resolve() != result_path.resolve():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    thresholds = load_thresholds(config_path)
    passed, violations, lines = evaluate_gate(result, baseline, thresholds)

    print("=" * 66)
    print("RAGOps RELEASE GATE")
    print(f"candidate : {result_path}  ({result.get('label', result.get('pipeline_mode'))}, {result.get('timestamp')})")
    print(f"baseline  : {baseline_path if baseline else 'N/A'}")
    print(f"thresholds: {config_path}  {thresholds}")
    print("-" * 66)
    for line in lines:
        print("  " + line)
    print("-" * 66)
    print("GATE RESULT: PASS - candidate may be rolled out" if passed
          else f"GATE RESULT: FAIL - {len(violations)} violation(s); rollout blocked")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
