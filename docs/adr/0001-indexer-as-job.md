# ADR 0001: Decoupling Corpus Indexing as a Kubernetes Job

## Status
Accepted · 2026-11-06

## Context
In a Retrieval-Augmented Generation (RAG) architecture running on Kubernetes, raw documents must be parsed, chunked, embedded via a dense representation model, and stored into a persistent ChromaDB vector store. The API serving tier runs as a horizontally scalable Deployment (3 to 8 replicas) behind an Ingress and Service to handle user queries.

A naive design pattern commonly observed in single-node prototypes embeds the indexing procedure directly into the API application startup sequence (such as a FastAPI lifespan or startup event). However, on a distributed container orchestration platform with multiple API replicas and persistent storage volumes, this approach introduces severe architectural failures:

1. **Concurrent Write Collisions & Index Corruption:** With 3 API replicas sharing or accessing a single persistent volume, 3 concurrent processes race to initialize and write embeddings into ChromaDB simultaneously. ChromaDB is designed for single-writer concurrency; concurrent multi-process writes without distributed coordination yield lock contention, torn index segments, and silent metadata corruption.
2. **Redundant & Prohibitive Re-indexing on Restarts:** Pod lifecycles in Kubernetes are ephemeral. Every rolling deployment, horizontal scaling event, node drain, liveness failure, or OOMKill triggers pod restarts. Re-indexing on startup causes every starting pod to redundantly re-extract and re-embed the entire corpus, placing severe, unnecessary compute load on embedding models (or external APIs) and I/O bottlenecks on storage.
3. **Readiness Probe Invalidation & Cascading Rollout Deadlocks:** A pod must only accept user traffic when its index is populated and queryable. If an API pod marks itself ready before indexing completes, it serves empty retrievals (returning false abstentions or hallucinations). Conversely, if the readiness probe waits for indexing (which can take minutes for large corpora), the startup/readiness probe failure thresholds will trigger kubelet termination, resulting in a CrashLoopBackOff cycle where working pods are replaced by unready pods, taking the entire cluster offline.
4. **Lifecycle Coupling:** A document update or policy revision should never require rebuilding, re-tagging, and redeploying the API container image. Coupling indexing with the API prevents on-demand re-indexing upon document change.

## Decision
We decouple indexing entirely from the API runtime lifecycle and implement it as a dedicated, one-shot Kubernetes `Job` (`indexer-job.yaml`) and recurring `CronJob` (`reindex-cronjob.yaml`):

1. **One Writer, Multiple Readers:** The indexer `Job` is the exclusive writer to the ChromaDB vector store. The API tier (`Deployment/api`) operates strictly as a read-only client.
2. **Readiness Verification:** The API's readiness probe (`/readyz`) verifies that Chroma is reachable, the collection exists and is non-empty (`collection.count() > 0`), and the collection metadata matches the pinned `EMBED_MODEL`. If the index is absent or empty, the API pod is withheld from the Service endpoints without being restarted.
3. **On-Demand & Zero-Downtime Re-indexing:** When documents change, re-indexing is triggered independently without redeploying the API:
   ```bash
   kubectl create job --from=cronjob/reindex manual-reindex-1 -n rahbar
   ```
   The API pods continue serving user requests against the existing index until Chroma atomic collection swaps or updates complete.

## Options Considered

- **Option A: Indexing in API Startup (Lifespan Hook)**  
  *Rejected.* Leads directly to concurrent write races across 3 replicas, readiness probe deadlocks during rollouts, and wasteful re-computation on every pod restart.
- **Option B: API Pod InitContainer**  
  *Rejected.* An `initContainer` on the API Deployment would still execute across every replica when scaling from 3 to 8 pods. If replicas initialize concurrently, they still race to write to the single Chroma volume.
- **Option C: Dedicated Indexer Kubernetes Job & CronJob (Selected)**  
  *Accepted.* Provides strict single-writer safety, decouples indexing batch resources (CPU/memory limits) from serving latency requirements, and allows manual and scheduled re-indexing with zero downtime.

## Consequences

- **Positive:**
  - Complete elimination of multi-replica write race conditions and index corruption.
  - Zero-downtime rolling updates of the API without paying re-indexing penalties.
  - API pods boot in seconds; readiness accurately reflects dependency health rather than batch job execution.
  - Independent resource allocation: the indexer Job can request higher peak CPU and memory (1Gi) for PDF parsing and OCR without inflating API pod baseline allocations.
- **Negative / Operational Considerations:**
  - New environments require running the indexer Job before API pods report `1/1 READY`. This sequence must be explicitly documented in the repository `README.md`.
  - The API readiness probe must return HTTP 503 until the indexer completes, withholding traffic gracefully.
