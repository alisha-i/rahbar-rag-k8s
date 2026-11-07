"""Unit tests for the 5-stage indexer pipeline."""

from __future__ import annotations

from pipeline.chunk import chunk
from pipeline.clean import clean
from pipeline.models import Document, Page


def test_clean_strips_repeating_headers_while_preserving_headings() -> None:
    doc = Document(
        source="handbook.pdf",
        doc_version="2026",
        superseded=False,
        pages=[
            Page(source="handbook.pdf", page=1, text="FAST-NUCES Running Header\n1.1 Academic Policy\nBody 1\nPage 1"),
            Page(source="handbook.pdf", page=2, text="FAST-NUCES Running Header\n1.2 Grading Policy\nBody 2\nPage 2"),
            Page(source="handbook.pdf", page=3, text="FAST-NUCES Running Header\n1.3 Attendance\nBody 3\nPage 3"),
        ],
    )
    cleaned = clean(doc)
    assert len(cleaned.pages) == 3
    for p in cleaned.pages:
        assert "FAST-NUCES Running Header" not in p.text
        assert "Page 1" not in p.text
        assert "Page 2" not in p.text
    assert "1.1 Academic Policy" in cleaned.pages[0].text
    assert "1.2 Grading Policy" in cleaned.pages[1].text
    assert "1.3 Attendance" in cleaned.pages[2].text


def test_structure_aware_chunking_preserves_metadata_and_overlap() -> None:
    doc = Document(
        source="rules.pdf",
        doc_version="2026-06",
        superseded=False,
        pages=[
            Page(
                source="rules.pdf",
                page=5,
                text="5.4 Academic Warning\nA student who fails to maintain a CGPA of 2.00 is placed on academic warning.\n"
                "The student must improve their CGPA in the subsequent semester to avoid dismissal.\n"
                "Academic counseling is mandatory during probation periods.",
            )
        ],
    )
    chunks = chunk(doc, size=20, overlap=5)
    assert len(chunks) >= 1
    for c in chunks:
        assert c.source == "rules.pdf"
        assert c.page == 5
        assert c.doc_version == "2026-06"
        assert c.superseded is False
        assert "5.4 Academic Warning" in c.section or "5.4 Academic Warning" in c.text
