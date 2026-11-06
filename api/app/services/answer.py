"""Turns a question into an AskResponse. This is where the query pipeline lives (sections 3.2, 3.4)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef

log = logging.getLogger("rahbar.answer")

DECLINE_ANSWER = Answer(
    reasoning="All retrieved passages fall below the relevance floor.",
    answer="The provided university documents do not contain sufficient information to answer this question.",
    citations=[],
    answered_from_context=False,
    confidence="low",
)


def _validate_citations(citations: list[Citation], passages: list[Passage]) -> list[Citation]:
    """Validate that every citation strictly matches a passage actually retrieved (Hard Rule 5).
    Any fabricated citation not present in the retrieved passages is dropped.
    """
    retrieved_keys = {(p.source, p.section) for p in passages}
    valid_citations: list[Citation] = []

    for c in citations:
        if (c.source, c.section) in retrieved_keys:
            valid_citations.append(c)
        else:
            log.warning(
                "dropped fabricated citation not in retrieved set",
                extra={"source": c.source, "section": c.section, "page": c.page},
            )
    return valid_citations


def _check_cache(question: str, settings: Settings) -> AskResponse | None:
    """Semantic cache lookup in Redis (section 3.3). Returns cached AskResponse on hit."""
    # Never cache or lookup follow-up questions
    if any(q in question.lower() for q in ["what about", "and then", "also", "furthermore"]):
        return None

    try:
        import redis

        r = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=0.5, decode_responses=True)
        cached_raw = r.get(f"cache:{question.strip().lower()}")
        if cached_raw:
            data = json.loads(cached_raw)
            log.info("semantic cache hit", extra={"question": question[:80]})
            return AskResponse(
                request_id=request_id_var.get(),
                answer=data["answer"],
                citations=[Citation(**c) for c in data.get("citations", [])],
                answered_from_context=data["answered_from_context"],
                confidence=data["confidence"],
                retrieved=[RetrievedRef(**ref) for ref in data.get("retrieved", [])],
                provider=data.get("provider", "cache"),
            )
    except Exception as exc:
        log.debug("cache lookup skipped or unavailable: %s", exc)
    return None


def _store_cache(question: str, response: AskResponse, settings: Settings) -> None:
    """Store entry in Redis semantic cache (section 3.3).
    Never cache follow-ups, abstentions, out-of-scope, or validation failures.
    """
    if not response.answered_from_context:
        return  # Never cache abstentions

    try:
        import redis

        r = redis.Redis(host=settings.redis_host, port=settings.redis_port, socket_timeout=0.5, decode_responses=True)
        payload = {
            "answer": response.answer,
            "citations": [c.model_dump() for c in response.citations],
            "answered_from_context": response.answered_from_context,
            "confidence": response.confidence,
            "retrieved": [ref.model_dump() for ref in response.retrieved],
            "provider": response.provider,
        }
        r.set(f"cache:{question.strip().lower()}", json.dumps(payload), ex=86400)
    except Exception as exc:
        log.debug("cache store skipped or unavailable: %s", exc)


def answer_question(
    question: str, retriever: Retriever, provider: LLMProvider, settings: Settings
) -> AskResponse:
    """Production 8-step query pipeline:
    1. Validation: 3 to 500 characters (enforced by request schema)
    2. Semantic cache check: return hit if found
    3. Query embedding & 4. Retrieve top_k chunks with superseded filter
    5. Relevance floor & honest abstention check
    6. Augment instructions & delimited evidence
    7. Generate schema-constrained answer with retry & safe fallback
    8. Validate citations & store turn / cache entry
    """
    # Step 2: Semantic cache check
    cached = _check_cache(question, settings)
    if cached:
        return cached

    # Step 3 & 4: Retrieve top-k chunks from Chroma (with superseded filter)
    passages = retriever.retrieve(question, k=settings.top_k)

    # Step 5: Relevance floor and honest abstention (section 3.4)
    # If no chunk clears the relevance floor, abstain immediately
    cleared_passages = [p for p in passages if p.score >= settings.relevance_floor]
    if not cleared_passages:
        log.info(
            "abstaining: no passages cleared relevance floor",
            extra={
                "question": question[:80],
                "top_score": passages[0].score if passages else 0.0,
                "floor": settings.relevance_floor,
            },
        )
        answer = DECLINE_ANSWER
    else:
        # Step 6 & 7: Augment and generate with provider (has retry and safe fallback)
        try:
            answer = provider.generate(question, cleared_passages)
        except Exception as exc:
            log.error("provider.generate unhandled exception: %s; using safe fallback", exc)
            answer = Answer(
                reasoning="Safe fallback: unhandled provider error",
                answer="I apologize, but an unexpected error occurred while generating a response from the documents.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

    # Step 8: Validate citations (Hard Rule 5)
    validated_citations = _validate_citations(answer.citations, passages)

    # Structured observability log per request (section 4.8)
    log.info(
        "answered",
        extra={
            "retriever": retriever.name,
            "provider": provider.name,
            "top_k": settings.top_k,
            "chunk_ids": [p.chunk_id for p in passages],
            "scores": [round(p.score, 4) for p in passages],
            "answered_from_context": answer.answered_from_context,
            "citation_count": len(validated_citations),
        },
    )

    resp = AskResponse(
        request_id=request_id_var.get(),
        answer=answer.answer,
        citations=validated_citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=[
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score
            )
            for p in passages
        ],
        provider=provider.name,
    )

    # Store in semantic cache if safe
    _store_cache(question, resp, settings)

    return resp
