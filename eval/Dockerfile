# Build from the REPOSITORY ROOT:  docker build -f eval/Dockerfile -t rahbar-eval:dev .
# The Part B suite + C2 gate, run as a Kubernetes Job (k8s/eval-job.yaml).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Trivy (CI) fails on fixable HIGH/CRITICAL: take the latest OS and packaging-tool patches.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip setuptools wheel

RUN groupadd -g 10001 rahbar && useradd -u 10001 -g rahbar -m rahbar
RUN pip install --no-cache-dir httpx==0.28.1 pyyaml==6.0.2

# CVE-2025-47273: make sure no setuptools older than 78.1.1 is left anywhere in the image
RUN pip install --no-cache-dir --upgrade "setuptools>=78.1.1" && \
    find / -xdev -regextype posix-extended -regex '.*/setuptools-([0-6][0-9]|7[0-7])\.[^/]*' -prune -exec rm -rf {} + && \
    pip show setuptools | grep Version

COPY eval/ ./eval/
COPY indexer/pipeline/ ./indexer/pipeline/

USER 10001
ENTRYPOINT ["python", "eval/run_eval_v2.py"]
