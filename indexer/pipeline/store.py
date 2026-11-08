"""Stage 5: Store. The only code in the whole system that writes to Chroma."""

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


def _invalidate_semantic_cache(redis_host: str = "redis", redis_port: int = 6379) -> None:
    """Invalidate semantic cache entries on re-index so old answers are never served."""
    try:
        import redis

        r = redis.Redis(host=redis_host, port=redis_port, socket_timeout=2.0)
        keys = r.keys("cache:*")
        if keys:
            r.delete(*keys)
            log.info("invalidated %d cached answers from Redis", len(keys))
    except Exception as exc:
        log.info("cache invalidation skipped or redis unreachable: %s", exc)


def store(chunks: list[Chunk], vectors: list[list[float]], settings: IndexerSettings) -> int:
    """Write every chunk with its vector and metadata, and record the embedding model
    and vector dimension in the collection metadata (section 4.4). Return the count.
    """
    if not chunks:
        log.warning("store called with 0 chunks; nothing to index")
        return 0

    client = get_client(settings)
    collection_name = settings.collection
    dimension = len(vectors[0]) if vectors else 768

    # Recreate the collection to replace old chunks cleanly
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass  # Collection did not exist yet

    collection_metadata = {
        "hnsw:space": "cosine",
        "embed_model": settings.embed_model,
        "dimension": dimension,
    }

    collection = client.create_collection(
        name=collection_name,
        metadata=collection_metadata,
    )

    # Batch insertions to stay within ChromaDB payload limits
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        chunk_batch = chunks[i : i + batch_size]
        vector_batch = vectors[i : i + batch_size]

        collection.add(
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
            "chunks_stored": total_stored,
            "embed_model": settings.embed_model,
            "dimension": dimension,
        },
    )

    # Invalidate cache when re-indexing completes (section 3.3)
    _invalidate_semantic_cache()

    return total_stored
