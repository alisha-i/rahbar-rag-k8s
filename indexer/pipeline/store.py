"""Stage 5: Store. The only code in the whole system that writes to Chroma.

Supports both incremental batch updates and collection-level maintenance.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import IndexerSettings
from .models import Chunk

log = logging.getLogger("rahbar.store")


def get_client(settings: IndexerSettings) -> Any:
    """Initialize ChromaDB HTTP client using IndexerSettings configuration."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def get_or_create_collection(client: Any, settings: IndexerSettings) -> Any:
    """Get existing collection or create with proper cosine metric."""
    collection_name = settings.collection
    try:
        return client.get_collection(name=collection_name)
    except Exception:
        return client.create_collection(
            name=collection_name,
            metadata=_collection_metadata(settings),
        )


def _collection_metadata(settings: IndexerSettings, dimension: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "hnsw:space": "cosine",
        "embed_model": settings.embed_model,          # the pin the API checks (A2 4.4)
        "index_version": settings.index_version,      # echoed in every trace (A3 D1, F3)
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }
    if dimension:
        meta["dimension"] = dimension
    return meta


def count_by_document(client: Any, settings: IndexerSettings) -> dict[str, dict[str, int]]:
    """Chunk count per source document, split by superseded flag (A2 orphan proof)."""
    col = client.get_collection(settings.collection)
    got = col.get(include=["metadatas"])
    counts: dict[str, dict[str, int]] = {}
    for meta in got.get("metadatas") or []:
        src = str((meta or {}).get("source", "?"))
        flag = "superseded" if (meta or {}).get("superseded") else "active"
        counts.setdefault(src, {"active": 0, "superseded": 0})[flag] += 1
    return counts


def _invalidate_semantic_cache(redis_host: str | None = None, redis_port: int | None = None) -> None:
    """Invalidate semantic cache entries on re-index so old answers are never served."""
    try:
        import redis

        import os

        r = redis.Redis(
            host=redis_host or os.environ.get("REDIS_HOST", "redis"),
            port=int(redis_port or os.environ.get("REDIS_PORT") or 6379),
            socket_timeout=2.0,
        )
        keys: list[Any] = list(r.keys("semcache:*"))  # type: ignore[arg-type]
        if keys:
            r.delete(*keys)
            log.info("invalidated %d cached answers from Redis", len(keys))
    except Exception as exc:
        log.info("cache invalidation skipped or redis unreachable: %s", exc)


def delete_chunks(client: Any, settings: IndexerSettings, chunk_ids: list[str]) -> int:
    """Delete chunks by ID from the Chroma vector store (orphan cleanup)."""
    if not chunk_ids:
        return 0
    try:
        col = get_or_create_collection(client, settings)
        col.delete(ids=chunk_ids)
        log.info("deleted %d chunks from vector store", len(chunk_ids))
        return len(chunk_ids)
    except Exception as exc:
        log.warning("failed to delete chunks %s: %s", chunk_ids[:5], exc)
        return 0


def update_metadata(client: Any, settings: IndexerSettings, chunk_ids: list[str], metadata: dict[str, Any]) -> None:
    """Update metadata for existing chunks (e.g. marking superseded=True)."""
    if not chunk_ids:
        return
    try:
        col = get_or_create_collection(client, settings)
        existing = col.get(ids=chunk_ids, include=["metadatas"])
        ids = existing.get("ids") or []
        merged = [{**(m or {}), **metadata} for m in (existing.get("metadatas") or [])]
        if ids:
            col.update(ids=ids, metadatas=merged)
        log.info("updated metadata for %d chunks: %s", len(chunk_ids), metadata)
    except Exception as exc:
        log.warning("failed to update chunk metadata: %s", exc)


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings, wipe: bool = False) -> int:
    """Write chunks with their vectors and metadata into Chroma.
    If wipe=False, it adds or replaces only the provided chunks incrementally.
    """
    if not chunks:
        log.info("store called with 0 chunks; 0 chunks embedded")
        return 0

    client = get_client(settings)
    collection_name = settings.collection
    dimension = len(vectors[0]) if vectors else 768

    if wipe:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        collection = client.create_collection(
            name=collection_name,
            metadata=_collection_metadata(settings, dimension),
        )
    else:
        collection = get_or_create_collection(client, settings)

    # Batch insertions to stay within ChromaDB payload limits
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        chunk_batch = chunks[i : i + batch_size]
        vector_batch = vectors[i : i + batch_size]

        collection.upsert(
            ids=[c.id for c in chunk_batch],
            documents=[c.text for c in chunk_batch],
            embeddings=vector_batch,
            metadatas=[c.metadata() for c in chunk_batch],
        )

    total_stored = int(collection.count())
    log.info(
        "stored index successfully",
        extra={
            "collection": collection_name,
            "new_chunks_added": len(chunks),
            "total_collection_count": total_stored,
            "embed_model": settings.embed_model,
            "dimension": dimension,
        },
    )

    _invalidate_semantic_cache()
    return len(chunks)
