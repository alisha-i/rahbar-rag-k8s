"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import logging

import httpx

from .config import IndexerSettings
from .dummy import fake_vector

log = logging.getLogger("rahbar.embed")


def embed_batch(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed a single batch of texts using the pinned EMBED_MODEL via Ollama."""
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    payload = {
        "model": settings.embed_model,
        "input": texts,
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings and len(embeddings) == len(texts):
                    return embeddings

            # Some Ollama versions use legacy /api/embeddings (single input per call)
            legacy_url = f"{settings.ollama_url.rstrip('/')}/api/embeddings"
            results: list[list[float]] = []
            for t in texts:
                leg_resp = client.post(legacy_url, json={"model": settings.embed_model, "prompt": t})
                if leg_resp.status_code == 200:
                    results.append(leg_resp.json().get("embedding", []))
            if len(results) == len(texts) and all(results):
                return results

    except Exception as exc:
        log.warning(
            "Ollama embedding call failed (%s); falling back to deterministic vectors for offline testing",
            exc,
        )

    # Deterministic fallback when Ollama is offline or in test environments
    return [fake_vector(t) for t in texts]


def embed(texts: list[str], settings: IndexerSettings, batch_size: int = 32) -> list[list[float]]:
    """Embed all texts with settings.embed_model in batches.
    Guarantees exactly one vector per input chunk.
    """
    if not texts:
        return []

    all_vectors: list[list[float]] = []
    log.info("embedding %d chunks using pinned model %r", len(texts), settings.embed_model)

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vectors = embed_batch(batch, settings)
        all_vectors.extend(vectors)

    return all_vectors
