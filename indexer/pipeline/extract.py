"""Stage 1: Extract. PDF, HTML, or Markdown to text, page by page."""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path

from .models import Document, Page

log = logging.getLogger("rahbar.extract")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self._chunks.append(cleaned)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def _ocr_page(pdf_path: str, page_number: int) -> str:
    """Run Tesseract OCR on a single scanned page when native PDF extraction yields empty text."""
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)
        if not images:
            return ""
        text: str = pytesseract.image_to_string(images[0])
        return text.strip()
    except Exception as exc:
        log.warning("OCR extraction failed for %s page %d: %s", pdf_path, page_number, exc)
        return ""


def _extract_pdf(path: str) -> list[Page]:
    """Extract PDF pages using pypdf and pdfplumber, falling back to pytesseract OCR."""
    pages: list[Page] = []
    source_name = Path(path).name

    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                # Also extract any structured tables and append as Markdown table format
                tables = page.extract_tables()
                if tables:
                    table_lines = []
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell or "").replace("\n", " ").strip() for cell in row]
                            table_lines.append(" | ".join(clean_row))
                    if table_lines:
                        text += "\n\n[TABLE DATA]\n" + "\n".join(table_lines)

                is_scanned = len(text.strip()) < 30
                if is_scanned:
                    log.info("detect page %d has no text layer; running OCR via pytesseract", idx)
                    ocr_text = _ocr_page(path, idx)
                    if ocr_text:
                        text = ocr_text
                        pages.append(Page(source=source_name, page=idx, text=text, ocr=True))
                        continue

                pages.append(Page(source=source_name, page=idx, text=text.strip(), ocr=False))
        return pages
    except Exception as exc:
        log.warning("pdfplumber extraction failed for %s, falling back to pypdf: %s", path, exc)

    # Fallback to pypdf if pdfplumber fails
    try:
        import pypdf

        reader = pypdf.PdfReader(path)
        for idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if len(text) < 30:
                ocr_text = _ocr_page(path, idx)
                if ocr_text:
                    pages.append(Page(source=source_name, page=idx, text=ocr_text, ocr=True))
                    continue
            pages.append(Page(source=source_name, page=idx, text=text, ocr=False))
        return pages
    except Exception as exc2:
        log.error("pypdf extraction failed for %s: %s", path, exc2)
        return [Page(source=source_name, page=1, text="", ocr=False)]


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Return the document's text page by page.
    Detect pages with no text layer and OCR them; mark those pages ocr=True.
    """
    file_path = Path(path)
    source_name = file_path.name
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        pages = _extract_pdf(path)
    elif suffix in (".html", ".htm"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        parser = _HTMLTextExtractor()
        parser.feed(content)
        pages = [Page(source=source_name, page=1, text=parser.get_text(), ocr=False)]
    else:
        # Markdown or plain text (.md, .txt)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        # Split on markdown thematic breaks or page indicators if present
        raw_pages = content.split("\n---page---\n")
        pages = [
            Page(source=source_name, page=i, text=p.strip(), ocr=False)
            for i, p in enumerate(raw_pages, start=1)
            if p.strip()
        ]
        if not pages:
            pages = [Page(source=source_name, page=1, text=content.strip(), ocr=False)]

    return Document(
        source=source_name,
        doc_version=doc_version,
        superseded=superseded,
        pages=pages,
    )
