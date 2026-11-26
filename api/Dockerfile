FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Trivy (CI) fails on fixable HIGH/CRITICAL: take the latest OS and packaging-tool patches.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip setuptools wheel

RUN groupadd -g 10001 rahbar && \
    useradd -u 10001 -g rahbar -s /bin/bash -m rahbar

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# CVE-2025-47273: make sure no setuptools older than 78.1.1 is left anywhere in the image
RUN pip install --no-cache-dir --upgrade "setuptools>=78.1.1" && \
    find / -xdev -regextype posix-extended -regex '.*/setuptools-([0-6][0-9]|7[0-7])\.[^/]*' -prune -exec rm -rf {} + && \
    pip show setuptools | grep Version

COPY app/ ./app/

USER rahbar

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
