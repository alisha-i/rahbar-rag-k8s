# AI Usage Disclosure

In compliance with the course academic integrity policy, this document provides a transparent and exhaustive disclosure of all AI tools utilized during the construction of RAHBAR.

| Tool | What it wrote or shaped | What we changed afterwards, and why |
| :--- | :--- | :--- |
| **Google Antigravity (Claude 3.5 Sonnet / Gemini 2.5 Pro)** | Drafted initial Kubernetes base manifests, Dockerfiles, Pydantic schemas, Redis semantic cache logic, CI/CD workflow YAML, and ARCHITECTURE.md. | - Added `enableServiceLinks: false` and explicit `CHROMA_PORT: "8000"` to fix ChromaDB Rust config panic caused by K8s service link injection.<br>- Replaced Starlette `TestClient` in local unit tests with isolated function tests to circumvent Windows Python 3.14 `anyio` thread-portal deadlock.<br>- Strengthened citation validation in Step 8 to strictly enforce Hard Rule 5 by dropping any non-retrieved citations before response formatting.<br>- Structured `cd.yml` into `test`, `build-push`, and `deploy` jobs with SHA tagging and pre-rollout indexer synchronization.<br>- Authored `ARCHITECTURE.md` explaining ChromaDB StatefulSet vs API Deployment tradeoffs and storage persistence guarantees. |
| **Claude Code / Gemini CLI** | Generated candidate regular expressions for running header/footer removal and assisted in structuring ADR templates. | - Modified cleaning regexes to explicitly preserve clause numbers (`1.21`, `5.4`) and grading percentage ranges so citation grounding remained intact.<br>- Added custom Calico NetworkPolicy rules for kube-dns UDP/TCP port 53 and Ollama host traffic. |
| **Claude (claude.ai)** | Diagnosed the failing GitHub Actions runs and rewrote `cd.yml` (SHA-pinned images, indexer wait, smoke test) and parts of `ci.yml` (Trivy as a pinned CLI image, kubeconform retries, single-writer indexer wait in e2e). | - Reviewed each change, added the Docker Hub secrets ourselves, and re-ran the pipelines to confirm the results. |

---

## Collaborative Partner Workflows

The system design, verification, and code reviews were conducted collaboratively:
- **Alisha Iqbal:** Orchestrated Kubernetes manifest composition, Kind/Minikube deployment configurations, CI/CD pipeline automation, and Docker containerization.
- **Musfirah Hamid:** Audited evaluation fixture golden questions, verified citation extraction fidelity, reviewed defensive error-handling paths, and conducted edge-case testing against the university corpus.
