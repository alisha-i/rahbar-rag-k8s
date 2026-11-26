# Assignment 03 — Screenshot Appendix Guide (Submission Checklist)

Every required terminal screenshot from the assignment submission instructions is listed below, with the exact terminal command to capture it uncropped.

---

### Screenshot A: Data Verification & Incremental Update
* **Command 1 (Verification Report for Batch B):**
  ```powershell
  python -m indexer.pipeline.run --batch-b
  ```
  *What must be visible:* The terminal logging quarantined documents (exact dups, near dup, corrupt file, missing metadata, empty file) and `scanned: 10, passed: 4, quarantined: 6`.
* **Command 2 (Unchanged Corpus 0 Chunks):**
  ```powershell
  python -m indexer.pipeline.run --batch-b
  ```
  *What must be visible:* `unchanged corpus detected; embedded zero chunks` and `embedded_chunks: 0, unchanged_docs: 4`.

---

### Screenshot B: Evaluation Summary with All Three Levels
* **Command:**
  ```powershell
  python eval/run_eval_v2.py --mode baseline
  ```
  *What must be visible:* The complete summary table showing:
  - Level 1: Module Level (Paraphrase vs Unrelated Cosine Gap = 0.2576, Determinism Max Diff = 0.00000000)
  - Level 2: Component Level (Recall@3 = 1.0, Recall@5 = 1.0, MRR = 0.9773, nDCG@5 = 1.0, Noise Test Typos)
  - Level 3: End-to-End Level (Faithfulness = 0.9455, Abstention Correctness = 1.0, Citation Validity = 1.0, Latencies p50/p95)
  - Stochasticity Seed Variation Deltas = 0.000000.

---

### Screenshot C: Release Gate Failing & Passing
* **Command 1 (Release Gate Passing):**
  ```powershell
  python eval/gate.py --result eval/results/latest_candidate.json
  ```
  *What must be visible:* `GATE RESULT: [PASS] — Candidate satisfies all release gates.` (Exit code 0).
* **Command 2 (Release Gate Failing):**
  ```powershell
  python eval/gate.py --result eval/results/latest_failing_candidate.json
  ```
  *What must be visible:* `GATE RESULT: [FAIL] — Candidate rejected by release gate.` with listed violations (Recall drop, faithfulness drop).

---

### Screenshot D: Trace Record & Reconstructed Prompt
* **Command 1 (Full Trace Record):**
  ```powershell
  Get-Content traces/query_traces.jsonl -Tail 1
  ```
  *What must be visible:* Full single-line JSON trace showing trace ID, models, retrieved docs, prompt hash, citations, guardrail actions, and latencies.
* **Command 2 (Trace Tool Rebuilding Prompt):**
  ```powershell
  python scripts/trace_tool.py --latest
  ```
  *What must be visible:* The formatted output of `scripts/trace_tool.py` showing exact retrieved context chunks, reconstructed prompt, guardrail decision, and generated response.

---

### Screenshot E: Query Coverage Output (Before and After Gap Closure)
* **Command 1 (Before Adding Questions — Gap Detected):**
  ```powershell
  python eval/coverage.py --golden eval/golden.jsonl
  ```
  *What must be visible:* `COVERAGE VERDICT: [GAP DETECTED] — Coverage 77.5% < 85.0%` with list of uncovered topics (Transport, Hostel, Cricket, Cafeteria).
* **Command 2 (After Adding Questions — Gap Closed):**
  ```powershell
  python eval/coverage.py --golden eval/golden_extended.jsonl
  ```
  *What must be visible:* `COVERAGE VERDICT: [PASS] — Coverage is 87.5% >= 85.0%. No gaps detected.`

---

### Screenshot F: Kubernetes Cluster Operations
* **Command 1 (All Cluster Resources & PVCs):**
  ```powershell
  kubectl get all,pvc,configmap,cronjob,job -n rahbar
  ```
  *What must be visible:* Running API pods, Chroma StatefulSet (1/1), Redis, UI, PVCs (`corpus-pvc`, `eval-results-pvc`, `chroma-data`), and ConfigMaps.
* **Command 2 (Completed Ingestion & Evaluation Job Logs):**
  ```powershell
  kubectl logs -l app.kubernetes.io/name=indexer -n rahbar
  kubectl logs -l app.kubernetes.io/name=eval-job -n rahbar
  ```
* **Command 3 (Gate Result Changing via ConfigMap Edit without Rebuilding Image):**
  ```powershell
  kubectl describe configmap rahbar-gate-thresholds -n rahbar
  ```
* **Command 4 (Zero-Downtime Rollout & Rollback Undo):**
  ```powershell
  kubectl rollout status deployment/api -n rahbar
  kubectl rollout undo deployment/api -n rahbar
  kubectl rollout status deployment/api -n rahbar
  ```
