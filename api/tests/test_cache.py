"""Unit tests for the semantic cache layer (section 3.3, ADR 0003):
- Cosine similarity calculation
- Follow-up query detection
- Never-cache safety guardrails (abstentions, follow-ups, fallbacks)
- Threshold-based lookup hit vs miss
- Cache invalidation
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from app.config import Settings
from app.schemas import AskResponse, Citation, RetrievedRef
from app.services.cache import (
    SemanticCache,
    cosine_similarity,
    is_followup,
    never_cache,
)


def test_cosine_similarity() -> None:
    # Identical vectors
    assert pytest.approx(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 0.0001) == 1.0
    # Orthogonal vectors
    assert pytest.approx(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0001) == 0.0
    # Scaled vectors
    assert pytest.approx(cosine_similarity([0.5, 0.5], [2.0, 2.0]), 0.0001) == 1.0
    # Length mismatch
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.5]) == 0.0


def test_is_followup_detection() -> None:
    assert is_followup("What about that?") is True
    assert is_followup("And then what happens?") is True
    assert is_followup("Can you explain further?") is True
    assert is_followup("Tell me more about the degree requirements") is True

    # Standalone queries should not be detected as follow-up
    assert is_followup("What is the minimum CGPA required to graduate?") is False
    assert is_followup("State the attendance policy in section 3.1") is False


def test_never_cache_guardrails() -> None:
    valid_resp = AskResponse(
        request_id="req-1",
        answer="Valid grounded answer.",
        citations=[Citation(source="doc.pdf", section="1.0", page=1)],
        answered_from_context=True,
        confidence="high",
        retrieved=[],
        provider="ollama",
    )

    abstention_resp = AskResponse(
        request_id="req-2",
        answer="Documents do not contain sufficient information.",
        citations=[],
        answered_from_context=False,
        confidence="low",
        retrieved=[],
        provider="system",
    )

    fallback_resp = AskResponse(
        request_id="req-3",
        answer="Fallback error occurred.",
        citations=[],
        answered_from_context=False,
        confidence="low",
        retrieved=[],
        provider="fallback",
    )

    # 1. Standalone valid response -> OK to cache
    assert never_cache("What is the degree duration?", valid_resp) is False

    # 2. Follow-up query -> NEVER cache
    assert never_cache("What about that?", valid_resp) is True

    # 3. Honest abstention -> NEVER cache
    assert never_cache("What is the hostel mess fee?", abstention_resp) is True

    # 4. Fallback error -> NEVER cache
    assert never_cache("What is the fee structure?", fallback_resp) is True


def test_semantic_cache_lookup_hit_and_miss() -> None:
    settings = Settings(cache_threshold=0.85)
    cache = SemanticCache(settings)

    mock_redis = MagicMock()
    cache._get_redis = MagicMock(return_value=mock_redis)

    # Case 1: No cached keys
    mock_redis.keys.return_value = []
    hit = cache.lookup("What is the CGPA?", [1.0, 0.0])
    assert hit is None

    # Case 2: Cached key exists with high similarity (>= 0.85)
    cached_payload = {
        "question": "What CGPA is needed to graduate?",
        "embedding": [0.99, 0.01],  # Close to [1.0, 0.0]
        "request_id": "cached-req",
        "answer": "Minimum CGPA is 2.50.",
        "citations": [{"source": "rules.pdf", "section": "4.2", "page": 10}],
        "answered_from_context": True,
        "confidence": "high",
        "retrieved": [],
        "provider": "ollama",
    }
    mock_redis.keys.return_value = ["semcache:test_key"]
    mock_redis.get.return_value = json.dumps(cached_payload)

    hit = cache.lookup("What is the CGPA needed to graduate?", [1.0, 0.0])
    assert hit is not None
    assert hit.answer == "Minimum CGPA is 2.50."
    assert "cache" in hit.provider

    # Case 3: Cached key exists but low similarity (< 0.85)
    mock_redis.get.return_value = json.dumps({
        **cached_payload,
        "embedding": [0.1, 0.9],  # Orthogonal-ish, cosine ~ 0.11 < 0.85
    })
    miss = cache.lookup("What is the tuition fee refund?", [1.0, 0.0])
    assert miss is None


def test_semantic_cache_invalidation() -> None:
    settings = Settings()
    cache = SemanticCache(settings)
    mock_redis = MagicMock()
    cache._get_redis = MagicMock(return_value=mock_redis)

    mock_redis.keys.return_value = ["semcache:k1", "semcache:k2"]
    mock_redis.delete.return_value = 2

    deleted = cache.invalidate_cache()
    assert deleted == 2
    mock_redis.delete.assert_called_once_with("semcache:k1", "semcache:k2")
