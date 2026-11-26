"""A2: Incremental Ingestion & Corpus State Manager (RAGOps Section 4.2.2 - 4.2.4).

Ensures:
1. Re-running on unchanged corpus embeds 0 chunks (proves incremental caching).
2. A changed document replaces only its own chunks.
3. A deleted document leaves 0 orphan chunks in the vector store.
4. A superseded document is archived to `corpus/archive/` and excluded from retrieval via `superseded: true`.
5. Rollback demo restores an archived document and unmarks it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Document

log = logging.getLogger("rahbar.state")


@dataclass
class DocRecord:
    source: str
    sha256: str
    doc_version: str
    superseded: bool
    chunk_ids: list[str] = field(default_factory=list)
    chunk_count: int = 0
    indexed_at: str = ""


@dataclass
class IngestionDiff:
    unchanged: list[Document] = field(default_factory=list)
    changed: list[Document] = field(default_factory=list)
    new_docs: list[Document] = field(default_factory=list)
    deleted_sources: list[str] = field(default_factory=list)
    superseded_to_archive: list[Document] = field(default_factory=list)

    @property
    def needs_embedding(self) -> list[Document]:
        return self.new_docs + self.changed


class StateManager:
    def __init__(self, state_file: str | Path = "corpus/.index_state.json", archive_dir: str | Path = "corpus/archive") -> None:
        self.state_file = Path(state_file)
        self.archive_dir = Path(archive_dir)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.records: dict[str, DocRecord] = self._load()

    def _load(self) -> dict[str, DocRecord]:
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {src: DocRecord(**rec) for src, rec in data.get("documents", {}).items()}
        except Exception as exc:
            log.warning("failed to load state file (%s); starting fresh", exc)
            return {}

    def save(self) -> None:
        payload = {
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "documents": {src: asdict(rec) for src, rec in self.records.items()},
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def compute_doc_hash(doc: Document) -> str:
        content = f"{doc.source}:{doc.doc_version}:{doc.superseded}:" + "\n".join(p.text for p in doc.pages)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def compute_diff(self, active_docs: list[Document]) -> IngestionDiff:
        diff = IngestionDiff()
        active_map = {d.source: d for d in active_docs}

        # Check for deleted documents
        for prev_src in list(self.records.keys()):
            if prev_src not in active_map:
                diff.deleted_sources.append(prev_src)

        # Check current documents
        for doc in active_docs:
            curr_hash = self.compute_doc_hash(doc)
            if doc.source not in self.records:
                diff.new_docs.append(doc)
            else:
                prev = self.records[doc.source]
                if prev.sha256 == curr_hash and prev.superseded == doc.superseded:
                    diff.unchanged.append(doc)
                else:
                    diff.changed.append(doc)

            # Check if superseded
            if doc.superseded:
                diff.superseded_to_archive.append(doc)

        return diff

    def record_indexed_doc(self, doc: Document, chunk_ids: list[str]) -> None:
        self.records[doc.source] = DocRecord(
            source=doc.source,
            sha256=self.compute_doc_hash(doc),
            doc_version=doc.doc_version,
            superseded=doc.superseded,
            chunk_ids=chunk_ids,
            chunk_count=len(chunk_ids),
            indexed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def remove_doc(self, source: str) -> list[str]:
        """Remove doc from state and return its orphan chunk IDs for vector store deletion."""
        if source in self.records:
            old_chunks = self.records[source].chunk_ids
            del self.records[source]
            return old_chunks
        return []

    def archive_superseded(self, doc: Document, source_file_path: Path | None = None) -> None:
        """Move / copy superseded doc to archive folder."""
        if source_file_path and source_file_path.exists():
            dest = self.archive_dir / source_file_path.name
            shutil.copy2(source_file_path, dest)
            log.info("archived superseded document to %s", dest)

    def restore_superseded(self, source_name: str, restore_dest_dir: Path) -> bool:
        """Rollback demo: restore an archived document and set superseded=False."""
        archived_file = self.archive_dir / source_name
        if not archived_file.exists():
            log.error("cannot restore %s; not found in archive %s", source_name, self.archive_dir)
            return False
        shutil.copy2(archived_file, restore_dest_dir / source_name)
        if source_name in self.records:
            self.records[source_name].superseded = False
        self.save()
        log.info("restored document %s from archive successfully", source_name)
        return True
