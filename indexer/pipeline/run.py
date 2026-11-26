"""The indexer Job's entry point with RAGOps Verification & Incremental Updates.

Supports:
- 4-check Verification stage (Quality, Completeness, Recency, Uniqueness) + Quarantine.
- Incremental update: unchanged documents embed 0 chunks; changed docs replace only their chunks;
  deleted docs leave 0 orphan chunks; superseded docs are archived.
- Batch B evaluation and rollback demo.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import IndexerSettings, get_settings
from .models import Chunk, Document
from .state import StateManager
from .verify import verify_corpus

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("rahbar.indexer")


def _log_event(msg: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "logger": "rahbar.indexer",
                "msg": msg,
                **fields,
            }
        ),
        flush=True,
    )


def _front_matter(path: Path) -> dict[str, str]:
    """Read `key: value` lines between leading '---' markers."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def discover_files(corpus_dir: str, extra_dirs: tuple[str, ...] = ()) -> list[tuple[Path, dict[str, str]]]:
    """Find all documents in the corpus root (plus any extra sub-folders, e.g. batch_b)."""
    found_all: list[tuple[Path, dict[str, str]]] = []
    for d in (corpus_dir, *extra_dirs):
        found_all.extend(_discover_one(d))
    return found_all


def _discover_one(corpus_dir: str) -> list[tuple[Path, dict[str, str]]]:
    root = Path(corpus_dir)
    if not root.exists():
        return []

    index_json = root / "_index.json"
    if index_json.exists():
        try:
            entries = json.loads(index_json.read_text(encoding="utf-8"))
            return [(root / e["file"], {
                "source": e.get("file", ""),
                "title": e.get("title", ""),
                "doc_version": str(e.get("doc_version", "unknown")),
                "superseded": str(e.get("superseded", "false")),
            }) for e in entries]
        except Exception:
            pass

    found: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(root.glob("*")):
        if path.is_dir() or path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme") or path.name.startswith("."):
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        if not meta.get("source"):
            meta["source"] = path.name
        found.append((path, meta))
    return found


def discover(corpus_dir: str) -> list[tuple[Path, str, bool]]:
    """Legacy helper for backward compatibility with starter tests."""
    files = discover_files(corpus_dir)
    return [(path, meta.get("doc_version", "unknown"), meta.get("superseded", "false").lower() == "true") for path, meta in files]


def corpus_root(corpus_dir: str) -> Path:
    """Batch B lives in <root>/batch_b; everything else is the root itself."""
    p = Path(corpus_dir)
    return p.parent if p.name == "batch_b" else p


def quarantine_dir_for(settings: IndexerSettings, corpus_dir: str) -> Path:
    return Path(settings.quarantine_dir) if settings.quarantine_dir else corpus_root(corpus_dir) / "quarantine"


def archive_dir_for(settings: IndexerSettings, corpus_dir: str) -> Path:
    return Path(settings.archive_dir) if settings.archive_dir else corpus_root(corpus_dir) / "archive"


def state_file_for(settings: IndexerSettings, corpus_dir: str) -> Path:
    """One state file per collection: the candidate collection (different chunking) must not
    think the corpus is "unchanged" just because the live collection already indexed it."""
    name = ".index_state.json" if settings.collection == "rahbar" else f".index_state.{settings.collection}.json"
    return Path(corpus_dir) / name


def run_pipeline(
    settings: IndexerSettings,
    target_corpus_dir: str | None = None,
    report_path: str = "eval/results/verification_report.json",
    wipe: bool = False,
    extra_dirs: tuple[str, ...] = (),
) -> dict[str, object]:
    from .chunk import chunk
    from .clean import clean
    from .embed import embed
    from .extract import extract
    from .store import delete_chunks, get_client, store, update_metadata

    corpus_dir = target_corpus_dir or settings.corpus_dir
    files = discover_files(corpus_dir, extra_dirs)
    if not files:
        _log_event("no documents found to index", corpus_dir=corpus_dir)
        return {"embedded_chunks": 0, "verified_docs": 0, "quarantined": 0}

    # Step 1: Extract all raw documents
    extracted_docs: list[tuple[Document, dict[str, str], Path]] = []
    for path, meta in files:
        doc_version = meta.get("doc_version", "unknown")
        superseded = meta.get("superseded", "false").lower() == "true"
        doc = clean(extract(str(path), doc_version, superseded))
        extracted_docs.append((doc, meta, path))

    # Step 2: Verification Stage (A1: Quality, Completeness, Recency, Uniqueness)
    quarantine_dir = quarantine_dir_for(settings, corpus_dir)
    verified_docs, v_report = verify_corpus(
        extracted_docs,
        quarantine_dir=quarantine_dir,
        report_path=report_path,
    )
    _log_event(
        "verification completed",
        scanned=v_report.total_scanned,
        passed=v_report.passed_count,
        quarantined=v_report.quarantined_count,
        report=str(report_path),
    )
    for d in v_report.details:
        _log_event("verification", document=d.source, passed=d.passed,
                   checks={k: ("pass" if v["pass"] else "FAIL: " + v["reason"]) for k, v in d.checks.items()})

    # Step 3: Incremental State & Diff Calculation (A2)
    state_file = state_file_for(settings, corpus_dir)
    archive_dir = archive_dir_for(settings, corpus_dir)
    state_mgr = StateManager(state_file=state_file, archive_dir=archive_dir)

    diff = state_mgr.compute_diff(verified_docs)
    _log_event(
        "incremental diff computed",
        unchanged=len(diff.unchanged),
        changed=len(diff.changed),
        new_docs=len(diff.new_docs),
        deleted=len(diff.deleted_sources),
        superseded=len(diff.superseded_to_archive),
    )

    client = None
    try:
        client = get_client(settings)
        client.heartbeat()
    except Exception as exc:
        if os.environ.get("INDEXER_DRY_RUN", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError(f"Chroma not reachable at {settings.chroma_host}:{settings.chroma_port}: {exc}") from exc
        log.warning("chroma client not reachable (%s); INDEXER_DRY_RUN set, continuing without writes", exc)
        client = None
    wrote_anything = False

    # Clean up orphan chunks for deleted documents
    for del_src in diff.deleted_sources:
        orphan_chunks = state_mgr.remove_doc(del_src)
        if client and orphan_chunks:
            delete_chunks(client, settings, orphan_chunks)
            wrote_anything = True
        _log_event("orphan chunks removed for deleted doc", doc=del_src, orphan_count=len(orphan_chunks))

    # Handle superseded documents archiving
    for s_doc in diff.superseded_to_archive:
        state_mgr.archive_superseded(s_doc, Path(corpus_dir) / s_doc.source)
        if client and s_doc.source in state_mgr.records:
            update_metadata(client, settings, state_mgr.records[s_doc.source].chunk_ids, {"superseded": True})
            wrote_anything = True

    docs_to_index = diff.needs_embedding
    if not docs_to_index and not wipe:
        _log_event("unchanged corpus detected; embedded zero chunks", embedded_chunks=0)
        state_mgr.save()
        if wrote_anything:
            from .store import _invalidate_semantic_cache
            _invalidate_semantic_cache()
        return {
            "embedded_chunks": 0,
            "unchanged_docs": len(diff.unchanged),
            "verified_docs": len(verified_docs),
            "quarantined": v_report.quarantined_count,
        }

    # Chunk and embed ONLY new or changed documents
    new_chunks: list[Chunk] = []
    for doc in docs_to_index:
        # If changed, remove old chunks first
        if doc.source in state_mgr.records:
            old_ids = state_mgr.remove_doc(doc.source)
            if client and old_ids:
                delete_chunks(client, settings, old_ids)
                wrote_anything = True

        pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
        _log_event("chunked", source=doc.source, chunks=len(pieces))
        new_chunks.extend(pieces)
        state_mgr.record_indexed_doc(doc, [p.id for p in pieces])

    embedded_count = 0
    if new_chunks:
        vectors = embed([c.text for c in new_chunks], settings)
        if client:
            store(new_chunks, vectors, settings, wipe=wipe)
        embedded_count = len(new_chunks)

    if wrote_anything and not new_chunks:
        from .store import _invalidate_semantic_cache
        _invalidate_semantic_cache()
    state_mgr.save()
    _log_event("indexing finished", embedded_chunks=embedded_count, total_verified=len(verified_docs))
    return {
        "embedded_chunks": embedded_count,
        "unchanged_docs": len(diff.unchanged),
        "verified_docs": len(verified_docs),
        "quarantined": v_report.quarantined_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RAHBAR index with RAGOps verification & incremental updates.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    parser.add_argument("--corpus-dir", type=str, default=None, help="Directory containing documents to index")
    parser.add_argument("--batch-b", action="store_true", help="Run ingestion on Batch B (corpus/batch_b)")
    parser.add_argument("--wipe", action="store_true", help="Force wipe collection and rebuild")
    parser.add_argument("--rollback", type=str, default=None,
                        help="Rollback demo: restore an archived (superseded) document so retrieval serves it again")
    parser.add_argument("--supersede", type=str, default=None,
                        help="With --rollback: the newer document to mark superseded instead")
    parser.add_argument("--count-by-doc", action="store_true",
                        help="Print chunk counts per document id (A2 orphan-chunk proof) and exit")
    args = parser.parse_args()

    settings = get_settings()
    started = time.perf_counter()

    if args.count_by_doc:
        from .store import count_by_document, get_client

        counts = count_by_document(get_client(settings), settings)
        _log_event("chunk count by document", collection=settings.collection,
                   total=sum(v["active"] + v["superseded"] for v in counts.values()), by_document=counts)
        return 0

    if args.rollback:
        corpus_dir = args.corpus_dir or settings.corpus_dir
        state_mgr = StateManager(
            state_file=state_file_for(settings, corpus_dir),
            archive_dir=archive_dir_for(settings, corpus_dir),
        )
        success = state_mgr.restore_superseded(args.rollback, Path(corpus_dir))
        if success:
            from .store import _invalidate_semantic_cache, get_client, update_metadata

            client = get_client(settings)
            rec = state_mgr.records.get(args.rollback)
            if rec:
                update_metadata(client, settings, rec.chunk_ids, {"superseded": False})
            if args.supersede and args.supersede in state_mgr.records:
                newer = state_mgr.records[args.supersede]
                update_metadata(client, settings, newer.chunk_ids, {"superseded": True})
                newer.superseded = True
                state_mgr.save()
            _invalidate_semantic_cache()
        _log_event("rollback completed", document=args.rollback, superseded=args.supersede, success=success)
        return 0 if success else 1

    # Batch B is dropped into <corpus>/batch_b and ingested TOGETHER with the existing corpus:
    # unchanged batch-A documents embed 0 chunks, and the recency check can see that a batch-B
    # document is a newer version of a batch-A document.
    base_dir = args.corpus_dir or settings.corpus_dir
    target_dir = base_dir
    extra = (str(Path(base_dir) / "batch_b"),) if args.batch_b else ()
    results_dir = Path(os.environ.get("RESULTS_DIR", "eval/results"))
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    report_file = str(results_dir / f"verification_{'batch_b' if args.batch_b else 'batch_a'}_{stamp}.json")

    _log_event(
        "indexer starting",
        target_dir=target_dir,
        embed_model=settings.embed_model,
        collection=settings.collection,
        batch_b=args.batch_b,
    )

    try:
        if args.dummy:
            from .dummy import write_dummy_index
            from .store import get_client

            client = get_client(settings)
            count = write_dummy_index(settings, client)
            res: dict[str, object] = {"embedded_chunks": count}
        else:
            res = run_pipeline(settings, target_corpus_dir=target_dir, report_path=report_file,
                               wipe=args.wipe, extra_dirs=extra)
    except Exception as exc:
        _log_event("indexer failed", error=str(exc), error_type=type(exc).__name__)
        return 2

    _log_event("indexer finished", seconds=round(time.perf_counter() - started, 2), **res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
