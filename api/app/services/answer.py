"""Turns a question into an AskResponse. This is where the query pipeline lives (sections 3.2, 3.4)."""

from __future__ import annotations

import datetime
import hashlib
import logging
import time
import uuid

from ..config import Settings
from ..logjson import request_id_var
from ..providers.llm.base import LLMProvider
from ..retrieval.base import Retriever
from ..providers.llm.ollama import _build_augmented_prompt
from ..retrieval.chroma import _embed_query
from ..routes.metrics import (
    ABSTENTIONS,
    CACHE_HITS,
    CACHE_MISSES,
    FALLBACKS,
    GENERATION_LATENCY,
    INJECTIONS,
    RETRIEVAL_LATENCY,
    TOKENS,
)
from ..schemas import Answer, AskResponse, Citation, Passage, RetrievedRef
from .cache import SemanticCache
from .tracing import TraceRecord, check_guardrails, log_trace, redact_injection

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
    """Store entry in the semantic cache (section 3.3).
    Never cache follow-ups, abstentions, out-of-scope, or validation failures.
    """
    try:
        cache = SemanticCache(settings)
        query_vec = _embed_query(question, settings)
        cache.store(question, query_vec, response)
    except Exception as exc:
        log.debug("cache store skipped or error: %s", exc)


def _index_info(retriever: Retriever, settings: Settings) -> dict[str, str]:
    info = getattr(retriever, "index_info", None)
    if callable(info):
        try:
            return dict(info())
        except Exception:  # pragma: no cover - defensive
            pass
    return {"collection": settings.collection, "embed_model": settings.embed_model,
            "embedding_version": settings.embed_model}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_prompt(question: str, passages: list[Passage]) -> str:
    """The exact augmented prompt the Ollama/hosted providers send (instructions, numbered,
    delimited evidence, question). Recorded in full in the trace so D2 can rebuild it."""
    return _build_augmented_prompt(question, passages)


def retrieve_only(question: str, retriever: Retriever, settings: Settings) -> tuple[list[Passage], dict[str, object]]:
    """Retrieval without generation, for the component-level evaluation (A3 Part B)."""
    passages = retriever.retrieve(question, k=settings.top_k)
    filters = dict(getattr(retriever, "last_filter", {}) or {})
    return passages, filters


def answer_question(
    question: str,
    retriever: Retriever,
    provider: LLMProvider,
    settings: Settings,
    use_cache: bool = True,
) -> AskResponse:
    """Production 8-step query pipeline:
    1. Validation: 3 to 500 characters (enforced by request schema)
    2. Semantic cache check: return hit if found (skipped when use_cache is False)
    3. Query embedding & 4. Retrieve top_k chunks with superseded filter
    5. Relevance floor & honest abstention check; guardrail drops injected chunks
    6. Augment instructions & delimited evidence
    7. Generate schema-constrained answer with retry & safe fallback
    8. Validate citations, write one trace line, store cache entry if safe
    """
    t_start = time.perf_counter()
    trace_id = f"tr-{uuid.uuid4().hex[:12]}"
    info = _index_info(retriever, settings)

    # Step 2: Semantic cache check
    t_cache_start = time.perf_counter()
    cached = _check_cache(question, settings) if use_cache else None
    cache_latency_ms = (time.perf_counter() - t_cache_start) * 1000.0

    if cached:
        CACHE_HITS.labels(type="semantic").inc()
        resp = cached.model_copy(update={"cached": True, "trace_id": trace_id,
                                         "request_id": request_id_var.get()})
        total_lat_ms = (time.perf_counter() - t_start) * 1000.0
        log_trace(
            TraceRecord(
                trace_id=trace_id,
                timestamp=_now(),
                user_query=question,
                rewritten_query=question.strip(),
                embedding_model=info["embed_model"],
                embedding_version=info["embedding_version"],
                collection_name=info["collection"],
                retrieved_chunk_ids=[r.chunk_id for r in resp.retrieved],
                retrieved_docs=[r.model_dump(exclude={"text"}) for r in resp.retrieved],
                filters_applied={},
                reranked_order=list(range(len(resp.retrieved))),
                prompt_hash="cached",
                full_prompt="[CACHE_HIT] answer served from the semantic cache; no prompt was built",
                model="semantic_cache",
                parameters={"cache_threshold": settings.cache_threshold},
                response=resp.answer,
                citations=[c.model_dump() for c in resp.citations],
                guardrail_actions={"injection_detected": False, "action_taken": "not_run_cache_hit",
                                   "patterns_matched": [], "dropped_chunk_ids": []},
                latency_per_stage={"cache_lookup_ms": round(cache_latency_ms, 2),
                                   "total_ms": round(total_lat_ms, 2)},
                cache_hit=True,
                request_id=request_id_var.get(),
                answered_from_context=resp.answered_from_context,
            ),
            settings.trace_file,
        )
        return resp
    CACHE_MISSES.inc()

    # Step 3 & 4: Retrieve top-k chunks from retriever (with superseded filter)
    t_ret = time.perf_counter()
    passages = retriever.retrieve(question, k=settings.top_k)
    retrieval_latency_ms = (time.perf_counter() - t_ret) * 1000.0
    RETRIEVAL_LATENCY.observe(retrieval_latency_ms / 1000.0)
    filters_applied = dict(getattr(retriever, "last_filter", {}) or {})

    # Step 5: Relevance floor and honest abstention (section 3.4)
    cleared_passages = [p for p in passages if p.score >= settings.relevance_floor]

    # Step 5.1: Guardrail (A2 3.4 "treat retrieved text as data", A3 D4).
    # Chunks carrying an injected instruction are removed from the context, and the
    # action is written into the trace.
    guardrail = check_guardrails(question, cleared_passages)
    if guardrail["dropped_chunk_ids"]:
        INJECTIONS.inc(len(guardrail["dropped_chunk_ids"]))
        cleared_passages = [
            p.model_copy(update={"text": redact_injection(p.text)}) if p.chunk_id in guardrail["dropped_chunk_ids"] else p
            for p in cleared_passages
        ]
    if guardrail["query_injection"]:
        INJECTIONS.inc()

    generation_latency_ms = 0.0
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
            generation_latency_ms = (time.perf_counter() - t_gen) * 1000.0
            GENERATION_LATENCY.observe(generation_latency_ms / 1000.0)
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

    # Step 8: Validate citations against what was actually retrieved (and sent) for this request.
    if not answer.answered_from_context:
        validated_citations: list[Citation] = []
        retrieved_refs: list[RetrievedRef] = []
    else:
        validated_citations = _validate_citations(answer.citations, cleared_passages)
        retrieved_refs = [
            RetrievedRef(
                chunk_id=p.chunk_id, source=p.source, section=p.section, page=p.page, score=p.score, text=p.text
            )
            for p in passages
        ]

    total_latency_ms = (time.perf_counter() - t_start) * 1000.0
    req_id = request_id_var.get()

    constructed_prompt = build_prompt(question, cleared_passages) if cleared_passages else "[NO_PROMPT] abstained before generation"
    prompt_hash = hashlib.sha256(constructed_prompt.encode("utf-8")).hexdigest()
    ranked = sorted(range(len(passages)), key=lambda i: passages[i].score, reverse=True)

    log_trace(
        TraceRecord(
            trace_id=trace_id,
            timestamp=_now(),
            user_query=question,
            rewritten_query=question.strip(),  # no query rewriting in this pipeline
            embedding_model=info["embed_model"],
            embedding_version=info["embedding_version"],
            collection_name=info["collection"],
            retrieved_chunk_ids=[p.chunk_id for p in passages],
            retrieved_docs=[
                {
                    "chunk_id": p.chunk_id,
                    "document_id": p.source,
                    "document_version": p.doc_version or "unknown",
                    "superseded": p.superseded,
                    "section": p.section,
                    "page": p.page,
                    "score": round(p.score, 4),
                    "cleared_relevance_floor": p.score >= settings.relevance_floor,
                    "sent_to_llm": any(c.chunk_id == p.chunk_id for c in cleared_passages),
                    "redacted": p.chunk_id in guardrail["dropped_chunk_ids"],
                    "text": p.text,
                }
                for p in passages
            ],
            filters_applied=filters_applied,
            reranked_order=ranked,  # no reranker: order is by similarity score
            prompt_hash=prompt_hash,
            full_prompt=constructed_prompt,
            model=f"{provider.name}:{settings.llm_model}" if provider.name != "stub" else "stub",
            parameters={"top_k": settings.top_k, "relevance_floor": settings.relevance_floor,
                        "temperature": settings.temperature},
            response=answer.answer,
            citations=[c.model_dump() for c in validated_citations],
            guardrail_actions=guardrail,
            latency_per_stage={
                "cache_lookup_ms": round(cache_latency_ms, 2),
                "retrieval_ms": round(retrieval_latency_ms, 2),
                "generation_ms": round(generation_latency_ms, 2),
                "total_ms": round(total_latency_ms, 2),
            },
            cache_hit=False,
            request_id=req_id,
            answered_from_context=answer.answered_from_context,
        ),
        settings.trace_file,
    )

    resp = AskResponse(
        request_id=req_id,
        answer=answer.answer,
        citations=validated_citations,
        answered_from_context=answer.answered_from_context,
        confidence=answer.confidence,
        retrieved=retrieved_refs,
        provider=provider.name,
        cached=False,
        trace_id=trace_id,
    )

    # Store in semantic cache if safe (never stores abstentions or errors)
    if use_cache:
        _store_cache(question, resp, settings)

    return resp
