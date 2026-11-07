"""Liveness and readiness (section 4.3). They answer different questions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

log = logging.getLogger("rahbar.health")

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Is the process alive? Must never touch Chroma, Redis or the LLM."""
    return {"status": "alive"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: checks Chroma reachable, collection non-empty, model pin matches, Redis reachable."""
    settings = request.app.state.settings
    checks: dict[str, Any] = {}

    # Check 1: Chroma reachable and collection non-empty
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        chroma_client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        chroma_client.heartbeat()
        collection = chroma_client.get_collection(settings.collection)
        chunk_count = collection.count()

        if chunk_count == 0:
            log.warning("readiness probe failed: collection is empty", extra={"collection": settings.collection})
            response.status_code = 503
            return {
                "status": "not_ready",
                "dependency": "chroma",
                "error": f"collection {settings.collection!r} is empty (chunk count is 0)",
            }
        checks["chroma"] = "ok"
        checks["chunk_count"] = chunk_count

        # Check 2: Embedding model pin check (section 4.4)
        col_metadata = collection.metadata or {}
        col_embed_model = col_metadata.get("embed_model")
        if col_embed_model and col_embed_model != settings.embed_model:
            log.error(
                "readiness probe failed: embedding model mismatch",
                extra={
                    "collection_model": col_embed_model,
                    "configured_embed_model": settings.embed_model,
                },
            )
            response.status_code = 503
            return {
                "status": "not_ready",
                "dependency": "chroma_model_pin",
                "error": (
                    f"embedding model mismatch: collection was indexed with {col_embed_model!r} "
                    f"does not match configured EMBED_MODEL {settings.embed_model!r}"
                ),
            }
        checks["embed_model"] = settings.embed_model

    except Exception as exc:
        log.warning("readiness probe failed: Chroma unreachable", extra={"error": str(exc)})
        response.status_code = 503
        return {
            "status": "not_ready",
            "dependency": "chroma",
            "error": f"Chroma unreachable at {settings.chroma_host}:{settings.chroma_port}: {exc}",
        }

    # Check 3: Redis reachable
    try:
        import redis

        redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            socket_timeout=2.0,
            decode_responses=True,
        )
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        log.warning("readiness probe failed: Redis unreachable", extra={"error": str(exc)})
        response.status_code = 503
        return {
            "status": "not_ready",
            "dependency": "redis",
            "error": f"Redis unreachable at {settings.redis_host}:{settings.redis_port}: {exc}",
        }

    return {"status": "ready", "checks": checks}
