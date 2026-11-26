# RAHBAR starter Makefile. `make` or `make help` lists the targets.
# Targets marked (needs yours) work once you have written the Dockerfiles or manifests.

SHELL := /bin/bash
NS ?= rahbar
TAG ?= dev
PY ?= python3
VENV ?= .venv
BIN := $(abspath $(VENV))/bin
# The Ingress address. On Linux this is the minikube IP. On macOS or Windows with the
# docker driver, run `minikube tunnel` in another terminal and use INGRESS_IP=127.0.0.1.
INGRESS_IP ?= $(shell minikube ip 2>/dev/null)
HOST ?= rahbar.local

.DEFAULT_GOAL := help
.PHONY: help install test lint dev cluster images apply secret index ask fetch eval eval-fixtures check a3-index a3-batch-b a3-eval a3-eval-local a3-coverage a3-collect port-forward

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

install: ## create .venv and install the api, indexer and tool dependencies
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r api/requirements-dev.txt -r indexer/requirements-dev.txt

test: ## run the api and indexer tests (stubs only: no cluster, no model, no network)
	cd api && $(BIN)/pytest -q
	cd indexer && $(BIN)/pytest -q

# ---- Assignment 03 (RAGOps). Full walkthrough: README "Assignment 03" and k8s/README.md ----
a3-index: ## A: ingest + verify the corpus as a Job (run twice: second run embeds 0 chunks)
	kubectl delete job indexer -n $(NS) --ignore-not-found
	kubectl apply -f k8s/base/indexer-job.yaml
	kubectl wait --for=condition=complete job/indexer -n $(NS) --timeout=600s; kubectl logs job/indexer -n $(NS) -c indexer

a3-batch-b: ## A/E3: ingest Batch B together with the corpus (verification report per document)
	kubectl delete job indexer-batch-b -n $(NS) --ignore-not-found
	kubectl apply -f k8s/indexer-batch-b-job.yaml
	kubectl wait --for=condition=complete job/indexer-batch-b -n $(NS) --timeout=600s; kubectl logs job/indexer-batch-b -n $(NS) -c indexer

a3-eval: ## B: three-level suite + gate against the live api, as a Job (LABEL=baseline)
	kubectl delete job rahbar-eval-job -n $(NS) --ignore-not-found
	sed 's/"baseline",/"$(or $(LABEL),baseline)",/' k8s/eval-job.yaml | kubectl apply -f -
	-kubectl wait --for=condition=complete job/rahbar-eval-job -n $(NS) --timeout=900s
	kubectl logs job/rahbar-eval-job -n $(NS)

a3-eval-local: ## B: same suite from your laptop through a port-forward (make port-forward first)
	$(BIN)/python eval/run_eval_v2.py --api http://localhost:8000 --label $(or $(LABEL),baseline)

a3-coverage: ## E: coverage before and after the new golden questions
	-OLLAMA_URL=http://localhost:11434 $(BIN)/python eval/coverage.py --golden eval/golden.jsonl
	OLLAMA_URL=http://localhost:11434 $(BIN)/python eval/coverage.py --golden eval/golden_extended.jsonl

a3-collect: ## copy results and traces from the PVC into eval/results and traces/ (to commit)
	kubectl apply -f k8s/toolbox-pod.yaml
	kubectl wait --for=condition=Ready pod/toolbox -n $(NS) --timeout=120s
	kubectl cp $(NS)/toolbox:/data/results ./eval/results
	kubectl cp $(NS)/toolbox:/data/traces ./traces

port-forward: ## api on localhost:8000 (metrics at /metrics)
	kubectl port-forward svc/api 8000:8000 -n $(NS)

lint: ## ruff and mypy over everything in Python
	$(BIN)/ruff check api indexer eval corpus
	cd api && $(BIN)/mypy app
	cd indexer && $(BIN)/mypy pipeline

dev: ## run the API with stubs on http://localhost:8000, serving the chat UI too
	cd api && SERVE_UI_DIR=../ui RETRIEVER=stub LLM_PROVIDER=stub $(BIN)/uvicorn app.main:app --reload --port 8000

cluster: ## start minikube with Calico, then enable metrics-server and ingress
	minikube start --driver=docker --cni=calico --memory=6144 --cpus=4
	minikube addons enable metrics-server
	minikube addons enable ingress

images: ## build the three images and load them into minikube (needs yours: Dockerfiles)
	docker build -t rahbar-api:$(TAG) api
	docker build -t rahbar-indexer:$(TAG) -f indexer/Dockerfile .
	docker build -t rahbar-eval:$(TAG) -f eval/Dockerfile .
	docker build -t rahbar-ui:$(TAG) ui
	minikube image load rahbar-api:$(TAG)
	minikube image load rahbar-indexer:$(TAG)
	minikube image load rahbar-eval:$(TAG)
	minikube image load rahbar-ui:$(TAG)

apply: ## apply the dev overlay (needs yours: manifests)
	kubectl apply -k k8s/overlays/dev

secret: ## create the LLM key Secret from the LLM_API_KEY variable; never from a file
	@test -n "$$LLM_API_KEY" || { echo "export LLM_API_KEY=... first"; exit 1; }
	kubectl create secret generic rahbar-secret -n $(NS) \
	  --from-literal=LLM_API_KEY="$$LLM_API_KEY" --dry-run=client -o yaml | kubectl apply -f -

index: ## run the indexer once, from the CronJob's template (needs yours: CronJob reindex)
	kubectl create job --from=cronjob/reindex reindex-$$(date +%s) -n $(NS)
	@echo "watch it with: kubectl get jobs -n $(NS) -w"

ask: ## ask through the Ingress: make ask Q="What CGPA puts a student on academic warning?"
	@curl -s -X POST "http://$(INGRESS_IP)/api/ask" -H "Host: $(HOST)" \
	  -H 'Content-Type: application/json' -d "$$(Q="$(Q)" $(PY) -c 'import json,os; print(json.dumps({"question": os.environ["Q"]}))')" \
	  | $(PY) -m json.tool

fetch: ## download the documents listed in corpus/sources.yaml
	$(BIN)/python corpus/fetch.py

eval: ## score eval/golden.yaml through the Ingress and apply the CI gate
	$(BIN)/python eval/run_eval.py --api "http://$(INGRESS_IP)" --host $(HOST)

eval-fixtures: ## score the CI fixture golden set against a local `make dev`
	$(BIN)/python eval/run_eval.py --api http://localhost:8000 --golden eval/fixtures/golden.fixture.yaml --out /tmp/rahbar-fixture-results.json

check: ## run the submission checker
	$(PY) scripts/check_submission.py
