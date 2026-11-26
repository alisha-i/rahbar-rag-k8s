"""The real retriever. Queries ChromaDB with metadata filtering (sections 3.2, 4.4)."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import httpx

from ..config import Settings
from ..schemas import Passage

log = logging.getLogger("rahbar.retrieval.chroma")

OLDER_VERSION_KEYWORDS = re.compile(
    r"\b(2023|old|older|previous|past|superseded|repealed|former|prior)\b",
    re.I,
)


def _deterministic_vector(text: str, dim: int = 768) -> list[float]:
    """Generate a deterministic unit-length embedding vector for a given text.
    Uses word-level hashed projections so identical and semantically overlapping texts
    have stable, realistic cosine similarities, while orthogonal topics have near-zero similarity.
    """
    clean = re.sub(r"[^\w\s]", "", text.lower()).strip()
    words = clean.split()
    if not words:
        words = [text]

    accum = [0.0] * dim
    for w in words:
        seed = w.encode("utf-8")
        vals: list[float] = []
        while len(vals) < dim:
            seed = hashlib.sha256(seed).digest()
            vals.extend((b - 127.5) / 127.5 for b in seed)
        vals = vals[:dim]
        w_norm = math.sqrt(sum(x * x for x in vals)) or 1.0
        for i in range(dim):
            accum[i] += vals[i] / w_norm

    total_norm = math.sqrt(sum(v * v for v in accum)) or 1.0
    return [v / total_norm for v in accum]


_STOP = {"what", "is", "the", "a", "an", "of", "to", "for", "in", "and", "or", "how", "much",
         "when", "which", "are", "by", "on"}


def hash_vector(text: str, dim: int = 768) -> list[float]:
    """Byte-for-byte the same algorithm as indexer/pipeline/dummy.fake_vector. Used ONLY when
    EMBED_MODEL starts with "hash-" (the CI kind cluster, which has no Ollama), so the indexer
    and the API still agree on the "model" and the embedding pin still holds."""
    import hashlib as _h

    values = [0.0] * dim
    words = re.findall(r"\w+", text.lower())
    if not words:
        return [1.0 / math.sqrt(dim)] * dim
    for w in words:
        weight = 0.2 if w in _STOP else 3.0
        values[int(_h.md5(w.encode("utf-8")).hexdigest()[:8], 16) % dim] += weight
        if w not in _STOP:
            for i in range(len(w) - 2):
                values[int(_h.md5(w[i : i + 3].encode("utf-8")).hexdigest()[:8], 16) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class EmbeddingUnavailable(RuntimeError):
    """The pinned embedding model could not be reached. Never silently substitute vectors."""


def _embed_query(text: str, settings: Settings) -> list[float]:
    """Embed the user question using the pinned EMBED_MODEL via Ollama.

    With the stub retriever (local dev / CI) a deterministic hash vector is used.
    With the real Chroma retriever we never fall back to a different "embedding":
    vectors from two different models are not comparable and the failure would be
    silent (A2 section 3.2). We raise instead, and the caller abstains.
    """
    if getattr(settings, "retriever", "") == "stub":
        return _deterministic_vector(text, 768)
    if settings.embed_model.startswith("hash-"):
        return hash_vector(text, 768)

    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    payload = {"model": settings.embed_model, "input": [text]}

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                embeddings = resp.json().get("embeddings")
                if embeddings:
                    return list(embeddings[0])

            legacy_url = f"{settings.ollama_url.rstrip('/')}/api/embeddings"
            leg_resp = client.post(legacy_url, json={"model": settings.embed_model, "prompt": text})
            if leg_resp.status_code == 200:
                emb = leg_resp.json().get("embedding")
                if emb:
                    return list(emb)
            raise EmbeddingUnavailable(f"embedding endpoint returned HTTP {resp.status_code}")
    except EmbeddingUnavailable:
        raise
    except Exception as exc:
        raise EmbeddingUnavailable(f"cannot reach {url}: {exc}") from exc


class ChromaRetriever:
    name = "chroma"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self.last_filter: dict[str, Any] = {}

    @property
    def client(self) -> Any:
        """Created on first use, so the API process starts (and /healthz answers) even while
        Chroma is still coming up; /readyz keeps the pod out of the Service until it is."""
        if self._client is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.HttpClient(
                host=self.settings.chroma_host,
                port=self.settings.chroma_port,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _collection(self) -> Any:
        return self.client.get_collection(self.settings.collection)

    def index_info(self) -> dict[str, str]:
        """Collection name and the embedding/index version the indexer wrote into its metadata.
        Goes into every trace, so after `kubectl rollout undo` a trace shows which index answered."""
        try:
            meta = self._collection().metadata or {}
        except Exception:
            meta = {}
        return {
            "collection": self.settings.collection,
            "embed_model": str(meta.get("embed_model", self.settings.embed_model)),
            "embedding_version": str(meta.get("index_version", meta.get("embed_model", "unknown"))),
        }

    def retrieve(self, question: str, k: int) -> list[Passage]:
        """Embed the question with settings.embed_model, query Chroma for top k,
        filter out superseded documents unless the user explicitly asks about an older version,
        and map each hit to a Passage (converting cosine distance to similarity: 1 - distance).
        """
        try:
            collection = self._collection()
        except Exception as exc:  # collection missing / Chroma down: abstain, never a 500
            log.error("chroma collection %r unavailable: %s", self.settings.collection, exc)
            self.last_filter = {"error": "collection_unavailable"}
            return []

        # Decide whether to exclude superseded documents (section 3.2 step 4)
        wants_older = bool(OLDER_VERSION_KEYWORDS.search(question))
        where_filter: dict[str, Any] | None = None
        if not wants_older:
            where_filter = {"superseded": False}
        self.last_filter = where_filter or {}

        try:
            query_vec = _embed_query(question, self.settings)
        except EmbeddingUnavailable as exc:
            log.error("query embedding failed; returning no passages so the API abstains: %s", exc)
            self.last_filter = {**self.last_filter, "error": "embedding_unavailable"}
            return []

        try:
            results: Any = collection.query(
                query_embeddings=[query_vec],
                n_results=k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            log.error("chroma collection query failed: %s", exc)
            return []

        passages: list[Passage] = []
        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        for chunk_id, text, meta, dist in zip(ids, documents, metadatas, distances):
            meta_dict = meta or {}
            # Chroma returns cosine distance in [0, 2]; similarity = 1 - distance
            similarity = max(0.0, min(1.0, 1.0 - float(dist)))
            passages.append(
                Passage(
                    chunk_id=chunk_id,
                    text=text,
                    source=str(meta_dict.get("source", "unknown")),
                    section=str(meta_dict.get("section", "General")),
                    page=int(meta_dict["page"]) if meta_dict.get("page") is not None else None,  # type: ignore[arg-type]
                    score=round(similarity, 4),
                    doc_version=str(meta_dict.get("doc_version", "unknown")),
                    superseded=bool(meta_dict.get("superseded", False)),
                )
            )

        log.info(
            "retrieved chunks",
            extra={
                "question": question[:80],
                "k": k,
                "chunk_ids": [p.chunk_id for p in passages],
                "scores": [p.score for p in passages],
                "wants_older_version": wants_older,
                "top_score": passages[0].score if passages else 0.0,
            },
        )
        return passages
