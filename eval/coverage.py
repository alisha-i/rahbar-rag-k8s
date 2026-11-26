#!/usr/bin/env python3
"""Part E2 & E4: Query Coverage & Gap Detection Script (RAGOps Section 4.2.7).

Evaluates whether the golden evaluation set adequately covers real user queries:
- For each live query in `eval/live_queries.jsonl`, finds the highest cosine similarity
  match among the golden questions.
- Similarity uses the SAME pinned embedding model as the pipeline (EMBED_MODEL via Ollama at
  OLLAMA_URL). If Ollama is unreachable the script stops, unless ALLOW_FAKE_EMBEDDINGS=1
  (unit tests only), and the output says which embedder was used.
- Queries falling below the similarity threshold are flagged as uncovered gaps.
- Uncovered queries are grouped into thematic topics.
- Exits with code 1 if coverage is below the required 85% threshold (gap detected).
- Exits with code 0 if coverage rises above the threshold (e.g. after adding new golden questions in E4).

Usage:
    python eval/coverage.py --golden eval/golden.jsonl            # E2: exits 1 if coverage < 85%
    python eval/coverage.py --golden eval/golden_extended.jsonl   # E4: re-run after adding questions

Choosing the threshold: run once with --calibrate. It prints the similarity of each in-domain live
query to its best golden match and of each out-of-domain one; pick a value between the two groups
and justify it in the report.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if str(ROOT / "indexer") not in sys.path:
    sys.path.insert(0, str(ROOT / "indexer"))

from pipeline.config import get_settings  # noqa: E402
from pipeline.embed import embed_batch, embedder_in_use  # noqa: E402


def _embed_all(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):
        out.extend(embed_batch(texts[i : i + 16], settings))
    return out


def cosine_sim(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1)) or 1.0
    n2 = math.sqrt(sum(b * b for b in v2)) or 1.0
    return max(0.0, min(1.0, dot / (n1 * n2)))


def cluster_uncovered_topic(query_text: str) -> str:
    q = query_text.lower()
    if any(k in q for k in ["bus", "transport", "shuttle", "stop", "route"]):
        return "Campus Shuttle & Transport"
    elif any(k in q for k in ["hostel", "dormitory", "mess", "room"]):
        return "Hostel & Residential Accommodation"
    elif any(k in q for k in ["cricket", "ground", "floodlight", "match", "tournament"]):
        return "Sports Complex & Ground Bookings"
    elif any(k in q for k in ["gym", "gymnasium", "workout", "fitness"]):
        return "Student Gymnasium & Fitness Center"
    elif any(k in q for k in ["cafeteria", "lunch", "menu", "meal", "coupon"]):
        return "Cafeteria & Dining Services"
    elif any(k in q for k in ["clinic", "medical", "hospital", "dental", "doctor"]):
        return "Health Center & Emergency Medical Care"
    elif any(k in q for k in ["parking", "sticker", "vehicle"]):
        return "Campus Security & Parking Facilities"
    elif any(k in q for k in ["copyright", "patent", "intellectual property", "capstone"]):
        return "Intellectual Property & Thesis Copyright"
    elif any(k in q for k in ["installment", "financial aid", "voucher"]):
        return "Fee Installments & Emergency Aid"
    return "Miscellaneous Uncategorized Inquiries"


def evaluate_coverage(
    live_queries_path: Path,
    golden_path: Path,
    threshold: float = 0.70,
) -> tuple[float, list[dict[str, Any]], dict[str, list[str]]]:
    live_lines = live_queries_path.read_text(encoding="utf-8").strip().splitlines()
    live_queries = [json.loads(l) for l in live_lines if l.strip()]

    golden_lines = golden_path.read_text(encoding="utf-8").strip().splitlines()
    golden_questions = [json.loads(l) for l in golden_lines if l.strip()]

    # Embed golden and live questions with the pinned model
    g_vecs = _embed_all([g["question"] for g in golden_questions])
    golden_vecs = [(g["id"], g["question"], v) for g, v in zip(golden_questions, g_vecs)]
    live_vecs = _embed_all([lq["query"] for lq in live_queries])

    results = []
    uncovered_by_topic: dict[str, list[str]] = {}
    covered_count = 0

    for lq, q_vec in zip(live_queries, live_vecs):
        q_text = lq["query"]

        best_sim = -1.0
        best_golden_id = ""
        best_golden_q = ""

        for gid, gtext, gvec in golden_vecs:
            sim = cosine_sim(q_vec, gvec)
            if sim > best_sim:
                best_sim = sim
                best_golden_id = gid
                best_golden_q = gtext

        is_covered = best_sim >= threshold
        if is_covered:
            covered_count += 1
        else:
            topic = cluster_uncovered_topic(q_text)
            uncovered_by_topic.setdefault(topic, []).append(q_text)

        results.append({
            "id": lq["id"],
            "query": q_text,
            "domain": lq.get("domain", "unknown"),
            "best_match_id": best_golden_id,
            "best_match_question": best_golden_q,
            "similarity": round(best_sim, 4),
            "covered": is_covered,
        })

    coverage_share = covered_count / max(1, len(live_queries))
    return coverage_share, results, uncovered_by_topic


def print_coverage_report(
    coverage_share: float,
    uncovered_by_topic: dict[str, list[str]],
    total_queries: int,
    threshold: float,
    golden_name: str,
    target: float = 0.85,
) -> None:
    print("\n" + "=" * 76)
    print("        RAGOps QUERY COVERAGE & GAP DETECTION REPORT (Part E2 & E4)")
    print(f"        Golden Set: {golden_name} | Similarity Threshold: {threshold:.2f}")
    print("=" * 76)

    print(f"\n[COVERAGE SUMMARY]")
    print(f"  • Total Live Queries Scanned : {total_queries}")
    print(f"  • Queries Covered (>= {threshold:.2f})  : {int(coverage_share * total_queries)} / {total_queries}")
    print(f"  • Query Coverage Score       : \033[1m{coverage_share:.1%}\033[0m (Target: >= {target:.1%})")

    if uncovered_by_topic:
        print(f"\n[UNCOVERED QUERY GAPS GROUPED BY TOPIC]")
        for topic, queries in uncovered_by_topic.items():
            print(f"\n  📁 Topic: \033[93m{topic}\033[0m ({len(queries)} queries)")
            for q in queries:
                print(f"     - \"{q}\"")
    else:
        print("\n  \033[92mAll live queries are adequately covered by the golden set!\033[0m")

    print("\n" + "-" * 76)
    if coverage_share >= target:
        print(f"  COVERAGE VERDICT: \033[92m[PASS]\033[0m — Coverage is {coverage_share:.1%} >= {target:.1%}.")
    else:
        print(f"  COVERAGE VERDICT: \033[91m[GAP DETECTED]\033[0m — Coverage {coverage_share:.1%} < {target:.1%}. Gap must be closed.")
    print("=" * 76 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Part E2 & E4: Compute query coverage and detect topic gaps.")
    parser.add_argument("--live", default="eval/live_queries.jsonl", help="Path to live queries JSONL")
    parser.add_argument("--golden", default="eval/golden.jsonl", help="Path to golden set JSONL")
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("COVERAGE_THRESHOLD", 0.75)),
                        help="Cosine similarity a live query needs to its best golden question to count as covered")
    parser.add_argument("--target", type=float, default=0.85, help="Required covered share (paper example: 85%%)")
    parser.add_argument("--calibrate", action="store_true", help="Print best-match similarity per domain and exit")
    parser.add_argument("--out-dir", default=os.environ.get("RESULTS_DIR", "eval/results"))
    args = parser.parse_args()

    live_path = Path(args.live)
    golden_path = Path(args.golden)

    if not live_path.exists() or not golden_path.exists():
        print("Error: Input files not found.", file=sys.stderr)
        return 2

    share, results, uncovered_by_topic = evaluate_coverage(live_path, golden_path, threshold=args.threshold)
    print(f"[embedder: {embedder_in_use()} / {get_settings().embed_model}]")

    if args.calibrate:
        for dom in sorted({r["domain"] for r in results}):
            sims = sorted(r["similarity"] for r in results if r["domain"] == dom)
            print(f"{dom:>14}: n={len(sims)} min={sims[0]:.3f} median={sims[len(sims)//2]:.3f} max={sims[-1]:.3f}")
        return 0

    print_coverage_report(share, uncovered_by_topic, len(results), args.threshold, golden_path.name, args.target)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    out = out_dir / f"coverage_{golden_path.stem}_{stamp}.json"
    out.write_text(json.dumps({
        "golden": str(golden_path), "live": str(live_path), "threshold": args.threshold,
        "target": args.target, "embedder": embedder_in_use(), "embed_model": get_settings().embed_model,
        "coverage": round(share, 4), "covered": sum(1 for r in results if r["covered"]), "total": len(results),
        "uncovered_by_topic": uncovered_by_topic, "per_query": results,
    }, indent=2), encoding="utf-8")
    print(f"[+] wrote {out}")

    # Non-zero exit when coverage is below the target (E4)
    return 0 if share >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
