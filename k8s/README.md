# RAHBAR on Kubernetes: Assignment 03 runbook (Parts A to F)

Every command here runs against **your minikube cluster**. Each step names the screenshot it
produces for the report appendix (label it with the part, e.g. "A1"). Copy every number you
report from the files these steps write; do not type numbers from memory.

Windows note: run these in Git Bash or WSL (the `sed`, `$(...)` and `&` syntax is bash).

---

## 0. Fresh cluster to working pipeline

```bash
minikube start --driver=docker --cni=calico --memory=6144 --cpus=4
minikube addons enable metrics-server
minikube addons enable ingress

# Ollama on the laptop (ollama-host mode). It must listen on all interfaces.
OLLAMA_HOST=0.0.0.0 ollama serve          # leave running
ollama pull llama3.2:3b
ollama pull nomic-embed-text

# Images: api and ui build from their folders; indexer and eval from the repo root.
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev -f indexer/Dockerfile .
docker build -t rahbar-eval:dev -f eval/Dockerfile .
docker build -t rahbar-ui:dev ui
for i in api indexer eval ui; do minikube image load rahbar-$i:dev; done
docker pull busybox:1.36 && minikube image load busybox:1.36   # toolbox pod

kubectl apply -k k8s/overlays/dev
kubectl rollout status statefulset/chroma -n rahbar --timeout=180s
kubectl rollout status deployment/redis  -n rahbar --timeout=120s
kubectl wait --for=condition=complete job/indexer -n rahbar --timeout=600s
kubectl rollout status deployment/api    -n rahbar --timeout=300s
```

If the API stays 0/1 READY: `kubectl logs deploy/api -n rahbar` and `curl` its `/readyz`
through a port-forward. It names the failing dependency (chroma, chroma_model_pin, redis).
If the indexer Job fails with `cannot embed with pinned model`, Ollama is not reachable from the
cluster: check `OLLAMA_HOST=0.0.0.0` and the firewall.

What lives where:

| Object | RAGOps responsibility |
| --- | --- |
| `Job indexer`, `CronJob reindex`, `Job indexer-batch-b`, `Job indexer-candidate` (all pods labelled `app.kubernetes.io/name: indexer`) | the only writers to Chroma: ingest, verify, update the data lake, update the retrieval source |
| `NetworkPolicy allow-chroma` | enforces "only indexer pods and API pods may reach Chroma"; `concurrencyPolicy: Forbid` stops two CronJob writers overlapping |
| `PVC corpus-pvc` (`/corpus`) | data lake: documents, `quarantine/`, `archive/`, `.index_state*.json` |
| `PVC eval-results-pvc` (`/data`) | `results/` (verification reports, eval results, coverage) and `traces/<pod>.jsonl` |
| `ConfigMap rahbar-config` | pipeline settings incl. `EMBED_MODEL` pin, chunking, top-k, floor |
| `ConfigMap rahbar-gate-thresholds` | release-gate thresholds, mounted into the eval Job |
| `Job rahbar-eval-job` / `rahbar-eval-candidate` | Part B suite + gate; Job status Failed = gate FAIL |
| `Deployment api` (rolling, `maxUnavailable: 0`) / `Deployment api-candidate` + `Service api-candidate` | live and candidate query pipelines |

---

## A. Data management: verify and update

```bash
# A1 + A2: the indexer Job already ran once at apply time. Run it again on the unchanged corpus:
kubectl delete job indexer -n rahbar && kubectl apply -f k8s/base/indexer-job.yaml
kubectl wait --for=condition=complete job/indexer -n rahbar --timeout=600s
kubectl logs job/indexer -n rahbar -c indexer | grep -E 'incremental diff|embedded'
```
**Screenshot A (2nd run):** `"embedded_chunks": 0` and `unchanged: 3`.

```bash
# Batch B (10 planted problems) ingested together with the corpus
kubectl apply -f k8s/indexer-batch-b-job.yaml
kubectl wait --for=condition=complete job/indexer-batch-b -n rahbar --timeout=600s
kubectl logs job/indexer-batch-b -n rahbar -c indexer | grep '"verification"'
```
**Screenshot A (Batch B):** one line per document with pass/FAIL and the reason for each of the
four checks. The full report is `/data/results/verification_batch_b_<ts>.json`.

Orphan proof and rollback (one-off pods from the indexer image; the toolbox has no Python):
```bash
# count by document id (before/after deleting a document from /corpus)
kubectl run count-by-doc -n rahbar --rm -i --restart=Never --image=rahbar-indexer:dev \
  --image-pull-policy=IfNotPresent -l app.kubernetes.io/name=indexer \
  --overrides='{"spec":{"containers":[{"name":"c","image":"rahbar-indexer:dev","imagePullPolicy":"IfNotPresent","args":["--count-by-doc"],"envFrom":[{"configMapRef":{"name":"rahbar-config"}}]}]}}'

# delete a document, re-index, count again: the deleted document must be gone (0 chunks)
kubectl apply -f k8s/toolbox-pod.yaml && kubectl wait --for=condition=Ready pod/toolbox -n rahbar
kubectl exec -n rahbar toolbox -- sh -c 'mkdir -p /corpus/_removed && mv /corpus/fees-2026.md /corpus/_removed/'
kubectl delete job indexer -n rahbar && kubectl apply -f k8s/base/indexer-job.yaml   # logs: "orphan chunks removed"
# ...run count-by-doc again (fees-2026.md must be absent), then move the file back and re-index.

# rollback demo: serve the archived 2026 rules again, mark 2027 superseded
kubectl run rollback -n rahbar --rm -i --restart=Never --image=rahbar-indexer:dev \
  -l app.kubernetes.io/name=indexer \
  --overrides='{"spec":{"securityContext":{"runAsUser":10001,"fsGroup":10001},"containers":[{"name":"c","image":"rahbar-indexer:dev","imagePullPolicy":"IfNotPresent","args":["--corpus-dir","/corpus","--rollback","academic-rules-2026.md","--supersede","doc07_academic_rules_2027.md"],"envFrom":[{"configMapRef":{"name":"rahbar-config"}}],"volumeMounts":[{"name":"c","mountPath":"/corpus"}]}],"volumes":[{"name":"c","persistentVolumeClaim":{"claimName":"corpus-pvc"}}]}}'
```
Note: the next scheduled re-index re-applies the recency check, so the 2027 version wins again
unless you remove it from the data lake. That is intended: rollback is an operational override.

Demo ("ingest a batch you have not seen"):
```bash
kubectl cp ./new_docs/. rahbar/toolbox:/corpus/batch_b/
kubectl delete job indexer-batch-b -n rahbar && kubectl apply -f k8s/indexer-batch-b-job.yaml
kubectl logs -f job/indexer-batch-b -n rahbar -c indexer
```

---

## B. Three-level offline suite as a Job

```bash
kubectl apply -f k8s/eval-job.yaml                      # label "baseline", writes latest_baseline.json
kubectl logs -f job/rahbar-eval-job -n rahbar
```
**Screenshot B:** the summary table (module, component, end-to-end, run-2 deltas, gate).

Faithfulness uses an LLM judge (llama3.2 via Ollama, temperature 0). For the agreement number,
label at least 8 answers yourselves: take `answers_to_label_baseline_<ts>.jsonl` from the results
PVC (`make a3-collect`), set `"faithful": true/false` by reading each answer against its trace,
save as `eval/human_labels.jsonl` (format: `eval/human_labels.example.jsonl`), rebuild
`rahbar-eval:dev` and re-run the Job.

---

## C/F3. Candidate, gate, controlled release

The change under test (C1): chunk size 250 / overlap 40 instead of 400 / 50, in its own
collection `rahbar-candidate`.

```bash
kubectl apply -f k8s/candidate-indexer-job.yaml
kubectl wait --for=condition=complete job/indexer-candidate -n rahbar --timeout=600s
kubectl apply -f k8s/candidate-deployment.yaml
kubectl rollout status deployment/api-candidate -n rahbar
kubectl apply -f k8s/eval-job-candidate.yaml
kubectl logs -f job/rahbar-eval-candidate -n rahbar        # gate vs latest_baseline.json
kubectl get job rahbar-eval-candidate -n rahbar             # Complete = PASS, Failed = FAIL
```

A candidate stopped by the gate (never rolled out):
```bash
kubectl set env deployment/api-candidate -n rahbar RELEVANCE_FLOOR=0.95   # deliberately bad
kubectl rollout status deployment/api-candidate -n rahbar
sed 's/"candidate"]/"failing_candidate"]/; s/rahbar-eval-candidate/rahbar-eval-failing/' \
  k8s/eval-job-candidate.yaml | kubectl apply -f -
kubectl logs -f job/rahbar-eval-failing -n rahbar           # FAIL -> no rollout
kubectl set env deployment/api-candidate -n rahbar RELEVANCE_FLOOR-          # undo the bad env
```
**Screenshot C:** gate FAIL and gate PASS.

Release the passing candidate to the live Deployment, then a bad release and its undo:
```bash
kubectl set env deployment/api -n rahbar CHROMA_COLLECTION=rahbar-candidate
kubectl rollout status deployment/api -n rahbar             # maxUnavailable: 0

kubectl set env deployment/api -n rahbar TOP_K=1            # a bad release
kubectl rollout status deployment/api -n rahbar
kubectl rollout undo deployment/api -n rahbar               # back to the previous revision
kubectl rollout status deployment/api -n rahbar
kubectl rollout history deployment/api -n rahbar
```
To show the PREVIOUS collection after an undo, undo twice (or undo the collection change itself):
```bash
kubectl rollout undo deployment/api -n rahbar --to-revision=<revision before the release>
make port-forward &   # then ask one question
curl -s -XPOST localhost:8000/api/ask -H 'content-type: application/json' \
  -d '{"question":"What is the minimum attendance?"}' | python -m json.tool | grep trace_id
kubectl logs -n rahbar -l app.kubernetes.io/name=api --tail=50 | grep <trace_id>
```
**Screenshot F:** `rollout status`, `rollout undo`, then a trace whose `collection_name` is
`rahbar` and `embedding_version` is `nomic-embed-text|chunk400-ov50`.

---

## D. Traces and metrics

```bash
kubectl logs -n rahbar -l app.kubernetes.io/name=api --tail=-1 | grep trace_id | tail -1   # D1 full record
make a3-collect                                                       # copies /data/traces -> ./traces
python scripts/trace_tool.py <trace_id> --file traces/                # D2
python scripts/trace_metrics.py --file traces/ --last-minutes 120 --out eval/results/trace_metrics.json  # D3
kubectl port-forward svc/api 8000:8000 -n rahbar & curl -s localhost:8000/metrics | grep rahbar_   # F4
```
D4: ask about exam guidelines after Batch B; the trace shows
`guardrail_actions.action_taken = redacted_injected_text` for the doc08 chunk.

---

## E. Coverage (laptop, uses the same Ollama embedding model)

```bash
export OLLAMA_URL=http://localhost:11434
python eval/coverage.py --calibrate                       # pick and justify a threshold
python eval/coverage.py --golden eval/golden.jsonl --threshold <t>           # E2, exit 1 if < 85%
python eval/coverage.py --golden eval/golden_extended.jsonl --threshold <t>  # E4, coverage rises
```
E3 (data drift): after `indexer-batch-b`, run the eval Job again with label `after_batch_b`
(`make a3-eval LABEL=after_batch_b`) and compare with the baseline file.

---

## F. Gate threshold from a ConfigMap, no rebuild

```bash
kubectl edit configmap rahbar-gate-thresholds -n rahbar        # e.g. min_faithfulness: 0.99
kubectl delete job rahbar-eval-job -n rahbar && kubectl apply -f k8s/eval-job.yaml
kubectl logs -f job/rahbar-eval-job -n rahbar                  # the decision flips
```

Inventory and logs for the appendix:
```bash
kubectl get all,pvc,configmap,cronjob,job -n rahbar
kubectl logs job/indexer -n rahbar -c indexer
kubectl logs job/rahbar-eval-job -n rahbar
```

## F5. Resources (measure, then set)

While the indexer Job and an eval run are going: `kubectl top pods -n rahbar` (several samples).
Set requests near the steady value and limits above the peak, copy the measured numbers into the
report and `docs/evidence/03-kubectl-top.txt`. Current settings:

| Component | CPU req / limit | Memory req / limit |
| --- | --- | --- |
| api (Deployment, 3-8 via HPA) | 100m / 500m | 128Mi / 512Mi |
| chroma (StatefulSet) | see `chroma-statefulset.yaml` | |
| redis | see `redis-deployment.yaml` | |
| indexer Jobs / CronJob | 100m / 1000m | 256Mi / 1Gi |
| eval Job | 50m / 500m | 128Mi / 256Mi |
| ui | see `ui-deployment.yaml` | |
