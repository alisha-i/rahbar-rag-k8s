# ADR 0003: Semantic Cache Store Architecture (Redis vs Chroma, Eviction, TTL, Invalidation)

## Status
Accepted · 2026-11-06

## Context
In a RAG question-answering system, generating answers through an LLM provider constitutes the single largest contributor to end-to-end request latency (typically 1.5s to 8s per query) and compute consumption. Many university queries are repetitive or semantically equivalent (e.g., *"What is the minimum CGPA for MS CS graduation?"* vs *"What CGPA is required to graduate from MS CS?"*).

To achieve sub-second response times for recurrent inquiries, RAHBAR introduces a semantic caching tier between query reception and LLM generation. The cache must satisfy the following architectural requirements:
1. **Semantic Matching:** Match incoming questions not solely on exact string equality, but on semantic proximity (cosine similarity of query dense embeddings $\ge 0.85$).
2. **Centralized Multi-Replica State:** Serve identical cached responses across all 3 to 8 API deployment replicas without partition skew.
3. **Eviction and TTL:** Bound memory footprint and discard stale entries via deterministic time-to-live policies.
4. **Guaranteed Cache Invalidation:** Instantly purge cached responses whenever the document index is rebuilt or updated (preventing the serving of obsolete answers after a university regulation amendment).
5. **Never-Cache Safety Guardrails:** Strictly forbid caching conversational follow-ups (which lack standalone context) and honest abstentions / fallback errors (`answered_from_context: false`).

---

## Options Considered

### Option A: Second ChromaDB Collection for Queries
Store query embeddings, answers, and citations in a secondary collection within the ChromaDB instance (`rahbar-cache`).
- **Advantages:** Native cosine similarity vector search (`collection.query`) handled by Chroma.
- **Disadvantages:**
  - ChromaDB is an append-optimized disk-backed vector store, not a high-throughput cache. Lookups incur SQLite/disk overhead.
  - Chroma does not support automatic TTL expiration, key-level eviction policies, or microsecond memory lookups.
  - Would require API pods to act as writers to Chroma, violating our core *single-writer* architecture established in ADR 0001.

### Option B: In-Memory In-Process Cache (Python LRU / Dict per Pod)
Store cached embeddings in API process memory.
- **Advantages:** Zero network serialization overhead.
- **Disadvantages:**
  - In a Kubernetes deployment with 3 to 8 pods, each pod maintains an isolated in-memory cache. A query hitting Pod A provides zero benefit to Pod B, resulting in cache thrashing, redundant LLM calls, and divergent answers.
  - Memory consumption scales linearly with the number of replicas and query volume.

### Option C: Centralized Redis 7 with Vector Similarity & Invalidation (Selected)
Deploy a dedicated Redis instance (`Deployment/redis`) in namespace `rahbar` accessible via ClusterIP `redis:6379`.
- **Advantages:**
  - Microsecond latency in-memory key-value store shared across all API replicas.
  - Native key expiration (`ex=86400` / 24-hour TTL) and Redis memory eviction (`volatile-lru`).
  - Supports rapid key scanning and atomic invalidation (`semcache:*`) upon indexer reindex.
  - Separates analytical retrieval storage (Chroma) from operational ephemeral caching (Redis).

---

## Decision

We adopt **Option C: Centralized Redis 7.4** with the following implementation design:

### 1. Storage Schema
Cached entries are stored under key prefix `semcache:<hash>` with a 24-hour TTL (`ex=86400`):
```json
{
  "question": "What is the minimum CGPA required to graduate from MS CS?",
  "embedding": [0.024, -0.051, ...],
  "request_id": "req-1234",
  "answer": "According to Section 4.2 of the Graduate Prospectus, a minimum CGPA of 2.50 is required...",
  "citations": [{"source": "Prospectus-2026.pdf", "section": "4.2", "page": 18}],
  "answered_from_context": true,
  "confidence": "high",
  "retrieved": [{"chunk_id": "chunk_42", "source": "Prospectus-2026.pdf", "score": 0.91}],
  "provider": "cache (similarity 0.94)"
}
```

### 2. Lookup & Similarity Threshold
When a question arrives:
1. The question is embedded using the pinned `EMBED_MODEL` via Ollama.
2. The semantic cache scans active `semcache:*` keys and calculates cosine similarity:
   $$\text{cosine\_sim}(\vec{q}, \vec{k}) = \frac{\vec{q} \cdot \vec{k}}{\|\vec{q}\|_2 \|\vec{k}\|_2}$$
3. If $\max(\text{sim}) \ge \text{CACHE\_THRESHOLD}$ (default: `0.85`), the cached answer is returned immediately, bypassing Chroma retrieval and LLM inference.
4. If no candidate exceeds the threshold, the request proceeds through the full 8-step pipeline.

### 3. Never-Cache Guardrails
To prevent pollution and hallucination loops, the cache layer strictly enforces `never_cache`:
- **Follow-up Questions:** Questions containing conversational context pronouns or phrases (e.g., *"What about that?"*, *"And then?"*, *"Explain further"*) are never looked up or cached, as their semantic interpretation depends on transient conversation history.
- **Abstentions:** Responses with `answered_from_context: false` are never cached. If a user asks about an absent topic (e.g. hostel rules) and the corpus is subsequently updated with hostel guidelines, an abstention cache entry would incorrectly suppress valid answers.
- **Error / Low-Confidence Fallbacks:** Schema parsing failures and provider timeouts are never cached.

### 4. Cache Invalidation upon Indexer Re-index
Whenever the indexer Job or CronJob publishes a new index to ChromaDB:
1. The indexer executes `invalidate_cache()`, which scans and flushes all `semcache:*` keys in Redis.
2. This ensures zero stale answers survive across corpus revisions, maintaining 100% alignment between retrieved context and cached responses.

---

## Consequences

- **Positive:**
  - Repeated and semantically close questions resolve in under 10ms, dramatically reducing load on Ollama/Hosted LLMs and saving compute.
  - Zero cross-replica drift: all 3 API replicas observe identical cached entries.
  - Predictable memory footprint: 24h TTL prevents unbounded Redis growth.
  - Clean invalidation lifecycle tied directly to corpus indexing events.
- **Negative / Mitigations:**
  - Redis constitutes an additional infrastructure component; mitigated by including a Redis readiness check in `/readyz` and gracefully bypassing caching if Redis is transiently unreachable.
