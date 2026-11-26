#!/usr/bin/env python3
"""Part D2: Trace Tool CLI (RAGOps Section 5.1, Table 2).

Takes a `trace_id` and prints the exact context, reconstructed prompt,
guardrail decisions, and output that produced that answer.

Usage:
    python scripts/trace_tool.py <trace_id>
    python scripts/trace_tool.py --latest
    python scripts/trace_tool.py <trace_id> --file traces/query_traces.jsonl
    kubectl logs -n rahbar -l app.kubernetes.io/name=api --tail=-1 | python scripts/trace_tool.py <trace_id> --file -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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


def find_trace(source: str, trace_id: str | None, get_latest: bool = False) -> dict | None:
    traces = load_traces(source)
    if not traces:
        return None
    if get_latest:
        return traces[-1]
    for rec in reversed(traces):
        if rec.get("trace_id") == trace_id:
            return rec
    return None


def print_trace(trace: dict) -> None:
    print("\n" + "=" * 78)
    print(f"   RAHBAR QUERY TRACE RECONSTRUCTION TOOL (Part D2)")
    print(f"   Trace ID: \033[96m{trace.get('trace_id')}\033[0m")
    print("=" * 78)

    print("\n[1. METADATA & INVOCATION]")
    print(f"  • Timestamp           : {trace.get('timestamp')}")
    print(f"  • User Query          : \"{trace.get('user_query')}\"")
    print(f"  • Normalized Query    : \"{trace.get('rewritten_query')}\"")
    print(f"  • Model & Parameters  : {trace.get('model')} (params: {trace.get('parameters')})")
    print(f"  • Embedding Model     : {trace.get('embedding_model')} ({trace.get('embedding_version')})")
    print(f"  • Collection          : {trace.get('collection_name')}")
    print(f"  • Filters Applied     : {trace.get('filters_applied')}")
    print(f"  • Reranked Order      : {trace.get('reranked_order')}")
    print(f"  • Cache Status        : {'HIT (Semantic Cache)' if trace.get('cache_hit') else 'MISS'}")

    lats = trace.get("latency_per_stage", {})
    print("\n[2. STAGE LATENCIES]")
    for stage, ms in lats.items():
        print(f"  • {stage:<20}: {ms:>8.2f} ms")

    print("\n[3. GUARDRAIL & DEFENSE (Part D4)]")
    guard = trace.get("guardrail_actions", {})
    print(f"  • Injection Detected  : {guard.get('injection_detected')}")
    print(f"  • Action Taken        : {guard.get('action_taken')}")
    if guard.get("dropped_chunk_ids"):
        print(f"  • Chunks Redacted     : {guard.get('dropped_chunk_ids')}")
    if guard.get("patterns_matched"):
        print(f"  • Patterns Flagged    : {guard.get('patterns_matched')}")

    print("\n[4. RETRIEVED CONTEXT PASSAGES]")
    docs = trace.get("retrieved_docs", [])
    if not docs:
        print("  (No passages retrieved or below relevance floor - honest abstention applied)")
    for i, doc in enumerate(docs, start=1):
        print(f"  Passage #{i}:")
        print(f"    - Chunk ID  : {doc.get('chunk_id')}")
        print(f"    - Document  : {doc.get('document_id')} (v: {doc.get('document_version')})")
        print(f"    - Section   : {doc.get('section')} (p. {doc.get('page')})")
        print(f"    - Sim Score : {doc.get('score')}")
        print(f"    - Superseded: {doc.get('superseded')}  | cleared floor: {doc.get('cleared_relevance_floor')}  | sent to LLM: {doc.get('sent_to_llm')}")
        print("    - Text      :")
        print("        " + "\n        ".join(str(doc.get('text', doc.get('text_preview', ''))).splitlines()))

    print("\n[5. EXACT RECONSTRUCTED PROMPT]")
    print(f"  • Prompt SHA-256 Hash : {trace.get('prompt_hash')}")
    print("  --- Prompt Content ---")
    print("  " + "\n  ".join(trace.get("full_prompt", "").splitlines()))
    print("  -----------------------")

    print("\n[6. GENERATED RESPONSE & CITATIONS]")
    print(f"  • Response Text : {trace.get('response')}")
    cites = trace.get("citations", [])
    if cites:
        print("  • Citations     :")
        for c in cites:
            print(f"      * {c.get('source')} / {c.get('section')} (page {c.get('page')})")
    else:
        print("  • Citations     : None (Honest abstention / ungrounded)")
    print("=" * 78 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Part D2: Inspect and reconstruct query traces.")
    parser.add_argument("trace_id", nargs="?", default=None, help="The trace_id to inspect")
    parser.add_argument("--latest", action="store_true", help="Inspect the most recent query trace")
    parser.add_argument("--file", default="traces/query_traces.jsonl",
                        help="Trace JSONL file, a folder of them (one per API pod), or - to read `kubectl logs` from stdin")
    parser.add_argument("--json", action="store_true", help="Print the raw trace record as JSON")
    args = parser.parse_args()

    if not args.trace_id and not args.latest:
        parser.print_help()
        return 1

    trace = find_trace(args.file, args.trace_id, get_latest=args.latest)
    if not trace:
        print(f"Trace not found: {args.trace_id}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(trace, indent=2))
    else:
        print_trace(trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
