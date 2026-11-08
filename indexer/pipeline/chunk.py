"""Stage 3: Chunk. Structure-aware chunking, with overlap."""

from __future__ import annotations

import re

from .models import Chunk, Document

HEADING_PATTERN = re.compile(
    r"^(?:#{1,6}\s+(.+)|(\d+(?:\.\d+)*\s+[A-Z][^\n]{2,80})|(?:Section|Chapter|Article)\s+\d+[:\s][^\n]{2,80})",
    re.M,
)


def _approx_token_count(text: str) -> int:
    """Approximate token count: standard rule of thumb is ~4 characters per token or whitespace words * 1.3."""
    words = text.split()
    return max(1, int(len(words) * 1.3))


def _split_into_sentences(text: str) -> list[str]:
    """Split paragraph into individual sentences while preserving punctuation."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def _split_into_paragraphs(text: str) -> list[str]:
    """Split section content into distinct paragraphs."""
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Structure-aware chunking: splits on sections, then paragraphs, then sentences,
    keeping each chunk under `size` tokens, with `overlap` tokens shared between neighbours.

    Preserves section titles, version metadata, and page numbers across sliding windows.
    """
    Every chunk carries source, section, page, doc_version, and superseded metadata.
    """
    chunks: list[Chunk] = []
    chunk_counter = 0

    for page in doc.pages:
        raw_text = page.text.strip()
        if not raw_text:
            continue

        # Detect sections / headings within this page
        # Break page into (heading, body) pairs
        sections: list[tuple[str, str]] = []
        heading_matches = list(HEADING_PATTERN.finditer(raw_text))

        if not heading_matches:
            sections.append(("General", raw_text))
        else:
            prev_pos = 0
            current_heading = "Preamble"
            for match in heading_matches:
                body = raw_text[prev_pos:match.start()].strip()
                if body:
                    sections.append((current_heading, body))
                current_heading = match.group(0).lstrip("#").strip()
                prev_pos = match.end()
            remainder = raw_text[prev_pos:].strip()
            if remainder:
                sections.append((current_heading, remainder))

        for heading, section_text in sections:
            paragraphs = _split_into_paragraphs(section_text)
            current_window: list[str] = []
            current_tokens = 0

            for p in paragraphs:
                p_tokens = _approx_token_count(p)

                if current_tokens + p_tokens <= size:
                    current_window.append(p)
                    current_tokens += p_tokens
                else:
                    # If single paragraph is larger than size, split on sentences
                    if not current_window:
                        sentences = _split_into_sentences(p)
                        sent_window: list[str] = []
                        sent_tokens = 0
                        for s in sentences:
                            s_toks = _approx_token_count(s)
                            if sent_tokens + s_toks <= size:
                                sent_window.append(s)
                                sent_tokens += s_toks
                            else:
                                if sent_window:
                                    chunk_counter += 1
                                    chunk_id = f"{doc.source}-p{page.page}-{chunk_counter:04d}"
                                    chunk_body = f"{heading}\n\n" + " ".join(sent_window)
                                    chunks.append(
                                        Chunk(
                                            id=chunk_id,
                                            text=chunk_body,
                                            source=doc.source,
                                            section=heading,
                                            page=page.page,
                                            doc_version=doc.doc_version,
                                            superseded=doc.superseded,
                                        )
                                    )
                                    # Overlap sentences
                                    overlap_tokens = 0
                                    overlap_sents: list[str] = []
                                    for prev_s in reversed(sent_window):
                                        st = _approx_token_count(prev_s)
                                        if overlap_tokens + st <= overlap:
                                            overlap_sents.insert(0, prev_s)
                                            overlap_tokens += st
                                        else:
                                            break
                                    sent_window = overlap_sents + [s]
                                    sent_tokens = sum(_approx_token_count(x) for x in sent_window)
                                else:
                                    # Extremely long sentence
                                    chunk_counter += 1
                                    chunk_id = f"{doc.source}-p{page.page}-{chunk_counter:04d}"
                                    chunks.append(
                                        Chunk(
                                            id=chunk_id,
                                            text=f"{heading}\n\n{s}",
                                            source=doc.source,
                                            section=heading,
                                            page=page.page,
                                            doc_version=doc.doc_version,
                                            superseded=doc.superseded,
                                        )
                                    )
                                    sent_window = []
                                    sent_tokens = 0
                        if sent_window:
                            current_window = sent_window
                            current_tokens = sent_tokens
                    else:
                        # Emit the current window as a chunk
                        chunk_counter += 1
                        chunk_id = f"{doc.source}-p{page.page}-{chunk_counter:04d}"
                        chunk_body = f"{heading}\n\n" + "\n\n".join(current_window)
                        chunks.append(
                            Chunk(
                                id=chunk_id,
                                text=chunk_body,
                                source=doc.source,
                                section=heading,
                                page=page.page,
                                doc_version=doc.doc_version,
                                superseded=doc.superseded,
                            )
                        )

                        # Carry forward paragraph overlap
                        overlap_tokens = 0
                        overlap_paras: list[str] = []
                        for prev_p in reversed(current_window):
                            pt = _approx_token_count(prev_p)
                            if overlap_tokens + pt <= overlap:
                                overlap_paras.insert(0, prev_p)
                                overlap_tokens += pt
                            else:
                                break
                        current_window = overlap_paras + [p]
                        current_tokens = sum(_approx_token_count(x) for x in current_window)

            if current_window:
                chunk_counter += 1
                chunk_id = f"{doc.source}-p{page.page}-{chunk_counter:04d}"
                chunk_body = f"{heading}\n\n" + "\n\n".join(current_window)
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        text=chunk_body,
                        source=doc.source,
                        section=heading,
                        page=page.page,
                        doc_version=doc.doc_version,
                        superseded=doc.superseded,
                    )
                )

    return chunks
