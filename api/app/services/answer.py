"""Turns a question into an AskResponse. This is where the query pipeline lives (sections 3.2, 3.4)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..retrieval.chroma import _embed_query
from ..routes.metrics import (
    ABSTENTIONS,
    CACHE_HITS,
    CACHE_MISSES,
    FALLBACKS,
    GENERATION_LATENCY,
    RETRIEVAL_LATENCY,
    TOKENS,
)
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef
from .cache import SemanticCache

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
    try:
        cache = SemanticCache(settings)
        query_vec = _embed_query(question, settings)
        return cache.lookup(question, query_vec)
    except Exception as exc:
        log.debug("cache lookup skipped or error: %s", exc)
        return None


def _store_cache(question: str, response: AskResponse, settings: Settings) -> None:
    """Store entry in Redis semantic cache (section 3.3).
    Never cache follow-ups, abstentions, out-of-scope, or validation failures.
    """
    try:
        cache = SemanticCache(settings)
        query_vec = _embed_query(question, settings)
        cache.store(question, query_vec, response)
    except Exception as exc:
        log.debug("cache store skipped or error: %s", exc)


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
        CACHE_HITS.labels(type="semantic").inc()
        return cached
    CACHE_MISSES.inc()

    # Step 3 & 4: Retrieve top-k chunks from Chroma (with superseded filter)
    t_ret = time.perf_counter()
    passages = retriever.retrieve(question, k=settings.top_k)
    RETRIEVAL_LATENCY.observe(time.perf_counter() - t_ret)

    # Step 5: Relevance floor and honest abstention (section 3.4)
    # If no chunk clears the relevance floor, abstain immediately
    cleared_passages = [p for p in passages if p.score >= settings.relevance_floor]
    if not cleared_passages:
        ABSTENTIONS.labels(reason="below_relevance_floor").inc()
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
        t_gen = time.perf_counter()
        try:
            answer = provider.generate(question, cleared_passages)
            GENERATION_LATENCY.observe(time.perf_counter() - t_gen)
            # Record token estimates
            est_in = len(question.split()) + sum(len(p.text.split()) for p in cleared_passages)
            TOKENS.labels(kind="input").inc(est_in)
            TOKENS.labels(kind="output").inc(len(answer.answer.split()))
            if not answer.answered_from_context:
                ABSTENTIONS.labels(reason="model_unanswered").inc()
        except Exception as exc:
            FALLBACKS.inc()
            ABSTENTIONS.labels(reason="provider_fallback").inc()
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
