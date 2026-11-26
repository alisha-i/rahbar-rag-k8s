"""Part B and C: the three-level suite's building blocks and the release gate (offline)."""

from __future__ import annotations

import json
from pathlib import Path

from eval.gate import apply_gate, evaluate_gate
from eval.run_eval_v2 import _rank_metrics, add_typos, run_module_level_test

ROOT = Path(__file__).resolve().parents[2]


def test_module_level_runs_and_is_deterministic() -> None:
    res = run_module_level_test(ROOT / "eval/fixtures/embedding_benchmark.json")
    assert res["paraphrase_pairs"] >= 15 and res["unrelated_pairs"] >= 15
    assert res["determinism_max_diff"] == 0.0
    assert res["cosine_gap"] > 0


def test_rank_metrics_on_first_relevant_rank() -> None:
    q = {"ground_truth_doc": "rules.md", "ground_truth_page": 1}
    hits = [{"source": "fees.md", "page": 1}, {"source": "rules.md", "page": 1}]
    m = _rank_metrics(hits, q)
    assert m["recall_at_3"] == 1.0 and m["mrr"] == 0.5
    assert abs(m["ndcg_at_5"] - 1 / 1.584962500721156) < 1e-9
    assert _rank_metrics([{"source": "rules.md", "page": 4}], q)["recall_at_5"] == 0.0


def test_typo_noise_is_deterministic_and_changes_the_query() -> None:
    q = "What is the minimum attendance percentage required?"
    assert add_typos(q) == add_typos(q) != q


def _result(recall5: float, faith: float, abst: float, cit: float, p95: float) -> dict:
    return {"component_level": {"recall_at_5": recall5, "mrr": 0.9},
            "end_to_end_level": {"faithfulness": faith, "abstention_correctness": abst,
                                 "citation_validity": cit, "latency_p95_ms": p95}}


def test_gate_passes_a_good_candidate_and_fails_a_bad_one(tmp_path: Path) -> None:
    cfg = tmp_path / "gate.yaml"
    cfg.write_text("gate_thresholds:\n  max_recall5_drop: 0.02\n  min_faithfulness: 0.85\n"
                   "  max_p95_latency_ms: 15000\n  min_abstention_correctness: 1.0\n  min_citation_validity: 0.95\n")
    base, good, bad = tmp_path / "b.json", tmp_path / "g.json", tmp_path / "x.json"
    base.write_text(json.dumps(_result(0.90, 0.92, 1.0, 1.0, 9000)))
    good.write_text(json.dumps(_result(0.91, 0.90, 1.0, 1.0, 9500)))
    bad.write_text(json.dumps(_result(0.70, 0.60, 0.8, 0.9, 20000)))
    assert apply_gate(good, base, cfg) == (True, [])
    passed, violations = apply_gate(bad, base, cfg)
    assert passed is False and len(violations) >= 4


def test_changing_a_threshold_changes_the_outcome() -> None:
    res = _result(0.9, 0.90, 1.0, 1.0, 9000)
    assert evaluate_gate(res, None, {"min_faithfulness": 0.85})[0] is True
    assert evaluate_gate(res, None, {"min_faithfulness": 0.99})[0] is False
