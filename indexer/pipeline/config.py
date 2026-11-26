"""Indexer configuration, from the same ConfigMap the API reads (section 4.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexerSettings:
    embed_model: str        # EMBED_MODEL: must equal the API's value (section 4.4)
    chroma_host: str
    chroma_port: int
    collection: str
    corpus_dir: str         # where the documents and _index.json sit inside the Job's pod
    chunk_size: int
    chunk_overlap: int
    ollama_url: str
    quarantine_dir: str = ""   # "" -> <corpus root>/quarantine (on the corpus PVC in the cluster)
    archive_dir: str = ""      # "" -> <corpus root>/archive

    @property
    def index_version(self) -> str:
        """Written into the collection metadata and echoed in every trace (A3 D1/F3)."""
        return f"{self.embed_model}|chunk{self.chunk_size}-ov{self.chunk_overlap}"


def get_settings() -> IndexerSettings:
    return IndexerSettings(
        embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        chroma_host=os.environ.get("CHROMA_HOST", "chroma"),
        chroma_port=int(os.environ.get("CHROMA_PORT", 8000)),
        collection=os.environ.get("CHROMA_COLLECTION", "rahbar"),
        corpus_dir=os.environ.get("CORPUS_DIR", "/corpus"),
        chunk_size=int(os.environ.get("CHUNK_SIZE", 400)),
        chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", 50)),
        ollama_url=os.environ.get("OLLAMA_URL", "http://host.minikube.internal:11434"),
        quarantine_dir=os.environ.get("QUARANTINE_DIR", ""),
        archive_dir=os.environ.get("ARCHIVE_DIR", ""),
    )
