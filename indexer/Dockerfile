FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 rahbar && \
    useradd -u 10001 -g rahbar -s /bin/bash -m rahbar

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY fixtures/ /corpus/

USER rahbar

ENTRYPOINT ["python", "-m", "pipeline.run"]
