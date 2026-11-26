# Build from the REPOSITORY ROOT:  docker build -f indexer/Dockerfile -t rahbar-indexer:dev .
# (root context so the seed corpus and Batch B can be baked in for the first run)
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Trivy (CI) fails on fixable HIGH/CRITICAL: take the latest OS patches too.
RUN apt-get update && \
    apt-get upgrade -y && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 rahbar && \
    useradd -u 10001 -g rahbar -s /bin/bash -m rahbar

COPY indexer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# CVE-2025-47273: make sure no setuptools older than 78.1.1 is left anywhere in the image
RUN pip install --no-cache-dir --upgrade "setuptools>=78.1.1" && \
    find / -xdev -regextype posix-extended -regex '.*/setuptools-([0-6][0-9]|7[0-7])\.[^/]*' -prune -exec rm -rf {} + && \
    pip show setuptools | grep Version

COPY indexer/pipeline/ ./pipeline/
# Seed documents. The corpus PVC is mounted at /corpus and hides anything baked there, so the
# Job's init container copies /seed/corpus into the PVC on the first run only.
COPY indexer/fixtures/academic-rules-2023.md indexer/fixtures/academic-rules-2026.md indexer/fixtures/fees-2026.md /seed/corpus/
COPY corpus/batch_b/ /seed/batch_b/

USER 10001

ENTRYPOINT ["python", "-m", "pipeline.run"]
