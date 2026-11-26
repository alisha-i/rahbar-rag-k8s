#!/usr/bin/env python3
"""Part D3: Trace Metrics Aggregation & Reporting (RAGOps Section 5.1, Table 2).

Summarizes operational metrics across query traces over a given time window:
- Top-1 retrieval similarity distribution (mean, min, max, p50, p95)
- Abstention rate (% queries declined)
- Cache hit rate (% queries served from Redis/memory)
- p95 latency per stage (cache, retrieval, generation, total)
- Number of prompt injection attempts flagged by guardrail (D4)

Usage:
    python scripts/trace_metrics.py
    python scripts/trace_metrics.py --file traces/query_traces.jsonl
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path


def percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(pct * len(s))
    return s[min(idx, len(s) - 1)]


def load_traces(source: str) -> list[dict]:
    """Read trace records from a JSONL file, or from `kubectl logs` output piped on stdin ("-").
    Non-trace lines (request logs, uvicorn output) are skipped; an old "[TRACE] " prefix is stripped."""
    if source == "-":
        lines = sys.stdin.read().splitlines()
    else:
        path = Path(source)
        if not path.exists():
            print(f"Error: trace log not found at {path}", file=sys.stderr)
            return []
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        lines = [ln for f in files for ln in f.read_text(encoding="utf-8").splitlines()]
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if line.startswith("[TRACE]"):
            line = line[len("[TRACE]"):].strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "trace_id" in rec:
            out.append(rec)
    out.sort(key=lambda r: str(r.get("timestamp", "")))  # several pod files -> one timeline
    return out


def _ts(rec: dict) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(rec["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def summarize_traces(source: str, since: datetime.datetime | None = None,
                     until: datetime.datetime | None = None) -> dict:
    traces = load_traces(source)
    if since or until:
        kept = []
        for t in traces:
            ts = _ts(t)
            if ts is None:
                continue
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            kept.append(t)
        traces = kept
    total_queries = len(traces)
    if total_queries == 0:
        return {}

    cache_hits = sum(1 for t in traces if t.get("cache_hit", False))
    def _abstained(t: dict) -> bool:
        if t.get("answered_from_context") is not None:
            return t["answered_from_context"] is False
        return not t.get("citations")  # older traces without the field

    abstentions = sum(1 for t in traces if _abstained(t))
    injections_flagged = sum(1 for t in traces if t.get("guardrail_actions", {}).get("injection_detected", False))

    top1_sims = []
    for t in traces:
        if t.get("cache_hit"):
            continue  # a cache hit did not run retrieval
        docs = t.get("retrieved_docs", [])
        if docs:
            top1_sims.append(max(d.get("score", 0.0) for d in docs))
    buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0}
    for v in top1_sims:
        key = "<0.3" if v < 0.3 else "0.3-0.5" if v < 0.5 else "0.5-0.7" if v < 0.7 else "0.7-0.9" if v < 0.9 else ">=0.9"
        buckets[key] += 1

    # Stage latencies
    cache_lats, ret_lats, gen_lats, tot_lats = [], [], [], []
    for t in traces:
        lats = t.get("latency_per_stage", {})
        if "cache_lookup_ms" in lats:
            cache_lats.append(lats["cache_lookup_ms"])
        if "retrieval_ms" in lats:
            ret_lats.append(lats["retrieval_ms"])
        if "generation_ms" in lats:
            gen_lats.append(lats["generation_ms"])
        if "total_ms" in lats:
            tot_lats.append(lats["total_ms"])

    return {
        "total_queries": total_queries,
        "cache_hits": cache_hits,
        "cache_hit_rate": cache_hits / total_queries,
        "abstentions": abstentions,
        "abstention_rate": abstentions / total_queries,
        "injections_flagged": injections_flagged,
        "top1_similarity": {
            "mean": sum(top1_sims) / len(top1_sims) if top1_sims else 0.0,
            "min": min(top1_sims) if top1_sims else 0.0,
            "max": max(top1_sims) if top1_sims else 0.0,
            "p50": percentile(top1_sims, 0.50),
            "p95": percentile(top1_sims, 0.95),
            "histogram": buckets,
        },
        "window": {
            "first": traces[0].get("timestamp"),
            "last": traces[-1].get("timestamp"),
        },
        "latencies_p95_ms": {
            "cache_lookup": percentile(cache_lats, 0.95),
            "retrieval": percentile(ret_lats, 0.95),
            "generation": percentile(gen_lats, 0.95),
            "total": percentile(tot_lats, 0.95),
        },
    }


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 70)
    print("      RAGOps PRODUCTION TRACE OBSERVABILITY METRICS (Part D3)")
    print("=" * 70)

    print(f"\n[WINDOW] {summary['window']['first']}  ->  {summary['window']['last']}")
    print(f"\n[VOLUME & RATES]")
    print(f"  • Total Trace Queries Logged   : {summary['total_queries']}")
    print(f"  • Cache Hit Rate (Redis/Local) : {summary['cache_hit_rate']:.1%} ({summary['cache_hits']} hits)")
    print(f"  • Honest Abstention Rate       : {summary['abstention_rate']:.1%} ({summary['abstentions']} declined)")
    print(f"  • Prompt Injections Flagged    : {summary['injections_flagged']} attempts (Part D4)")

    top1 = summary["top1_similarity"]
    print(f"\n[TOP-1 RETRIEVAL SIMILARITY DISTRIBUTION]")
    print(f"  • Mean Top-1 Similarity        : {top1['mean']:.4f}")
    print(f"  • Min Top-1 Similarity         : {top1['min']:.4f}")
    print(f"  • Max Top-1 Similarity         : {top1['max']:.4f}")
    print(f"  • Median (p50) Similarity      : {top1['p50']:.4f}")
    print(f"  • 95th Percentile (p95)        : {top1['p95']:.4f}")
    print(f"  • Histogram                    : {top1['histogram']}")

    lats = summary["latencies_p95_ms"]
    print(f"\n[STAGE-BY-STAGE LATENCIES (p95)]")
    print(f"  • Cache Lookup p95 Latency     : {lats['cache_lookup']:.2f} ms")
    print(f"  • Vector Retrieval p95 Latency : {lats['retrieval']:.2f} ms")
    print(f"  • LLM Generation p95 Latency   : {lats['generation']:.2f} ms")
    print(f"  • End-to-End Total p95 Latency : {lats['total']:.2f} ms")
    print("=" * 70 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Part D3: Aggregate metrics from trace logs.")
    parser.add_argument("--file", default="traces/query_traces.jsonl",
                        help="Trace JSONL file, a folder of them (one per API pod), or - to read `kubectl logs` from stdin")
    parser.add_argument("--since", help="ISO start time, e.g. 2026-11-26T10:00:00")
    parser.add_argument("--until", help="ISO end time")
    parser.add_argument("--last-minutes", type=int, help="Only the last N minutes (relative to now, UTC)")
    parser.add_argument("--out", help="Also write the summary as JSON to this path (e.g. eval/results/)")
    args = parser.parse_args()

    def _parse(v: str | None) -> datetime.datetime | None:
        return datetime.datetime.fromisoformat(v).replace(tzinfo=datetime.timezone.utc) if v else None

    since, until = _parse(args.since), _parse(args.until)
    if args.last_minutes:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=args.last_minutes)
    summary = summarize_traces(args.file, since, until)
    if not summary:
        print("No traces in that window.", file=sys.stderr)
        return 1

    print_summary(summary)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[+] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
