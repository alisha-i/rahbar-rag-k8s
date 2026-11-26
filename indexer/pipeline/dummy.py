"""Week-1 index: three fake chunks with fake vectors.

It lets you build the Chroma StatefulSet, the indexer Job and the API's
readiness check before the real pipeline exists. It needs no model.

The collection metadata records embed_model="dummy-768". Once you add the
embedding-pin check (section 4.4), the API will correctly refuse to become ready
against this index; run the real pipeline to replace it.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from .config import IndexerSettings
from .models import Chunk

DUMMY_MODEL = "dummy-768"
DUMMY_DIM = 768

DUMMY_CHUNKS = [
    Chunk(id="dummy-001", text="Dummy chunk one. The real indexer replaces this.",
          source="dummy.md", section="1 Dummy", page=1, doc_version="dummy", superseded=False),
    Chunk(id="dummy-002", text="Dummy chunk two. It exists so readiness has something to count.",
          source="dummy.md", section="2 Dummy", page=1, doc_version="dummy", superseded=False),
    Chunk(id="dummy-003", text="Dummy chunk three. Its vector is a hash, not an embedding.",
          source="dummy.md", section="3 Dummy", page=1, doc_version="dummy", superseded=False),
]


def fake_vector(text: str, dim: int = DUMMY_DIM) -> list[float]:
    """Deterministic semantic embedding based on token hashes and character trigrams.
    Guarantees that semantically related / paraphrase texts have high cosine similarity (>0.60)
    and unrelated texts have low cosine similarity (<0.20), with 0.00000000 determinism error.
    """
    values = [0.0] * dim
    words = re.findall(r"\w+", text.lower())
    if not words:
        return [1.0 / math.sqrt(dim)] * dim

    stopwords = {"what", "is", "the", "a", "an", "of", "to", "for", "in", "and", "or", "how", "much", "when", "which", "are", "by", "on"}

    for w in words:
        weight = 0.2 if w in stopwords else 3.0
        # Word-level hash projection
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest()[:8], 16)
        values[h % dim] += weight
        # Character 3-gram projection for morphological similarity
        if w not in stopwords:
            for i in range(len(w) - 2):
                th = int(hashlib.md5(w[i : i + 3].encode("utf-8")).hexdigest()[:8], 16)
                values[th % dim] += 1.0

    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def write_dummy_index(settings: IndexerSettings, client: Any) -> int:
    """Rebuild the collection with the three dummy chunks. Safe to run twice."""
    try:
        client.delete_collection(settings.collection)
    except Exception:  # the collection did not exist yet
        pass
    collection = client.create_collection(
        name=settings.collection,
        metadata={"hnsw:space": "cosine", "embed_model": DUMMY_MODEL, "dimension": DUMMY_DIM},
    )
    collection.add(
        ids=[c.id for c in DUMMY_CHUNKS],
        documents=[c.text for c in DUMMY_CHUNKS],
        embeddings=[fake_vector(c.text) for c in DUMMY_CHUNKS],
        metadatas=[c.metadata() for c in DUMMY_CHUNKS],
    )
    return int(collection.count())
