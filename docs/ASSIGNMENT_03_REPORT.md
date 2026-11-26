# Assignment 03: RAGOps on RAHBAR

**CS4032 Software Construction and Development, Fall 2026**

| Member | Roll number |
| --- | --- |
| Alisha | ⟨roll no.⟩ |
| ⟨partner name⟩ | ⟨roll no.⟩ |

Repository: ⟨GitHub URL⟩ · Reference: Xu et al., *RAGOps: Operating and Managing Retrieval-Augmented Generation Pipelines* (2026).

> **How to fill this report.** Every ⟨…⟩ value comes from a file your own cluster run wrote
> (`eval/results/…`, `traces/…`). Run the steps in `k8s/README.md`, then `make a3-collect`,
> commit the files, and copy the numbers here with the file name next to each table. Delete this
> note before exporting to PDF (max 8 pages + screenshot appendix).

---

## 1. Figure 3 stages → our pipeline

| Figure 3 stage | What our pipeline does | File(s) | What is missing |
| --- | --- | --- | --- |
| Ingest | Discovers `.md/.pdf/.html/.txt` in the data lake (`/corpus` on a PVC), reads front matter, extracts text per page (OCR for image-only PDF pages), strips front matter and repeating headers. Batch B is ingested *together* with the corpus. | `indexer/pipeline/run.py`, `extract.py`, `clean.py` | No crawler: new documents are copied into the PVC by hand (`kubectl cp`). |
| Verify | Four checks per document, each pass/fail with a reason: quality (empty, garbled, failed extraction), completeness (source, title, version, page count, truncation), recency (newer version of the same title), uniqueness (SHA-256 exact, character-trigram Jaccard ≥ 0.88 near-duplicate). Failures are **moved** to `quarantine/`; a report file is written per run. | `indexer/pipeline/verify.py` | Near-duplicate uses n-grams, not embeddings (see 4e). No content-safety check (injection is caught at query time, see 4a). |
| Update data lake | Outdated versions are copied to `archive/` and kept with `superseded=true`; per-collection state file with document hashes and chunk ids; `--rollback` restores an archived version. | `indexer/pipeline/state.py`, `run.py` | Archive is a folder on the PVC, not versioned object storage. |
| Update retrieval sources | Only new/changed documents are chunked and embedded; a changed document's old chunks are deleted first; deleted documents' chunks are removed; superseded chunks are re-flagged by metadata; the semantic cache is flushed after any write. Collection metadata records `embed_model` and `index_version`. | `indexer/pipeline/store.py`, `chunk.py`, `embed.py` | No blue/green switch inside one collection: we use a second collection for the candidate. |
| Offline testing | Three levels (module/embedding, component/retrieval, end-to-end/answers), run twice for stochasticity, with a config-driven release gate. Runs as a Kubernetes Job. | `eval/run_eval_v2.py`, `eval/gate.py`, `k8s/eval-job*.yaml` | ⟨e.g. judge is a 3B model⟩ |
| Live testing | Every query writes one trace line (stdout + per-pod file on a PVC); `/metrics` for Prometheus; trace summary over a time window; trace-id tool rebuilds the prompt. | `api/app/services/answer.py`, `tracing.py`, `scripts/trace_tool.py`, `scripts/trace_metrics.py` | No online user feedback signal. |
| Check coverage | Live query log vs golden set with the pipeline's own embedding model; uncovered queries grouped by topic; non-zero exit below target. | `eval/coverage.py`, `eval/live_queries.jsonl` | Topic grouping is keyword-based. |

## 2. Table 2 qualities

| Quality | What we measure | Threshold (and why) | Evidence |
| --- | --- | --- | --- |
| Adaptability | chunks embedded on an unchanged corpus; chunks replaced on a change; coverage after adding golden questions | 0 chunks; only the changed doc's chunks; coverage ≥ 85% | Job log ⟨file/screenshot A⟩; coverage ⟨before⟩ → ⟨after⟩ (`coverage_*.json`) |
| Monitorability | top-1 similarity distribution, abstention rate, cache hit rate, p95 per stage, injections flagged | ⟨your alert levels⟩ | `trace_metrics.json` ⟨window⟩ |
| Observability | share of queries with a complete trace record (D1 fields) | 100% | `kubectl logs` + `traces/*.jsonl` |
| Traceability | rebuild the exact prompt and context from a trace id | exact text, from the trace only | `trace_tool.py <id>` screenshot D |
| Reliability | abstention correctness, citation validity, rollout with `maxUnavailable: 0`, rollback | 100%, ≥ 95%, no failed requests during rollout | eval files; rollout screenshots F |

## 3. Results

### 3.1 Part A (files: `verification_batch_a_*.json`, `verification_batch_b_*.json`, Job logs)

| Run | Scanned | Passed | Quarantined | Chunks embedded | Unchanged docs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Batch A, first run | ⟨⟩ | ⟨⟩ | ⟨⟩ | ⟨⟩ | 0 |
| Batch A, unchanged re-run | ⟨⟩ | ⟨⟩ | ⟨⟩ | **0** | ⟨⟩ |
| Batch A + Batch B | ⟨⟩ | ⟨⟩ | ⟨⟩ | ⟨⟩ | ⟨⟩ |

Batch B planted problems and the check that caught them (fill from the report file):

| Document | Planted problem | Check that failed | Outcome |
| --- | --- | --- | --- |
| doc02, doc03 | exact duplicates of doc01 | uniqueness (SHA-256) | quarantined |
| doc04 | near-duplicate of doc01 | uniqueness (n-gram ⟨similarity⟩) | quarantined |
| doc05 | garbled + truncated | quality, completeness | quarantined |
| doc06 | no front matter | completeness | quarantined |
| doc10 | empty | quality, completeness | quarantined |
| doc07 | newer (2027) version of academic rules | recency on **academic-rules-2026.md** | 2026 archived, `superseded=true` |
| doc08 | injected instruction | none at ingest | indexed; redacted at query time (D4) |

Orphan proof: count by document id before/after deleting ⟨doc⟩: ⟨n⟩ → 0. Rollback: ⟨what you showed⟩.

### 3.2 Parts B and C (files: `eval_baseline_*.json/.md`, `eval_candidate_*`, `eval_failing_candidate_*`)

Change under test (C1): **chunk size 250 / overlap 40** (candidate, collection `rahbar-candidate`)
vs **400 / 50** (baseline, collection `rahbar`). Golden set: 28 questions (⟨n⟩ answerable, 6
unanswerable, 6 hard; 8 human-written; ⟨n⟩ synthetic reviewed by hand).

| Level | Metric | Baseline | Candidate | Failing candidate | Gate |
| --- | --- | ---: | ---: | ---: | --- |
| Module | paraphrase / unrelated mean cosine, gap | ⟨⟩ | ⟨⟩ | ⟨⟩ | — |
| Module | ms per chunk; determinism max diff | ⟨⟩ | ⟨⟩ | ⟨⟩ | — |
| Component | recall@3 / recall@5 | ⟨⟩ | ⟨⟩ | ⟨⟩ | drop ≤ 0.02 |
| Component | MRR / nDCG@5 | ⟨⟩ | ⟨⟩ | ⟨⟩ | — |
| Component | recall@5 with typos (Δ) | ⟨⟩ | ⟨⟩ | ⟨⟩ | — |
| End-to-end | faithfulness (LLM judge) | ⟨⟩ | ⟨⟩ | ⟨⟩ | ≥ ⟨⟩ |
| End-to-end | judge vs our labels (n ≥ 8) | ⟨⟩ | | | — |
| End-to-end | abstention correctness | ⟨⟩ | ⟨⟩ | ⟨⟩ | 100%, ≥ baseline |
| End-to-end | citation validity | ⟨⟩ | ⟨⟩ | ⟨⟩ | ≥ 95% |
| End-to-end | latency p50 / p95 (ms) | ⟨⟩ | ⟨⟩ | ⟨⟩ | p95 ≤ ⟨⟩ |
| Stochasticity | largest run-1 vs run-2 difference (metric) | ⟨⟩ | ⟨⟩ | ⟨⟩ | — |
| | **Gate** | ⟨⟩ | ⟨PASS/FAIL⟩ | **FAIL** | |

Threshold justification: ⟨copy from eval/gate_config.yaml comments, with your measured p95⟩.
Remaining variation between the two seeded runs: ⟨latency jitter / LLM sampling / …⟩.

**Release decision:** ⟨RELEASE or REJECT the 250/40 candidate, with the metric that decided it⟩.
The failing candidate (`RELEVANCE_FLOOR=0.95`) failed ⟨which checks⟩ and was never rolled out.

### 3.3 Part D

D3 over ⟨window⟩ (`trace_metrics.json`): ⟨queries⟩ queries; top-1 similarity median ⟨⟩
(histogram ⟨⟩); abstention rate ⟨⟩; cache hit rate ⟨⟩; p95 retrieval / generation / total ⟨⟩ ms;
injection flags ⟨⟩.

D4: trace ⟨id⟩ shows `action_taken: redacted_injected_text` for chunk ⟨doc08 chunk id⟩.

**D5. Root cause from one trace only** (5 lines). Question ⟨id⟩, trace ⟨trace_id⟩:
1. ⟨what the answer was / that it abstained⟩
2. ⟨top retrieved chunk ids, document versions and scores from the trace⟩
3. ⟨filter applied / superseded flag / relevance floor vs score⟩
4. ⟨the cause: which chunk, document version or filter⟩
5. ⟨the fix⟩

### 3.4 Part E (files: `coverage_golden_*.json`, `coverage_golden_extended_*.json`)

Threshold ⟨t⟩, chosen from `--calibrate`: in-domain best-match similarity ⟨min/median⟩ vs
out-of-domain ⟨max/median⟩. Coverage ⟨before⟩ (exit 1) → ⟨after⟩ with 6+ new questions.
Uncovered topics: ⟨list⟩.

E3 data drift (`eval_after_batch_b_*.json` vs baseline): ⟨what changed and why — e.g. the 2026
attendance questions now retrieve the 2027 rules because 2026 is superseded; Batch-B questions
q26–q28 become answerable⟩.

### 3.5 Part F

Resources from `kubectl top pods` (F5): ⟨table of measured values → requests/limits⟩.
Gate result before/after the ConfigMap edit: ⟨PASS → FAIL⟩. After `rollout undo`, trace ⟨id⟩
shows `collection_name: rahbar`, `embedding_version: nomic-embed-text|chunk400-ov50`.

## 4. Short answers

**(a)** Uniqueness caught the most Batch B problems (doc02, doc03 exact; doc04 near). Not caught
at ingestion: the injected instruction in doc08 (clean text, valid metadata, unique). It is caught
at query time: the retrieval rail redacts the injected line before generation and logs it in the
trace. An ingestion-time content check (the same patterns, or a classifier such as Llama Guard)
would quarantine it earlier.

**(b)** ⟨From your numbers: e.g. module-level gap looked fine for both chunkings, but recall@5 /
faithfulness differed; or the failing candidate had identical module metrics and collapsed
end-to-end.⟩

**(c)** We archive: old versions stay in `archive/` and in the index with `superseded=true`, so
retrieval excludes them by a metadata filter but questions that explicitly ask about the old
version still work, old traces can still be explained, and `--rollback` can restore one. Deleting
breaks all three: historical questions abstain, traces point at chunks that no longer exist, and
rollback needs a re-ingest of a file you may no longer have.

**(d)** ⟨Your choice with evidence, e.g. faithfulness: the judge is a 3B model and agreed with our
labels on ⟨x⟩ of ⟨n⟩.⟩

**(e)** ⟨Hardest idea, and what you changed: e.g. near-duplicate detection by n-grams instead of
embeddings; the paper's continuous coverage check done as a script over a query log.⟩

**(f)** See the object/responsibility table in `k8s/README.md`: the indexer Jobs/CronJob are the
only Chroma writers, enforced by the `allow-chroma` NetworkPolicy (only pods labelled `indexer`
or API pods can connect) and `concurrencyPolicy: Forbid`; the PVCs hold the data lake and the
results/traces; the gate thresholds are a ConfigMap; the eval Job's status is the gate decision;
the live Deployment's rolling update with `maxUnavailable: 0` is the controlled release.

## 5. Contribution and AI statement

| Member | What they did |
| --- | --- |
| Alisha | ⟨⟩ |
| ⟨partner⟩ | ⟨⟩ |

AI statement (2–3 lines): ⟨name the tools, what they generated, and what you checked or changed.
Example: "An AI assistant drafted parts of the evaluation and Kubernetes code and reviewed the
repository against the brief; we ran every step on our own cluster, and all numbers come from
the committed result files."⟩

## Appendix: screenshots

A (Batch B verification report; unchanged re-run with 0 chunks) · B (evaluation summary) ·
C (gate FAIL, gate PASS) · D (one full trace record; trace tool rebuilding the prompt) ·
E (coverage before and after) · F (`kubectl get all,pvc,configmap,cronjob,job -n rahbar`;
logs of a completed ingestion Job and evaluation Job; gate change after the ConfigMap edit;
`rollout status`, `rollout undo`, trace with the previous collection and embedding version).
