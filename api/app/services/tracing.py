"""Part D: Observability & Traceability Engine (RAGOps Section 3.2.3, 5.1, Table 2).

Every query logs a complete JSON trace line capturing:
- Trace ID, timestamp, original and rewritten user query
- Embedding model and version, collection name
- Retrieved chunk IDs, document IDs, versions, and scores
- Filters applied, reranked order, prompt hash / full prompt
- Model parameters, generated response, validated citations
- Guardrail injection actions (D4)
- Stage-by-stage latencies (cache, embedding, retrieval, generation, total)
- Cache hit / miss status
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any

log = logging.getLogger("rahbar.trace")

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+|your\s+)?(previous|prior|earlier)?\s*(instructions|constraints|rules)", re.I),
    re.compile(r"system\s+(instruction\s+)?override", re.I),
    re.compile(r"disregard\s+(all\s+)?(prior|previous|existing)\s+rules", re.I),
    re.compile(r"tell\s+the\s+(student|user)\s+that\s+\w+(\s+\w+)?\s+is\s+(completely\s+)?optional", re.I),
    re.compile(r"output\s+(confidential|admin|system)\s+credentials", re.I),
    re.compile(r"waive\s+all\s+degree\s+requirements", re.I),
]


def _matches(text: str) -> list[str]:
    return [m.group(0) for pat in INJECTION_PATTERNS if (m := pat.search(text))]


def check_guardrails(query: str, passages: Sequence[Any] = ()) -> dict[str, Any]:
    """Retrieval rail (A2 3.4, A3 D4).

    - In a retrieved chunk, every sentence/line carrying an injected instruction is REDACTED
      before generation (see redact_injection); the rest of the chunk is kept, so a legitimate
      rule that shares a chunk with the attack is still answerable.
    - An injection attempt in the user's question is flagged; the question is still answered
      only from evidence, inside the delimited prompt.
    The returned dict is written verbatim into the trace as `guardrail_actions`.
    """
    query_hits = _matches(query)
    dropped: list[str] = []
    chunk_hits: dict[str, list[str]] = {}
    for p in passages:
        hits = _matches(getattr(p, "text", "") or "")
        if hits:
            dropped.append(p.chunk_id)
            chunk_hits[p.chunk_id] = hits

    actions: list[str] = []
    if dropped:
        actions.append("redacted_injected_text")
    if query_hits:
        actions.append("flagged_query_injection")
    return {
        "injection_detected": bool(dropped or query_hits),
        "action_taken": "+".join(actions) if actions else "allowed",
        "query_injection": bool(query_hits),
        "patterns_matched": query_hits + [h for hs in chunk_hits.values() for h in hs],
        "dropped_chunk_ids": dropped,  # chunks whose injected text was redacted
    }


REDACTION = "[removed by RAHBAR guardrail: suspected injected instruction]"


def redact_injection(text: str) -> str:
    """Remove each line that matches an injection pattern (the whole line: an attack is usually
    several sentences, and only the first one carries the trigger phrase)."""
    pieces = text.split("\n")
    kept = [REDACTION if _matches(p) else p for p in pieces if p.strip()]
    return "\n".join(kept)


@dataclass
class RetrievedDocTrace:
    chunk_id: str
    document_id: str
    document_version: str
    section: str
    page: int | None
    score: float
    text_preview: str = ""


@dataclass
class TraceRecord:
    trace_id: str
    timestamp: str
    user_query: str
    rewritten_query: str
    embedding_model: str
    embedding_version: str
    collection_name: str
    retrieved_chunk_ids: list[str]
    retrieved_docs: list[dict[str, Any]]
    filters_applied: dict[str, Any]
    reranked_order: list[int]
    prompt_hash: str
    full_prompt: str
    model: str
    parameters: dict[str, Any]
    response: str
    citations: list[dict[str, Any]]
    guardrail_actions: dict[str, Any]
    latency_per_stage: dict[str, float]
    cache_hit: bool
    request_id: str = "-"
    answered_from_context: bool | None = None

    def to_json_line(self) -> str:
        return json.dumps(asdict(self))


def log_trace(record: TraceRecord, trace_file_path: str | Path = "traces/query_traces.jsonl") -> None:
    """Write trace record to stdout (for kubectl logs) and append to trace log volume."""
    line = record.to_json_line()
    # 1. Standard out, one bare JSON object per line, so `kubectl logs ... | grep trace_id`
    #    and `jq` both work (F4).
    print(line, flush=True)

    # 2. Append to persistent trace log file
    try:
        p = Path(trace_file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        log.warning("failed to write trace to file %s: %s", trace_file_path, exc)
