"""POST /api/ask and POST /api/retrieve. The Ingress sends /api/* here unchanged."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..schemas import AskRequest, AskResponse, RetrievedRef, RetrieveResponse
from ..services.answer import _index_info, answer_question, retrieve_only

router = APIRouter(prefix="/api")


def _bypass_cache(request: Request) -> bool:
    """`Cache-Control: no-cache` skips the semantic cache: the eval suite uses it so latency
    and answers are measured on the real pipeline, not on cached copies."""
    return "no-cache" in request.headers.get("cache-control", "").lower()


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, response: Response) -> AskResponse:
    state = request.app.state
    resp = answer_question(
        body.question, state.retriever, state.provider, state.settings,
        use_cache=not _bypass_cache(request),
    )
    response.headers["X-Cache"] = "HIT" if resp.cached else "MISS"
    if resp.trace_id:
        response.headers["X-Trace-ID"] = resp.trace_id
    response.headers["Access-Control-Expose-Headers"] = "X-Cache, X-Trace-ID"
    return resp


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(body: AskRequest, request: Request) -> RetrieveResponse:
    """Retrieval only (no LLM call). Component-level metrics (recall@k, MRR, nDCG) use this,
    so they measure the retriever and not the generator's decision to abstain."""
    state = request.app.state
    passages, filters = retrieve_only(body.question, state.retriever, state.settings)
    info = _index_info(state.retriever, state.settings)
    return RetrieveResponse(
        retrieved=[
            RetrievedRef(chunk_id=p.chunk_id, source=p.source, section=p.section,
                         page=p.page, score=p.score, text=p.text)
            for p in passages
        ],
        filters_applied=filters,
        collection=info["collection"],
        embedding_version=info["embedding_version"],
    )
