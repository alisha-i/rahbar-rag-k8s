"""Semantic cache layer backed by Redis and memory fallback (section 3.3).

Matches incoming questions against cached queries using cosine similarity of embeddings
and normalized token overlap similarity.
A hit is cosine similarity of the question embeddings >= CACHE_THRESHOLD (A2 section 3.3).
An identical normalised question also counts as a hit. Token-overlap "containment" is NOT used
for hits: it matched version-sensitive questions ("... in 2023?") to their 2026 answers.

Keys are namespaced by collection, so a candidate index and the live index never share answers,
and the indexer Job flushes `semcache:*` on every re-index. The in-process dictionary is only used
when no Redis is configured (local dev); on the cluster every pod reads the same Redis, so an
invalidation reaches all replicas.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from ..config import Settings
from ..schemas import AskResponse, Citation, RetrievedRef

log = logging.getLogger("rahbar.cache")

FOLLOWUP_PATTERNS = re.compile(
    r"\b(what about|and then|what else|also|how about that|tell me more|and that|does it also|why is that|can you explain further)\b",
    re.I,
)


def _normalize_text(text: str) -> str:
    """Normalize text by lowercasing and stripping punctuation and excess whitespace."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _text_similarity(q1: str, q2: str) -> float:
    """Compute normalized token overlap similarity between two questions.
    Returns 1.0 on exact match, or high score on containment/synonym phrasing.
    """
    n1 = _normalize_text(q1)
    n2 = _normalize_text(q2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    w1 = set(n1.split())
    w2 = set(n2.split())
    if not w1 or not w2:
        return 0.0
    intersection = len(w1 & w2)
    union = len(w1 | w2)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(w1), len(w2))
    # If the smaller query is essentially fully contained in the larger query, score 0.90
    return max(jaccard, containment * 0.90)


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors.

    Returns a normalized value in [0.0, 1.0]. A score >= settings.cache_threshold (0.85)
    triggers a semantic cache hit.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    norm_b = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def is_followup(question: str) -> bool:
    """Detect conversational follow-up questions that depend on previous context (never cache)."""
    return bool(FOLLOWUP_PATTERNS.search(question.strip()))


def never_cache(question: str, response: AskResponse) -> bool:
    """Never-cache rules:
    - Follow-up questions (depend on chat history)
    - Abstentions (answered_from_context: false)
    - Fallback or validation failures
    - Low-confidence error responses
    """
    if is_followup(question):
        return True
    if not response.answered_from_context:
        return True
    if response.confidence == "low" and "fallback" in response.answer.lower():
        return True
    return False


def _hit_similarity(question: str, query_embedding: list[float], entry: dict[str, Any]) -> float:
    if _normalize_text(question) == _normalize_text(entry.get("question", "")):
        return 1.0
    cached_vec = entry.get("embedding", [])
    return cosine_similarity(query_embedding, cached_vec) if query_embedding and cached_vec else 0.0


# In-memory cache, used only when Redis is not configured (local dev)
_LOCAL_MEM_CACHE: dict[str, dict[str, Any]] = {}


class SemanticCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _prefix(self) -> str:
        return f"semcache:{self.settings.collection}:"

    def _get_redis(self) -> Any:
        import os

        if "KUBERNETES_SERVICE_HOST" not in os.environ and self.settings.redis_host == "redis":
            return None
        import redis

        return redis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            socket_timeout=1.0,
            decode_responses=True,
        )

    def lookup(self, question: str, query_embedding: list[float]) -> AskResponse | None:
        """Search cached questions in Redis or local memory for similarity >= cache_threshold."""
        if is_followup(question):
            log.info("never_cache rule applied: question is a follow-up query", extra={"question": question[:60]})
            return None

        # 1. Try Redis cache first if configured/reachable
        try:
            r = self._get_redis()
            if r is not None:
                cached_keys = r.keys(f"{self._prefix()}*")
                if cached_keys:
                    best_sim = -1.0
                    best_entry: dict[str, Any] | None = None

                    for key in cached_keys:
                        raw = r.get(key)
                        if not raw:
                            continue
                        entry = json.loads(raw)
                        sim = _hit_similarity(question, query_embedding, entry)
                        if sim > best_sim:
                            best_sim = sim
                            best_entry = entry

                    if best_sim >= self.settings.cache_threshold and best_entry is not None:
                        log.info(
                            "semantic cache hit (redis)",
                            extra={
                                "question": question[:60],
                                "matched_query": best_entry.get("question", "")[:60],
                                "similarity": round(best_sim, 4),
                                "threshold": self.settings.cache_threshold,
                            },
                        )
                        return AskResponse(
                            request_id=best_entry.get("request_id", "cached"),
                            answer=best_entry["answer"],
                            citations=[Citation(**c) for c in best_entry.get("citations", [])],
                            answered_from_context=best_entry["answered_from_context"],
                            confidence=best_entry["confidence"],
                            retrieved=[RetrievedRef(**ref) for ref in best_entry.get("retrieved", [])],
                            provider=f"cache (similarity {best_sim:.2f})",
                            cached=True,
                        )
        except Exception as exc:
            log.debug("redis cache lookup failed: %s", exc)

        # 2. In-memory cache: local development only (no Redis configured)
        try:
            if self._get_redis() is None and _LOCAL_MEM_CACHE:
                best_sim = -1.0
                best_entry = None
                for key, entry in _LOCAL_MEM_CACHE.items():
                    if not key.startswith(self._prefix()):
                        continue
                    sim = _hit_similarity(question, query_embedding, entry)
                    if sim > best_sim:
                        best_sim = sim
                        best_entry = entry

                if best_sim >= self.settings.cache_threshold and best_entry is not None:
                    log.info(
                        "semantic cache hit (memory fallback)",
                        extra={
                            "question": question[:60],
                            "matched_query": best_entry.get("question", "")[:60],
                            "similarity": round(best_sim, 4),
                            "threshold": self.settings.cache_threshold,
                        },
                    )
                    return AskResponse(
                        request_id=best_entry.get("request_id", "cached"),
                        answer=best_entry["answer"],
                        citations=[Citation(**c) for c in best_entry.get("citations", [])],
                        answered_from_context=best_entry["answered_from_context"],
                        confidence=best_entry["confidence"],
                        retrieved=[RetrievedRef(**ref) for ref in best_entry.get("retrieved", [])],
                        provider=f"cache (similarity {best_sim:.2f})",
                        cached=True,
                    )
        except Exception as exc:
            log.debug("in-memory cache lookup error: %s", exc)

        return None

    def store(self, question: str, query_embedding: list[float], response: AskResponse) -> None:
        """Store query and embedding in Redis and local memory if it clears never_cache rules."""
        if never_cache(question, response):
            log.debug("never_cache rule applied; skipping cache store for: %s", question[:60])
            return

        key_id = re.sub(r"[^a-zA-Z0-9]+", "_", question.lower().strip())[:40]
        cache_key = f"{self._prefix()}{key_id}"

        payload = {
            "question": question,
            "embedding": query_embedding,
            "request_id": response.request_id,
            "answer": response.answer,
            "citations": [c.model_dump() for c in response.citations],
            "answered_from_context": response.answered_from_context,
            "confidence": response.confidence,
            "retrieved": [ref.model_dump() for ref in response.retrieved],
            "provider": response.provider,
        }

        try:
            r = self._get_redis()
            if r is None:
                _LOCAL_MEM_CACHE[cache_key] = payload
            else:
                r.set(cache_key, json.dumps(payload), ex=86400)
                log.info("stored semantic cache entry in redis", extra={"cache_key": cache_key})
        except Exception as exc:
            log.debug("redis store skipped or failed: %s", exc)

    def invalidate_cache(self) -> int:
        """Flush/expire all semantic cache entries on re-index."""
        _LOCAL_MEM_CACHE.clear()
        try:
            r = self._get_redis()
            if r is not None:
                keys = r.keys("semcache:*")
                if keys:
                    deleted = r.delete(*keys)
                    log.info("cache invalidation: flushed %d keys", deleted)
                    return deleted
        except Exception as exc:
            log.warning("cache invalidation failed: %s", exc)
        return 0

    def clear_cache(self) -> int:
        """Alias for invalidate_cache."""
        return self.invalidate_cache()
