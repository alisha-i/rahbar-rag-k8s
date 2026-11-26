"""Stage 4: Embed. One vector per chunk, from the pinned model only."""

from __future__ import annotations

import logging
import os

import httpx

from .config import IndexerSettings
from .dummy import fake_vector

log = logging.getLogger("rahbar.embed")

_OLLAMA_ONLINE: bool | None = None


class EmbeddingUnavailable(RuntimeError):
    """The pinned model could not be reached and fake vectors were not explicitly allowed."""


def fake_embeddings_allowed() -> bool:
    """Hash vectors are for offline unit tests only. In the cluster the Job must fail loudly
    rather than write vectors from a different "model" into Chroma (A2 3.2 / 4.4)."""
    return os.environ.get("ALLOW_FAKE_EMBEDDINGS", "").lower() in {"1", "true", "yes"}


def embedder_in_use() -> str:
    return "ollama" if _OLLAMA_ONLINE else ("hash-fallback" if _OLLAMA_ONLINE is False else "not-yet-called")


def _fallback(texts: list[str], reason: str) -> list[list[float]]:
    if not fake_embeddings_allowed():
        raise EmbeddingUnavailable(
            f"cannot embed with pinned model: {reason}. Is Ollama running with "
            "OLLAMA_HOST=0.0.0.0 and the model pulled? (set ALLOW_FAKE_EMBEDDINGS=1 only for offline tests)"
        )
    log.warning("using deterministic hash vectors (ALLOW_FAKE_EMBEDDINGS set): %s", reason)
    return [fake_vector(t) for t in texts]


def embed_batch(texts: list[str], settings: IndexerSettings) -> list[list[float]]:
    """Embed a single batch of texts using the pinned EMBED_MODEL via Ollama.
    If Ollama is unreachable this raises, unless ALLOW_FAKE_EMBEDDINGS is set (offline tests).
    """
    global _OLLAMA_ONLINE

    if settings.embed_model.startswith("hash-"):
        # CI-only "model" (kind cluster without Ollama); the API uses the identical function.
        return [fake_vector(t) for t in texts]

    if _OLLAMA_ONLINE is False:
        return _fallback(texts, "ollama marked offline earlier in this run")

    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    payload = {
        "model": settings.embed_model,
        "input": texts,
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=0.5)) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings and len(embeddings) == len(texts):
                    _OLLAMA_ONLINE = True
                    return embeddings

            legacy_url = f"{settings.ollama_url.rstrip('/')}/api/embeddings"
            results: list[list[float]] = []
            for t in texts:
                leg_resp = client.post(legacy_url, json={"model": settings.embed_model, "prompt": t})
                if leg_resp.status_code == 200:
                    results.append(leg_resp.json().get("embedding", []))
            if len(results) == len(texts) and all(results):
                _OLLAMA_ONLINE = True
                return results

    except Exception as exc:
        _OLLAMA_ONLINE = False
        return _fallback(texts, str(exc))

    _OLLAMA_ONLINE = False
    return _fallback(texts, "embedding endpoint returned no vectors")


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
