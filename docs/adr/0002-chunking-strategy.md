# ADR 0002: Structure-Aware Chunking with Semantic Overlap

## Status
Accepted · 2026-11-06

## Context
In a RAG system indexing university academic and administrative regulations, document chunking represents the boundary between raw document structure and dense vector embedding semantics. Raw documents (e.g., FAST-NUCES Academic Handbooks, grading schemes, and fee rules) feature hierarchical structures: numbered chapters, clauses (e.g., `1.21 Attendance`, `5.4 Academic Warning`), tabular fee schedules, and multi-paragraph definitional policies.

Two main chunking strategies were evaluated:
1. **Fixed-Size Sliding Window Chunking:** Splitting text into fixed character or token counts (e.g., 200 tokens) with fixed step sizes, completely oblivious to document structure.
2. **Structure-Aware Chunking with Semantic Overlap:** Parsing headings, section boundaries, and paragraphs first, and only applying overlapping token windowing when a structural unit exceeds the target chunk size.

## Decision
We implement a **Structure-Aware Hierarchical Chunker** in `indexer/pipeline/chunk.py`:
1. **Structural Boundary Respect:** Headings and numbered clauses (matching `^(#+\s+|\d+(\.\d+)*\s+[A-Z]|Section\s+\d+)`) establish section boundaries. The active heading is propagated into the `section` metadata field of every child chunk.
2. **Recursive Splitting:** Text within a section is split first by paragraphs (`\n\n`), and only if an individual paragraph exceeds the target `chunk_size` is it decomposed into sentences (`[.!?]\s+`).
3. **Controlled Overlap:** When chunk boundaries must be cut across sentences or paragraphs, an overlap of `chunk_overlap` tokens is retained from the preceding window. This ensures that boundary concepts (such as conditional clauses: *"provided that..."*) are not orphaned.
4. **Metadata Preservation:** Every chunk retains explicit origin metadata (`source`, `section`, `page`, `doc_version`, and `superseded`).

## Options Considered

- **Option A: Naive Fixed-Character Chunking (e.g., 500 chars with 50 chars overlap)**  
  *Rejected.* Frequently cuts across numbers (e.g., separating "2." from "00 CGPA") or splits clause numbers from their rule statements. Crucial context is destroyed before vector embedding occurs.
- **Option B: Fixed-Token Windows with Overlap (e.g., 200 tokens, 30 overlap)**  
  *Rejected.* While better than raw characters, it still blind-cuts across paragraphs, separating policy exceptions from their parent clauses.
- **Option C: Structure-Aware Chunking with Configurable Overlap (Selected)**  
  *Accepted.* Respects natural semantic units (clauses and paragraphs). Only splits when a section exceeds target size (default 400 tokens), preserving headings in metadata.

## Consequences

- **Positive:**
  - High precision citation: Each chunk knows exactly which clause heading (`section`) and `page` it belongs to.
  - Zero context fragmentation for clauses that fit within the chunk size.
  - Mitigates Failure Mode B (poor chunks causing orphaned references).
- **Negative / Trade-offs:**
  - Variable chunk sizes: chunks are not identically sized vectors, though all remain under the maximum bound.
  - Slight storage amplification due to overlapping boundary tokens (~1.25x), which is acceptable for ChromaDB storage capacity.
