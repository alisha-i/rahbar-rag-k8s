# RAHBAR · Grounded RAG on Kubernetes

[![CI](https://github.com/alisha-i/rahbar-rag-k8s/actions/workflows/ci.yml/badge.svg)](https://github.com/alisha-i/rahbar-rag-k8s/actions/workflows/ci.yml)
[![CD](https://github.com/alisha-i/rahbar-rag-k8s/actions/workflows/cd.yml/badge.svg)](https://github.com/alisha-i/rahbar-rag-k8s/actions/workflows/cd.yml)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.28%2B-blue?style=flat-square&logo=kubernetes)](https://kubernetes.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-1.0.12-FF6F00?style=flat-square)](https://www.trychroma.com/)
[![Redis](https://img.shields.io/badge/Redis-7.4--alpine-DC382D?style=flat-square&logo=redis)](https://redis.io/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

---

## 📌 What is RAHBAR?

**RAHBAR** (Urdu for *“Guide”* or *“Pathfinder”*) is a cloud-native, enterprise-grade **Grounded Retrieval-Augmented Generation (RAG)** service engineered to run reliably on **Kubernetes**. 

Built for university academic policies, grading regulations, and handbooks (specifically FAST-NUCES), RAHBAR solves the fundamental problem of Large Language Model (LLM) hallucinations. It guarantees that **every answer is strictly grounded in verified source documents**, complete with pinpoint `(source, section, page)` citations, while enforcing **honest abstention** whenever relevant context does not exist.

### 🌟 Core Highlights at a Glance
| Feature | How RAHBAR Implements It |
| :--- | :--- |
| **Grounding** | Answers only from retrieved chunks above a relevance floor; the prompt delimits evidence as data. |
| **Deterministic Citations** | Every response cites exact `(source, section, page)`. Citations not present in retrieved context are stripped. |
| **Honest Abstention** | If a policy does not cover the question or if a topic is absent, returns `answered_from_context: false` instead of guessing. |
| **Semantic Caching** | Redis cache keyed by question embedding (cosine ≥ CACHE_THRESHOLD), namespaced per collection, flushed on every re-index. |
| **Decoupled Architecture** | **Single-Writer Indexer Job** writes to vector storage; stateless **API Deployments (3 to 8 pods)** are strictly read-only. |
| **Kubernetes Resilience** | ChromaDB StatefulSet with 1Gi PersistentVolumeClaim, Calico deny-by-default NetworkPolicies, and HPA v2 autoscaling. |
| **Automated CI/CD** | Linting, unit tests with 60% coverage gate, Kubeconform manifest validation, Trivy container security scanning, Kind e2e regression gate, and SHA-pinned CD deployment. |

---

## 🚀 Assignment 03 (RAGOps): the command for each part (A to F)

Everything below runs **on the minikube cluster** (the demo must be Kubernetes, not Docker).
The full step-by-step walkthrough, with the screenshot list, is in [`k8s/README.md`](k8s/README.md).
Prerequisites: Ollama on the laptop (`OLLAMA_HOST=0.0.0.0 ollama serve`, `ollama pull llama3.2:3b`,
`ollama pull nomic-embed-text`), then:

```bash
make cluster          # minikube with Calico + ingress + metrics-server
make images           # builds rahbar-api, rahbar-indexer, rahbar-eval, rahbar-ui and loads them
kubectl apply -k k8s/overlays/dev      # namespace, PVCs, ConfigMaps, Chroma, Redis, API, UI, indexer Job, CronJob
```

| Part | Command (Makefile target → what it runs) | Output |
| --- | --- | --- |
| **A1/A2** ingest + verify | `make a3-index` (twice: the 2nd run must log `embedded_chunks: 0`) | Job logs + `verification_batch_a_<ts>.json` on the results PVC |
| **A** Batch B | `make a3-batch-b` → `k8s/indexer-batch-b-job.yaml` | per-document pass/fail for quality, completeness, recency, uniqueness; failures moved to `/corpus/quarantine` |
| **A2** orphan proof | `kubectl exec -n rahbar job/indexer -- python -m pipeline.run --count-by-doc` (or run a Job with `--count-by-doc`) | chunk count per document id |
| **A2** rollback | `--rollback academic-rules-2026.md --supersede doc07_academic_rules_2027.md` (see k8s/README §A) | archived version served again |
| **B** three-level suite | `make a3-eval` → `k8s/eval-job.yaml` (Job) — or `make a3-eval-local` through a port-forward | `eval_baseline_<ts>.json` + `.md` summary table |
| **C1/C2** change + gate | `kubectl apply -f k8s/candidate-indexer-job.yaml -f k8s/candidate-deployment.yaml -f k8s/eval-job-candidate.yaml` | gate PASS/FAIL, Job exit status; `python eval/gate.py --result ...` re-applies it |
| **D** traces | every `/api/ask` writes one JSON line to stdout and `/data/traces/<pod>.jsonl`; `python scripts/trace_tool.py <trace_id> --file traces/`; `python scripts/trace_metrics.py --file traces/ --last-minutes 60` | prompt rebuilt from a trace id; metrics over a window |
| **E** coverage | `make a3-coverage` (`eval/coverage.py`, exits 1 below target) | coverage before/after `golden_extended.jsonl` |
| **F** cluster ops | `k8s/README.md` §F: rollout status / undo, ConfigMap gate flip, `kubectl top` | screenshots for the appendix |
| collect results | `make a3-collect` (copies `/data/results` and `/data/traces` from the PVC into the repo) | commit `eval/results/` and `traces/` |

> ⚠️ The numbers in `docs/ASSIGNMENT_03_REPORT.md` must come from these runs. Stub mode
> (`RETRIEVER=stub`) is for unit tests and `make dev` only; `eval/run_eval_v2.py --offline-stub`
> refuses to write result files.

---

## 🏗️ High-Level System Architecture

RAHBAR is strictly decoupled into distinct tiers within the dedicated `rahbar` Kubernetes namespace:

```mermaid
flowchart TD
    subgraph ClientTier ["1. Client & User Tier"]
        Browser["User Web Browser (Chat UI)"]
        CLI["API Client / Automated Evaluator"]
    end

    subgraph IngressTier ["2. Ingress & Routing Tier"]
        Ingress["NGINX Ingress Controller (Host: rahbar.local)"]
    end

    subgraph ServingTier ["3. Serving Tier (Namespace: rahbar)"]
        UI["UI Pod (nginx-unprivileged: 1 Replica)"]
        API["FastAPI Deployment (3 to 8 Replicas)"]
        HPA["HPA v2 (CPU Target: 70%, Min: 3, Max: 8)"]
    end

    subgraph DataTier ["4. Persistent Data Tier (ClusterIP)"]
        Chroma["ChromaDB StatefulSet (chroma-0: 1Gi PVC)"]
        Redis["Redis Semantic Cache (1Gi PVC)"]
    end

    subgraph IndexingTier ["5. Single-Writer Indexing Tier"]
        IndexerJob["Indexer Job / reindex CronJob"]
        Corpus[("Corpus Repository (PDFs, Markdown, Tables)")]
    end

    Browser -->|HTTP Port 80| Ingress
    CLI -->|HTTP Port 80 /api| Ingress
    Ingress -->|/| UI
    Ingress -->|/api| API
    HPA -.->|Autoscales Replicas| API

    API -->|1. Fast Semantic Lookup| Redis
    API -->|2. Vector Search (filters superseded=false)| Chroma
    API -->|3. Constrained JSON Synthesis| LLM["Ollama / Hosted LLM"]

    IndexerJob -->|Extract, OCR & Chunk| Corpus
    IndexerJob -->|Exclusive Write| Chroma
    IndexerJob -->|Invalidate Stale Cache| Redis
```

### Key Architectural Decisions (ADRs)
1. **[ADR 0001: Indexer as a Kubernetes Job](docs/adr/0001-indexer-as-job.md)** — Indexing is a batch task, not an API startup event. Running indexing on API startup causes write races across 3 replicas against ChromaDB. The indexer runs as a decoupled Kubernetes `Job`.
2. **[ADR 0002: Structure-Aware Chunking Strategy](docs/adr/0002-chunking-strategy.md)** — Documents are parsed preserving headings, clause numbers, and tables with 400-token chunks and 50-token overlap, ensuring citations capture complete policy context.
3. **[ADR 0003: Redis Semantic Caching](docs/adr/0003-cache-store.md)** — High-frequency questions query Redis semantic vectors first ($\text{cosine similarity} \ge 0.85$). Abstentions and conversational follow-ups are never cached. Re-indexing flushes stale entries.
4. **[ADR 0004: LLM Provider Abstraction & Data Governance](docs/adr/0004-llm-provider-and-data-governance.md)** — Clean provider interface (`Stub`, `Ollama`, `Hosted`) ensures offline CI evaluation without leaking institutional document contents or requiring external API keys.

---

## 📂 Repository Tour & Project Structure

```text
rahbar-rag-k8s/
├── api/                              # Serving Tier: FastAPI application
│   ├── app/
│   │   ├── main.py                   # FastAPI initialization & lifespan model verification
│   │   ├── config.py                 # Pydantic Settings loaded from ConfigMap / Secret
│   │   ├── schemas.py                # Pydantic contracts: AskRequest, Answer, Citation
│   │   ├── routes/                   # HTTP Routes: /ask, /healthz, /readyz, /metrics
│   │   ├── services/                 # Business logic: orchestrator & semantic cache
│   │   ├── retrieval/                # Vector query engine with version filtering
│   │   └── providers/llm/            # LLM interfaces: Stub, Ollama, Hosted
│   ├── tests/                        # Unit tests & coverage suite (minimum 60% gate)
│   └── Dockerfile                    # Multi-stage non-root container (python:3.12-slim)
├── indexer/                          # Single-Writer Indexing Tier
│   ├── pipeline/                     # 5-Stage Pipeline: extract, clean, chunk, embed, store
│   │   ├── extract.py                # PDF & OCR text extraction (pdfplumber, tesseract)
│   │   ├── clean.py                  # Header/footer stripping while keeping headings & clauses
│   │   ├── chunk.py                  # Structure-aware windowing with token overlap
│   │   ├── embed.py                  # Embedding generation with dimension verification
│   │   └── store.py                  # ChromaDB exclusive collection ingestion & cache invalidation
│   ├── tests/                        # Indexer test suite
│   └── Dockerfile                    # Pinned non-root container with tesseract-ocr & poppler
├── ui/                               # Chat Interface
│   ├── index.html                    # Responsive, non-root web chat frontend
│   ├── nginx.conf                    # Hardened nginx configuration
│   └── Dockerfile                    # nginxinc/nginx-unprivileged:1.27-alpine
├── k8s/                              # Cloud-Native Kubernetes Manifests
│   ├── base/                         # Declarative K8s resources (namespace, services, deployments)
│   │   ├── namespace.yaml            # Dedicated 'rahbar' namespace
│   │   ├── configmap.yaml            # Shared cluster configuration
│   │   ├── chroma-statefulset.yaml   # Persistent vector database StatefulSet (1Gi PVC)
│   │   ├── redis-deployment.yaml      # Semantic cache deployment with PVC
│   │   ├── api-deployment.yaml       # 3-replica stateless API Deployment
│   │   ├── ui-deployment.yaml        # Web chat frontend Deployment
│   │   ├── indexer-job.yaml          # Single-writer batch Job
│   │   ├── reindex-cronjob.yaml      # Scheduled recurring reindexing
│   │   ├── networkpolicy.yaml        # Deny-by-default Calico security policies
│   │   └── hpa.yaml                  # HorizontalPodAutoscaler (CPU 70%, 3-8 replicas)
│   └── overlays/                     # Kustomize environment overlays
│       ├── dev/                      # Development overlay
│       └── prod/                     # Production overlay (SHA-pinned tags)
├── corpus/                           # Document Corpus Management
│   ├── fetch.py                      # Deterministic document fetch script
│   └── MANIFEST.md                   # SHA-256 checksums and source URLs
├── eval/                             # Evaluation & Regression Gate
│   ├── golden.yaml                   # 16 curated policy evaluation queries
│   ├── run_eval.py                   # Automated test runner for recall@k & citations
│   ├── baseline.json                 # Baseline retrieval scores
│   ├── results.json                  # Measured evaluation numbers
│   └── fixtures/                     # Isolated fixture corpus for CI
├── docs/                             # Engineering Documentation
│   ├── CORPUS.md                     # Analysis of 7 corpus properties
│   ├── PIPELINE.md                   # Chunking parameter experiments & amplification ratio
│   ├── FAILURE-ANALYSIS.md           # Failure diagnosis for 6 system stages
│   ├── ENGINEERING-NOTES.md          # Answers to core systems & HPA design questions
│   ├── AI-USAGE.md                   # AI attribution and engineering modifications
│   ├── adr/                          # Architectural Decision Records (0001 to 0004)
│   └── evidence/                     # Screenshots & command outputs (HPA, endpoints, top)
├── .github/workflows/                # CI/CD Automation Pipelines
│   ├── ci.yml                        # Lint, test, kubeconform, trivy, Kind e2e evaluation
│   └── cd.yml                        # Build, push to Docker Hub, Kind deploy, rollout smoke test
└── scripts/
    └── check_submission.py           # Automated rubric submission verification lint
```

---

## 📡 API Endpoints Specification

All API endpoints are served under `/api` (or at the root for health probes):

| Method | Endpoint | Purpose | Request Example | Response Highlights |
| :---: | :---: | :--- | :--- | :--- |
| POST | `/api/ask` | Primary Question Answering | `{"question": "What CGPA puts a student on academic warning?"}` | Returns grounded answer, citations list, `answered_from_context`, confidence, and retrieval scores. |
| GET | `/healthz` | Kubernetes Liveness Probe | None | Returns `{"status": "alive"}` instantly without touching data tier dependencies. |
| GET | `/readyz` | Kubernetes Readiness Probe | None | Verifies ChromaDB connectivity, non-empty collection, embedding model pin, and Redis. Returns 503 if not ready. |
| GET | `/metrics` | Prometheus Metrics Target | None | Exposes request counters, latency histograms, semantic cache hits/misses, and abstention counts. |
| GET | `/` | Web Chat UI | None | Serves the interactive chat frontend from the UI container. |

### Example API Query & Response
```bash
curl -X POST http://rahbar.local/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the minimum attendance percentage required to sit in final exams?"}'
```

```json
{
  "request_id": "8f3b14a2c9...",
  "question": "What is the minimum attendance percentage required to sit in final exams?",
  "answer": "According to the FAST-NUCES Academic Regulations, students must maintain a minimum attendance of 80% in each registered course to be eligible to sit for the final examinations.",
  "citations": [
    {
      "source": "academic-regulations-2026.pdf",
      "section": "3.1 Attendance Requirements",
      "page": 12
    }
  ],
  "answered_from_context": true,
  "confidence": "high",
  "retrieved": [
    {
      "chunk_id": "acad-reg-2026-c042",
      "score": 0.91,
      "source": "academic-regulations-2026.pdf"
    }
  ],
  "cache_hit": false
}
```

---

## 🚀 Quickstart: Running on Minikube with Calico

### 1. Start Local Cluster
Start Minikube with Calico CNI (required for NetworkPolicy enforcement) and enable necessary add-ons:
```bash
minikube start --driver=docker --cni=calico --memory=6144 --cpus=4
minikube addons enable metrics-server
minikube addons enable ingress
```

### 2. Exact apply order (from a clean clone)
```bash
# Ollama on the laptop, reachable from minikube
OLLAMA_HOST=0.0.0.0 ollama serve &          # Windows: set OLLAMA_HOST=0.0.0.0 in the environment, restart Ollama
ollama pull llama3.2:3b && ollama pull nomic-embed-text

# Images (indexer and eval build from the repo root)
make images                                  # or the four `docker build` lines in the Makefile

# Everything in one go: kustomize orders Namespace, PVCs, ConfigMaps, Services, StatefulSet,
# Deployments, the indexer Job and the CronJob correctly
kubectl apply -k k8s/overlays/dev

# Optional: only if you use LLM_PROVIDER=hosted
make secret LLM_API_KEY=...

# Wait: data tier, then the indexer Job (the only Chroma writer), then the API (ready only
# once the collection exists, is non-empty and was built with the same EMBED_MODEL)
kubectl rollout status statefulset/chroma -n rahbar --timeout=180s
kubectl rollout status deployment/redis  -n rahbar --timeout=120s
kubectl wait --for=condition=complete job/indexer -n rahbar --timeout=600s
kubectl rollout status deployment/api    -n rahbar --timeout=300s
kubectl rollout status deployment/ui     -n rahbar --timeout=120s

make ask Q="What CGPA puts a student on academic warning?"
```

### 3. Verification & Evaluation Gate
Run the automated mechanical checker to verify compliance:
```bash
python scripts/check_submission.py
```
Score retrieval quality against the A2 golden set (through the Ingress, `make eval`):
```bash
python eval/run_eval.py --api http://$(minikube ip) --host rahbar.local --golden eval/golden.yaml --baseline eval/baseline.json
```

---

## 🔒 Security & Cloud-Native Best Practices

- **Zero-Privilege Containers:** Every container runs as a dedicated non-root user (`USER 10001`) with read-only root filesystems (`readOnlyRootFilesystem: true`), all Linux capabilities dropped (`drop: [ALL]`), and privilege escalation explicitly disallowed.
- **Micro-Segmentation:** Network policies enforce a **deny-by-default** stance across all ingress and egress in the `rahbar` namespace. Only necessary traffic is allowed: UI $\rightarrow$ API (:8000), API $\rightarrow$ Chroma (:8000), API $\rightarrow$ Redis (:6379), and kube-dns (:53). Direct public exposure of ChromaDB and Redis is prohibited.
- **Embedding Pin Guardrail:** If an index is written with an embedding model differing from the configured `EMBED_MODEL`, the `/readyz` probe immediately returns `503 Service Unavailable`, preventing the serving of corrupted retrieval vectors.

---

## 👥 Authors & Academic Attribution
- **Student Name:** Alisha Iqbal (`alisha-i`) & Musfirah Hamid (`MUSFIRAH828`)
- **Institution:** FAST National University of Computer & Emerging Sciences (NUCES)
- **Course:** CS4032 - Software Construction & Development 
- **AI Attribution:** Detailed transparency and tool usage documented in [docs/AI-USAGE.md](docs/AI-USAGE.md).
