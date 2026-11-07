# Corpus Specification & Grounding Properties

**Source:** FAST-NUCES Public Website (`nu.edu.pk`)  
**Total Documents Cataloged:** 9 documents (plus 1 deliberately absent policy)  
**Total Page Volume:** ~180+ pages across PDFs and HTML tables  
**Verification Manifest:** `corpus/MANIFEST.md` with SHA-256 cryptographic digests  

---

## 1. Overview and Grounding Objective

In Retrieval-Augmented Generation (RAG), a curated corpus is an active benchmark designed to expose retrieval and generation failure modes. If a corpus contains only modern, clean, plain-text documents with homogeneous definitions, critical failure modes (such as quoting repealed policies, jumbled tabular lookups, OCR degradation, and hallucinating over missing information) remain unreachable and therefore unverified.

The RAHBAR corpus is drawn directly from official, public FAST-NUCES academic and administrative documents. It explicitly models all seven properties mandated in Section 2 of the specification.

---

## 2. Seven Required Corpus Properties

| # | Required Property | Document(s) that Provide It | Failure Mode Made Reachable | Concrete Evidence & Verification Notes |
|---|---|---|---|---|
| **1** | **At least 6 documents, 40+ pages in total** | 9 distinct documents (~180+ pages) from FAST-NUCES portals | None; establishes production scale and realistic indexing latency. | Spans academic handbooks, prospectuses, annual reports, ORIC research policies, and admissions fee structures. |
| **2** | **Two versions of one policy (policy revision pair)** | 1. `Undergraduate Academic Rules (Aug 2023)` (`superseded: true`, version `2023-08`)<br>2. `Academic Rules Handbook (Jun 2026)` (`superseded: false`, version `2026-06`) | **Failure Mode E (Outdated Source / Repealed Clause):** Without explicit `doc_version` and `superseded` metadata filtering, vector semantic similarity will match the repealed 2023 rule rather than the 2026 update. | In the 2023 rules, attendance requirements and probation criteria are numbered differently from the 2026 handbook (clause 1.21 vs clause 1). The 2023 policy is explicitly flagged `superseded: true` in metadata, enabling retriever filtering. |
| **3** | **Scanned, image-only PDF with no text layer** | `Faculty Entrepreneurship and Technical Start-ups Policy` (`27.pdf`, version `2022-05`) | **Failure Mode A (Extraction Failure / Silent Information Loss):** Standard PDF text extraction (`pypdf`, `pdfplumber`) extracts 0 characters. | Running `pdffonts` on this file produces an empty font table (`name, type, emb, sub, uni` are completely blank), confirming it is an image-only scan. Our pipeline intercepts empty page extracts and invokes Tesseract OCR (`pytesseract` + `pdf2image`) to extract the scanned clauses. |
| **4** | **Document with a table carrying real information** | 1. `Annual Report 2019-20` & `Annual Report 2023-24` (Faculty, enrollment, and publication tables)<br>2. `Fee Structure page` (`fee-structure-page.html`, version `2026-27`) | **Failure Mode A (Table Extraction / Mangled Structural Columns):** Naive multi-column layout extraction reads across columns horizontally, merging adjacent cell values into nonsensical text. | Tables contain structured credit-hour tuition rates, admission charges, security deposits, and refund timelines. `pdfplumber` table extraction and HTML table DOM parsers preserve table rows and header associations. |
| **5** | **Long definitions spanning multiple paragraphs** | `Student Handbook (Jun 2026)` (`student-handbook-jun-2026.pdf`, version `2026-06`) | **Failure Mode B (Chunk Boundary Splitting):** Fixed-size window chunking splits complex clauses or multi-sentence definitions across chunk boundaries, causing orphaned pronouns and loss of context. | Contains a multi-part Generative Artificial Intelligence and Academic Integrity Policy (Section 4) with nested procedural definitions and clauses that must be preserved within cohesive section-aware chunks. |
| **6** | **Realistic topic deliberately absent** | `Transport and Hostel Facilities` (Prospectus section) is **intentionally omitted** from `sources.yaml` and indexing. | **Failure Mode (Honest Abstention / Section 3.4):** When a user asks about hostel allotments or bus transport routes, the retriever finds nothing clearing the relevance floor. | While `Student Handbook` mentions that university hostels and buses exist, the specific rules, fee schedules, curfew timings, and application forms are completely absent. The system must return `answered_from_context: false` and decline to answer. |
| **7** | **Exact-term content: clause numbers, codes, CGPA thresholds, fees** | 1. `Academic Rules Handbook (Jun 2026)` (Clause numbers: `1.21` attendance, `5.4` academic warning)<br>2. `Grading page` (Grade points, SGPA/CGPA cutoffs, e.g., CGPA < 2.00 warning threshold)<br>3. `Fee Structure page` (Tuition fee per credit hour, admission security deposit PKR figures) | **Failure Mode C (Embedding Blur / Exact Term Confusion):** Dense embeddings blur numbers and tokens (treating "Clause 5.4" similarly to "Clause 5.5" or "CGPA 2.0" to "CGPA 2.5"). | Grounded retrieval must preserve exact clause headings in chunk metadata (`section`) and retrieve the exact numerical threshold rather than general academic prose. |

---

## 3. Storage & Document Governance Discipline

1. **No Raw PDFs Committed to Git:**  
   Per Hard Rule 7 and repository standards, binary documents live exclusively in `corpus/raw/` (which is gitignored). 
2. **Deterministic Reproducibility:**  
   The manifest `corpus/MANIFEST.md` records the exact origin URL, file size in bytes, and SHA-256 checksum for every external document.
3. **Execution Mode Support:**  
   - **Local & Production Clusters:** The indexer Job downloads and mounts documents from the persistent volume or fetches them into `corpus/raw/`.
   - **CI/CD Ephemeral Clusters:** The fast fixture corpus in `eval/fixtures/` contains lightweight markdown files (`academic-rules-2023.md`, `academic-rules-2026.md`, `fees-2026.md`) with front-matter versioning, allowing end-to-end integration and evaluation runs in under 30 seconds without network bandwidth bottlenecks.
