FROM python:3.11-slim

WORKDIR /app

# Patch glibc CVEs (CVE-2026-0915, CVE-2025-15281, CVE-2026-0861)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --upgrade pip wheel setuptools && \
    pip install --no-cache-dir .

RUN adduser --disabled-password --gecos "" appuser
USER appuser

EXPOSE 3000

CMD ["python", "-m", "ado_mcp_server"]
