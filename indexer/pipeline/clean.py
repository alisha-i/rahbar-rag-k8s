"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re
from collections import Counter

from .models import Document, Page


def _detect_repeating_lines(pages: list[Page], threshold_ratio: float = 0.6) -> set[str]:
    """Identify lines that appear at the very top or very bottom of most pages (running headers/footers)."""
    if len(pages) < 3:
        return set()

    top_lines = Counter()
    bottom_lines = Counter()

    for p in pages:
        lines = [ln.strip() for ln in p.text.splitlines() if ln.strip()]
        if lines:
            top_lines[lines[0]] += 1
            if len(lines) > 1:
                bottom_lines[lines[-1]] += 1

    min_count = max(2, int(len(pages) * threshold_ratio))
    repeating = set()
    for line, cnt in top_lines.items():
        # Do not strip clause headings like "1.2 Attendance" or "Section 4"
        if cnt >= min_count and not re.match(r"^(\d+(\.\d+)*|[A-Z]\.)\s+", line):
            repeating.add(line)
    for line, cnt in bottom_lines.items():
        # Do not strip if it looks like a substantive clause
        if cnt >= min_count and not re.match(r"^(\d+(\.\d+)*|[A-Z]\.)\s+", line):
            repeating.add(line)

    return repeating


def clean_text(text: str, repeating_lines: set[str] | None = None) -> str:
    """Clean text while preserving headings, clause numbers, and tables."""
    if not text:
        return ""

    lines = text.splitlines()
    cleaned_lines = []
    page_number_pattern = re.compile(r"^(Page\s+\d+(\s+of\s+\d+)?|\d+)\s*$", re.I)

    for line in lines:
        stripped = line.strip()
        # Drop detected running header/footer lines
        if repeating_lines and stripped in repeating_lines:
            continue
        # Drop standalone page numbers like "Page 12" or "12"
        if page_number_pattern.match(stripped):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Rejoin words broken across line wraps (e.g. "pre-\nrequisite" -> "prerequisite")
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

    # Normalize excessive carriage returns and non-breaking spaces
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")

    # Collapse multiple consecutive blank lines into a double newline (paragraph boundary)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def clean(doc: Document) -> Document:
    """Strip repeating headers and footers that repeat on every page;
    keep headings and section boundaries, because chunking and citations depend on them.
    """
    repeating_lines = _detect_repeating_lines(doc.pages)
    cleaned_pages: list[Page] = []

    for p in doc.pages:
        c_text = clean_text(p.text, repeating_lines)
        cleaned_pages.append(Page(source=p.source, page=p.page, text=c_text, ocr=p.ocr))

    return Document(
        source=doc.source,
        doc_version=doc.doc_version,
        superseded=doc.superseded,
        pages=cleaned_pages,
    )
