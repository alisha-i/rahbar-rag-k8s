"""Semantic cache layer backed by Redis (section 3.3).

Matches incoming questions against cached queries using cosine similarity of embeddings.
A cache hit occurs if cosine_similarity(query_emb, cached_emb) >= settings.cache_threshold.
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


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
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


class SemanticCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
        """Search cached questions in Redis for cosine similarity >= cache_threshold."""
        if is_followup(question):
            log.info("never_cache rule applied: question is a follow-up query", extra={"question": question[:60]})
            return None

        try:
            r = self._get_redis()
            if r is None:
                return None
            cached_keys = r.keys("semcache:*")
            if not cached_keys:
                return None

            best_sim = -1.0
            best_entry: dict[str, Any] | None = None

            for key in cached_keys:
                raw = r.get(key)
                if not raw:
                    continue
                entry = json.loads(raw)
                cached_vec = entry.get("embedding", [])
                sim = cosine_similarity(query_embedding, cached_vec)
                if sim > best_sim:
                    best_sim = sim
                    best_entry = entry

            if best_sim >= self.settings.cache_threshold and best_entry is not None:
                log.info(
                    "semantic cache hit",
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
                )

        except Exception as exc:
            log.debug("semantic cache lookup failed or redis unreachable: %s", exc)

        return None

    def store(self, question: str, query_embedding: list[float], response: AskResponse) -> None:
        """Store query and embedding in Redis if it clears never_cache rules."""
        if never_cache(question, response):
            log.debug("never_cache rule applied; skipping cache store for: %s", question[:60])
            return

        try:
            r = self._get_redis()
            if r is None:
                return
            key_id = re.sub(r"[^a-zA-Z0-9]+", "_", question.lower().strip())[:40]
            cache_key = f"semcache:{key_id}"

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
            # Cache TTL: expire after 24 hours (86400 seconds)
            r.set(cache_key, json.dumps(payload), ex=86400)
            log.info("stored semantic cache entry", extra={"cache_key": cache_key})
        except Exception as exc:
            log.debug("semantic cache store failed or redis unreachable: %s", exc)

    def invalidate_cache(self) -> int:
        """Flush/expire all semantic cache entries on re-index."""
        try:
            r = self._get_redis()
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
