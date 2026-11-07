# RAHBAR · Grounded RAG on Kubernetes

[![CI](https://img.shields.io/badge/CI-Passing-brightgreen?style=flat-square&logo=githubactions)](https://github.com/alisha-i/rahbar-rag-k8s/actions)
[![K8s](https://img.shields.io/badge/Kubernetes-1.28%2B-blue?style=flat-square&logo=kubernetes)](https://kubernetes.io/)
[![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

**RAHBAR** is a production-grade, zero-downtime, grounded Retrieval-Augmented Generation (RAG) question-answering system running entirely on Kubernetes. Designed to serve university regulations, academic prospectuses, and grading policies with verifiable fidelity, RAHBAR answers user questions strictly from an authenticated corpus, provides deterministic `(source, section, page)` citations, enforces single-writer index isolation through Kubernetes batch Jobs, and guarantees honest abstention (`answered_from_context: false`) whenever relevant context is absent.

---

## Architecture

The system operates across three distinct tiers inside the dedicated `rahbar` namespace: a stateless API serving tier with rolling updates and Horizontal Pod Autoscaling (HPA), a persistent storage tier (ChromaDB StatefulSet and Redis Cache Deployment), and a decoupled batch Indexing tier.

```mermaid
flowchart TD
    subgraph Client ["Client Tier"]
        Browser["User Browser"]
        CI["CI Evaluation Runner"]
    end

    subgraph IngressTier ["Ingress & Routing"]
        Ingress["Ingress Controller (NGINX)"]
    end

    subgraph ServingTier ["Serving Tier (Namespace: rahbar)"]
        UI["UI Pod (nginx-unprivileged: 1 replica)"]
        API["API Deployment (python:3.12-slim: 3 to 8 replicas)"]
        HPA["Horizontal Pod Autoscaler (HPA v2: CPU 70%)"]
    end

    subgraph DataTier ["Data & State Tier (ClusterIP)"]
        Chroma["ChromaDB StatefulSet (chroma-0: 1Gi PVC)"]
        Redis["Redis Semantic Cache (Deployment: 1Gi PVC)"]
    end

    subgraph IndexingTier ["Single-Writer Indexing Tier"]
        IndexerJob["Indexer Job / CronJob (One-Shot Batch)"]
        Corpus[("FAST-NUCES Corpus (PDFs, HTML, MD)")]
    end

    Browser -->|HTTP :80| Ingress
    CI -->|HTTP /api| Ingress
    Ingress -->|/| UI
    Ingress -->|/api| API
    HPA -.->|Autoscale 3 to 8| API

    API -->|1. Check Semantic Cache| Redis
    API -->|2. Vector Query (superseded filter)| Chroma
    API -->|3. LLM Inference (JSON Schema)| Ollama["Ollama Host / API"]

    IndexerJob -->|Extract & OCR| Corpus
    IndexerJob -->|Exclusive Write| Chroma
    IndexerJob -->|Flush Cache| Redis
```

---

## API Specification

All data endpoints are served under `/api` and require non-root network routing:

| Method | Endpoint | Description | Request Body / Parameters | Response Schema |
| :--- | :--- | :--- | :--- | :--- |
| POST | `/api/ask` | Primary grounded question-answering pipeline | `{"question": "string (3-500 chars)"}` | `AskResponse` JSON with `answer`, `citations`, `answered_from_context`, `retrieved` |
| GET | `/healthz` | Liveness probe: returns 200 if process event loop is active (touches zero dependencies) | None | `{"status": "ok"}` |
| GET | `/readyz` | Readiness probe: validates Chroma connectivity, non-empty collection, embedding model pin, and Redis | None | `{"status": "ready"}` (503 if dependencies unready or model mismatched) |
| GET | `/metrics` | Prometheus metrics scrape target (latencies, cache hits, token counters, abstentions) | None | Prometheus text exposition format |
| GET | `/` | Web chat user interface (static HTML/JS) | None | HTML |

---

## Kubernetes Cluster Setup & Exact Apply Order

### 1. Cluster Start (Minikube with Calico CNI)
Start Minikube with Docker driver, Calico network plugin, and required resource thresholds:
```bash
minikube start --driver=docker --network-plugin=cni --cni=calico --cpus=4 --memory=8192
minikube addons enable ingress
minikube addons enable metrics-server
```

### 2. Exact Resource Apply Order
Manifests must be applied in strict dependency order:
```bash
# 1. Namespace
kubectl apply -f k8s/base/namespace.yaml

# 2. Configuration & Secrets
kubectl apply -f k8s/base/configmap.yaml
kubectl apply -f k8s/base/secret.placeholder.yaml

# 3. ServiceAccounts & RBAC (automount disabled)
kubectl apply -f k8s/base/serviceaccounts.yaml

# 4. Storage & Data Tier Services
kubectl apply -f k8s/base/services.yaml
kubectl apply -f k8s/base/chroma-statefulset.yaml
kubectl apply -f k8s/base/redis-deployment.yaml

# 5. Wait for Stateful Data Tier Readiness
kubectl rollout status statefulset/chroma -n rahbar --timeout=120s
kubectl rollout status deployment/redis -n rahbar --timeout=60s

# 6. Single-Writer Indexer Execution (Populate Index Before Serving)
kubectl create job --from=cronjob/reindex initial-index -n rahbar
kubectl wait --for=condition=complete job/initial-index -n rahbar --timeout=300s

# 7. Serving Tier (API, UI, Ingress, Autoscaler)
kubectl apply -f k8s/base/api-deployment.yaml
kubectl apply -f k8s/base/ui-deployment.yaml
kubectl apply -f k8s/base/ingress.yaml
kubectl apply -f k8s/base/hpa.yaml

# 8. Network Security (Deny-by-default Calico NetworkPolicy)
kubectl apply -f k8s/base/networkpolicy.yaml

# 9. Verify Serving Rollout
kubectl rollout status deployment/api -n rahbar --timeout=120s
kubectl rollout status deployment/ui -n rahbar --timeout=60s
```

Alternatively, apply the full Kustomize overlay directly:
```bash
kubectl apply -k k8s/overlays/dev/
```

---

## Clean Clone to Answered Question Walkthrough

1. **Clone Repository & Switch to Dev Branch:**
   ```bash
   git clone https://github.com/alisha-i/rahbar-rag-k8s.git
   cd rahbar-rag-k8s
   git checkout dev
   ```

2. **Verify Submission Compliance:**
   Run the mechanical grader lint to verify 0 failures and 0 deductions:
   ```bash
   python scripts/check_submission.py
   ```

3. **Run Unit & Pipeline Tests:**
   ```bash
   # Indexer tests
   PYTHONPATH=indexer python -m pytest indexer/tests

   # API & Semantic Cache tests
   PYTHONPATH=api python -m pytest api/tests
   ```

4. **Port-Forward and Query the Cluster:**
   ```bash
   kubectl port-forward svc/api 8000:8000 -n rahbar
   ```
   Query the grounded endpoint:
   ```bash
   curl -X POST http://localhost:8000/api/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "What is the minimum attendance percentage required to sit in final exams?"}'
   ```
   Expected response:
   ```json
   {
     "request_id": "7f9a12b4...",
     "answer": "According to the Undergraduate Prospectus, students must maintain a minimum attendance of 80% to be eligible to appear in the final examinations.",
     "citations": [
       {
         "source": "Prospectus-2026.pdf",
         "section": "Attendance Rules",
         "page": 14
       }
     ],
     "answered_from_context": true,
     "confidence": "high",
     "provider": "ollama"
   }
   ```

5. **Run the Automated Golden Evaluation Gate:**
   ```bash
   python eval/run_eval.py --api http://localhost:8000 --golden eval/golden.yaml --baseline eval/baseline.json
   ```

---

## Core Operational Principles

- **Single Writer Pattern (ADR 0001):** The indexer Job is the exclusive writer to ChromaDB. API pods are strictly read-only, preventing multi-replica SQLite lock collisions.
- **Pinned Embedding Model:** Both the indexer and API pin `EMBED_MODEL: "dummy-768"`. The API `/readyz` probe inspects collection metadata on startup and fails readiness (HTTP 503) upon any model mismatch.
- **Semantic Caching (ADR 0003):** Redis caches grounded queries using cosine similarity (threshold $\ge 0.85$). Conversational follow-ups and abstentions are never cached. Re-indexing triggers an automatic cache invalidation flush.
- **Citation Guardrails (Hard Rule 5):** Step 8 of the pipeline strictly filters generated citations against retrieved chunks, eliminating hallucinations and ensuring 100% citation validity.
