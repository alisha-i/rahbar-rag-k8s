"""Unit tests for RAGOps Verification Stage (A1) and Incremental Ingestion (A2)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pipeline.models import Document, Page
from pipeline.state import StateManager
from pipeline.verify import verify_corpus


def test_verification_catches_all_four_checks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        quarantine_dir = tmp_path / "quarantine"
        report_file = tmp_path / "report.json"

        # 1. Clean valid doc
        f1 = tmp_path / "clean.md"
        f1.write_text("---\nsource: clean.md\ntitle: Policy\ndoc_version: 2026\n---\n# Policy\nValid content for testing rules.")
        d1 = Document(source="clean.md", doc_version="2026", superseded=False, pages=[Page(source="clean.md", page=1, text="# Policy\nValid content for testing rules.")])

        # 2. Exact duplicate
        f2 = tmp_path / "dup.md"
        f2.write_text(f1.read_text())
        d2 = Document(source="dup.md", doc_version="2026", superseded=False, pages=[Page(source="dup.md", page=1, text="# Policy\nValid content for testing rules.")])

        # 3. Corrupt/garbled doc
        f3 = tmp_path / "corrupt.md"
        f3.write_text("---\nsource: corrupt.md\ntitle: Err\ndoc_version: 2026\n---\nGARBLED_STREAM_ERROR: %$#@!")
        d3 = Document(source="corrupt.md", doc_version="2026", superseded=False, pages=[Page(source="corrupt.md", page=1, text="GARBLED_STREAM_ERROR: %$#@!")])

        # 4. Missing metadata doc
        f4 = tmp_path / "missing.md"
        f4.write_text("No header, no title, just random body.")
        d4 = Document(source="missing.md", doc_version="unknown", superseded=False, pages=[Page(source="missing.md", page=1, text="No header, no title, just random body.")])

        docs = [
            (d1, {"source": "clean.md", "title": "Policy", "doc_version": "2026"}, f1),
            (d2, {"source": "dup.md", "title": "Policy", "doc_version": "2026"}, f2),
            (d3, {"source": "corrupt.md", "title": "Err", "doc_version": "2026"}, f3),
            (d4, {"source": "missing.md", "title": "", "doc_version": "unknown"}, f4),
        ]

        verified, report = verify_corpus(docs, quarantine_dir=quarantine_dir, report_path=report_file)

        assert report.total_scanned == 4
        assert report.passed_count == 1
        assert report.quarantined_count == 3
        assert len(verified) == 1
        assert verified[0].source == "clean.md"

        # Check quarantine folder has the 3 failed files
        q_files = [p.name for p in quarantine_dir.glob("*")]
        assert "dup.md" in q_files
        assert "corrupt.md" in q_files
        assert "missing.md" in q_files


def test_incremental_state_tracks_changes_and_deletions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        state_file = tmp_path / ".index_state.json"
        archive_dir = tmp_path / "archive"
        mgr = StateManager(state_file=state_file, archive_dir=archive_dir)

        d1 = Document(source="rules.md", doc_version="2026", superseded=False, pages=[Page(source="rules.md", page=1, text="Rule 1")])
        mgr.record_indexed_doc(d1, ["rules.md#001"])
        mgr.save()

        # Re-run on unchanged doc
        diff1 = mgr.compute_diff([d1])
        assert len(diff1.unchanged) == 1
        assert len(diff1.needs_embedding) == 0

        # Change doc
        d1_mod = Document(source="rules.md", doc_version="2026", superseded=False, pages=[Page(source="rules.md", page=1, text="Rule 1 Modified")])
        diff2 = mgr.compute_diff([d1_mod])
        assert len(diff2.changed) == 1
        assert len(diff2.needs_embedding) == 1

        # Delete doc
        diff3 = mgr.compute_diff([])
        assert "rules.md" in diff3.deleted_sources
        orphans = mgr.remove_doc("rules.md")
        assert orphans == ["rules.md#001"]
