"""Prometheus metrics at /metrics (section 4.8).

Request count and latency are wired up along with:
retrieval latency, generation latency, cache hits/misses, abstentions, fallbacks, tokens.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

router = APIRouter()

REQUESTS = Counter("rahbar_requests_total", "HTTP requests", ["path", "status"])
LATENCY = Histogram(
    "rahbar_request_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

RETRIEVAL_LATENCY = Histogram(
    "rahbar_retrieval_seconds",
    "Vector retrieval latency in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

GENERATION_LATENCY = Histogram(
    "rahbar_generation_seconds",
    "LLM provider generation latency in seconds",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30),
)

CACHE_HITS = Counter("rahbar_cache_hits_total", "Semantic cache hits", ["type"])
CACHE_MISSES = Counter("rahbar_cache_misses_total", "Semantic cache misses")
ABSTENTIONS = Counter(
    "rahbar_abstentions_total",
    "Queries where the system abstained from answering",
    ["reason"],
)
FALLBACKS = Counter("rahbar_fallbacks_total", "Provider fallback generation events")
TOKENS = Counter("rahbar_tokens_total", "Estimated tokens processed", ["kind"])


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
