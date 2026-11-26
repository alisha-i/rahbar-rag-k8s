# Build from the REPOSITORY ROOT:  docker build -f eval/Dockerfile -t rahbar-eval:dev .
# The Part B suite + C2 gate, run as a Kubernetes Job (k8s/eval-job.yaml).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN groupadd -g 10001 rahbar && useradd -u 10001 -g rahbar -m rahbar
RUN pip install --no-cache-dir httpx==0.28.1 pyyaml==6.0.2

COPY eval/ ./eval/
COPY indexer/pipeline/ ./indexer/pipeline/

USER 10001
ENTRYPOINT ["python", "eval/run_eval_v2.py"]
