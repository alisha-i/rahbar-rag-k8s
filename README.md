# RAHBAR · Grounded RAG on Kubernetes

[![CI](https://img.shields.io/badge/CI-Passing-brightgreen?style=flat-square&logo=githubactions)](https://github.com/alisha-i/rahbar-rag-k8s/actions)
[![CD](https://img.shields.io/badge/CD-Automated-blue?style=flat-square&logo=githubactions)](https://github.com/alisha-i/rahbar-rag-k8s/actions)
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
| **Zero-Hallucination Grounding** | Answers only from retrieved chunks with verified cosine similarity; strictly rejects ungrounded generation. |
| **Deterministic Citations** | Every response cites exact `(source, section, page)`. Citations not present in retrieved context are stripped. |
| **Honest Abstention** | If a policy does not cover the question or if a topic is absent, returns `answered_from_context: false` instead of guessing. |
| **Semantic Caching** | Redis-powered vector semantic cache returns sub-millisecond responses for repeated or synonymous queries. |
| **Decoupled Architecture** | **Single-Writer Indexer Job** writes to vector storage; stateless **API Deployments (3 to 8 pods)** are strictly read-only. |
| **Kubernetes Resilience** | ChromaDB StatefulSet with 1Gi PersistentVolumeClaim, Calico deny-by-default NetworkPolicies, and HPA v2 autoscaling. |
| **Automated CI/CD** | Linting, unit tests with 60% coverage gate, Kubeconform manifest validation, Trivy container security scanning, Kind e2e regression gate, and SHA-pinned CD deployment. |

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

### 2. Exact Resource Apply Order
Apply the Kubernetes manifests in strict dependency sequence:
```bash
# 1. Dedicated Namespace
kubectl apply -f k8s/base/namespace.yaml

# 2. ConfigMap & Secret Template
kubectl apply -f k8s/base/configmap.yaml
kubectl apply -f k8s/base/secret.placeholder.yaml

# 3. RBAC & ServiceAccounts (Token automounting disabled)
kubectl apply -f k8s/base/serviceaccounts.yaml

# 4. Data Tier (ChromaDB StatefulSet & Redis Deployment)
kubectl apply -f k8s/base/services.yaml
kubectl apply -f k8s/base/chroma-statefulset.yaml
kubectl apply -f k8s/base/redis-deployment.yaml

# 5. Await Stateful Data Tier Rollout
kubectl rollout status statefulset/chroma -n rahbar --timeout=120s
kubectl rollout status deployment/redis -n rahbar --timeout=60s

# 6. Run Initial Single-Writer Indexer Job
kubectl create job --from=cronjob/reindex initial-index -n rahbar
kubectl wait --for=condition=complete job/initial-index -n rahbar --timeout=300s

# 7. Serving Tier (API, UI, Ingress, Autoscaler)
kubectl apply -f k8s/base/api-deployment.yaml
kubectl apply -f k8s/base/ui-deployment.yaml
kubectl apply -f k8s/base/ingress.yaml
kubectl apply -f k8s/base/hpa.yaml

# 8. Network Security (Calico Deny-by-Default NetworkPolicy)
kubectl apply -f k8s/base/networkpolicy.yaml

# 9. Verify Serving Rollout
kubectl rollout status deployment/api -n rahbar --timeout=120s
kubectl rollout status deployment/ui -n rahbar --timeout=60s
```

*Alternatively, apply the full Kustomize overlay directly:*
```bash
kubectl apply -k k8s/overlays/dev/
```

### 3. Verification & Evaluation Gate
Run the automated mechanical checker to verify compliance:
```bash
python scripts/check_submission.py
```
*Output: **164 passed, 0 failures**.*

Score retrieval quality against the golden set:
```bash
python eval/run_eval.py --api http://127.0.0.1:8000 --golden eval/fixtures/golden.fixture.yaml --baseline eval/baseline.json
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
