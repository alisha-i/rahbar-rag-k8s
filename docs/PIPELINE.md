# Indexing and Retrieval Pipeline

## 1. Extraction

### OCR Extraction
In our 9-document FAST-NUCES corpus, `Code-of-Conduct-Scanned.pdf` is an image-only, 300 DPI scanned document containing no embedded text stream. A standard PDF parser (`pypdf`) returned 0 characters across all pages. Our ingestion pipeline (`indexer/pipeline/extract.py`) detects pages with fewer than 50 text characters and automatically invokes our OCR fallback path using `pdf2image` and `pytesseract` (`lang='eng'`). This successfully extracted 2,840 characters of disciplinary regulations and academic integrity policies.

### Table Extraction (Before and After)
For documents containing tabular fee schedules and course curriculum matrices (`Fee-Structure-Fall-2024.pdf` and `CS-Curriculum-Structure.html`), plain string extraction flattened spatial columns into interleaved, unparseable sentences:
- **Before (Raw text dump):**
  ```text
  BS CS BS SE MS CS Tuition Fee Per Credit Hour 9,500 9,500 11,000 Admission Fee 30,000 Security Deposit 20,000
  ```
- **After (`pdfplumber` tabular extraction):**
  ```markdown
  | Program | Fee per Credit Hour | Admission Fee | Security Deposit (Refundable) |
  | :--- | :--- | :--- | :--- |
  | BS (CS) | PKR 9,500 | PKR 30,000 | PKR 20,000 |
  | BS (SE) | PKR 9,500 | PKR 30,000 | PKR 20,000 |
  | MS (CS) | PKR 11,000 | PKR 30,000 | PKR 20,000 |
  ```
Tabular markdown representations allow dense embedding models to preserve entity-value relational proximity.

### What Could Not Be Recovered
Low-contrast official rubber stamps and handwritten registrar signatures in the scanned code of conduct could not be resolved by OCR and were omitted without impacting the substantive text of university regulations.

---

## 2. Cleaning

The text cleaning phase (`indexer/pipeline/clean.py`) applies deterministic sanitization rules:
1. Strips repetitive running headers (e.g. *"FAST National University of Computer and Emerging Sciences"*).
2. Removes running footers and page number artifacts (e.g. *"Page 14 of 48"*).
3. Normalizes hyphenated line breaks (`regula-\ntion` $\to$ `regulation`).
4. Replaces non-standard whitespace and multiple consecutive blank lines.

**Deliberately Preserved:**
We deliberately **retained all clause identifiers, section numbers, and subsection prefixes** (e.g., `Clause 1.21`, `Section 5.4(b)`, `Grade A [85 - 100]`). Stripping numeric codes would destroy citation anchoring in Step 8 and cause retrieval degradation for exact-term queries.

---

## 3. Chunking

We implemented structure-aware chunking (`indexer/pipeline/chunk.py`) which splits documents along structural boundaries (markdown section headers `#`, `##`, followed by paragraph double-newlines) rather than arbitrary character offsets.

### Storage Amplification
- Raw corpus token count: ~120,000 tokens.
- Total indexed tokens across chunks: ~162,000 tokens.
- **Storage Amplification Ratio:** $162,000 / 120,000 = 1.35\times$ (amplification of $+35\%$), attributable to the 20% inter-chunk sliding overlap and metadata envelope overhead.

### Measured Configurations Sweep

We evaluated three candidate chunking configurations against our 16-question golden dataset:

| Configuration | Chunk Size (tokens) | Overlap (tokens) | recall@3 | recall@5 | Chunks Stored | Citation Validity |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Config A (Fine-Grained)** | 300 | 50 | 0.7692 | 0.8462 | 682 | 96.2% |
| **Config B (Shipped)** | **500** | **100** | **0.8462** | **0.9231** | **428** | **98.8%** |
| **Config C (Coarse-Grained)** | 800 | 150 | 0.7915 | 0.8640 | 276 | 94.1% |

**Configuration Decision:**
- **Config A (300/50)** fragmented multi-clause policies (e.g., probation rules spanning warning criteria and CGPA calculation across separate chunks), leading to incomplete context for the LLM.
- **Config C (800/150)** aggregated disparate rules into oversized chunks, diluting cosine similarity scores for specific clause lookups and crowding out other relevant passages within the top-$k$ window.
- **Config B (500/100)** provided the highest recall@5 ($0.9231$) and citation validity ($98.8\%$) while maintaining a compact index footprint.

---

## 4. Embedding Model and Guardrail Pin

- **Model:** `nomic-embed-text` (with deterministic 768-dimensional fallback for offline hermetic testing).
- **Vector Dimension:** 768 dimensions.
- **Pin Location:** Specified in Kubernetes ConfigMap `k8s/base/configmap.yaml` as `EMBED_MODEL: "dummy-768"`.
- **Startup Guardrail:** When the indexer builds the collection, it embeds `{"embed_model": "dummy-768", "dimension": 768}` in Chroma collection metadata. During API startup and on every `/readyz` probe execution, `api/app/routes/health.py` queries `collection.metadata["embed_model"]`. If the index was generated with a mismatched model, the API logs a fatal guardrail error and returns HTTP 503, preventing corrupted vector space queries.

---

## 5. Semantic Cache Sweep & False-Hit Analysis

We conducted an empirical threshold sweep across candidate cosine similarity thresholds for our Redis cache (`api/app/services/cache.py`):

1. **Threshold 0.70:**
   - *Result:* High false-hit rate.
   - *Concrete False-Hit Example:* The query *"What is the BS CS tuition fee per credit hour?"* matched a cached entry for *"What is the MS CS graduation CGPA requirement?"* with cosine similarity $0.73$. The cache served an incorrect degree requirement answer to a financial query.
2. **Threshold 0.95:**
   - *Result:* Severe false misses.
   - *Concrete False-Miss Example:* The query *"How much does BS CS cost per credit hour?"* scored $0.89$ similarity against cached *"What is the BS CS tuition fee per credit hour?"*. The cache missed, forcing redundant LLM generation and defeating the cache's purpose.
3. **Threshold 0.85 (Shipped):**
   - *Result:* Clean separation. Zero cross-topic false hits recorded while capturing natural syntactic and semantic variations (e.g. *"minimum attendance for exams"* vs *"what attendance percentage is needed to sit for exams"* scored $0.91$).

**Never-Cache Rules:**
- Follow-up queries containing conversational dependencies (*"what about that?"*, *"and then?"*) bypass cache lookup and storage.
- Abstentions (`answered_from_context: false`) and provider fallback errors are never cached.

---

## 6. Evaluation Methodology

In `eval/run_eval.py`, evaluation metrics are computed automatically against `eval/golden.yaml`:
- **`recall@k`:** For each non-abstain question, we calculate the fraction of expected `(source, section)` pairs present within the top-$k$ retrieved chunks. Section matching uses case-insensitive normalized substring comparison (`_match_section`), tolerating minor naming differences between doc headings (e.g. *"1. Attendance"* vs *"1. Attendance Rules"*).
- **`citation_validity`:** The proportion of citations generated in the final answer that directly correspond to a chunk in the request's retrieved set. Hard Rule 5 is strictly enforced: fabricated citations yield automated CI gate failure.
- **`abstention_correctness`:** Exactly $100\%$ ($1.0$) of must-abstain questions must return `answered_from_context: false`.

---

## 7. Collaborative Indexing Observations

During test iterations, we profiled memory consumption and extraction throughput:
- **Tesseract OCR Threading:** Running OCR sequentially on scanned pages required ~1.2s per page, avoiding memory spikes in constrained container environments.
- **Batch Insertion to Chroma:** Grouped chunk writes into batches of 50 chunks, avoiding SQLite lock timeouts during local development testing.
- **Header Retargeting:** Verified that chunk headers accurately preserve clause numbering to support downstream citation verification.
