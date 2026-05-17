# syntax=docker/dockerfile:1.7
# Multi-stage build for AEGIS FastAPI app.
# Stage 1 ("builder"): install deps into a venv (CPU-only torch wheel) +
# pre-download spaCy + MiniLM models so cold-start latency stays low.
# Stage 2 ("runtime"): copy the venv + model caches into a slim image.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VENV_PATH=/opt/venv \
    HF_HOME=/opt/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv ${VENV_PATH}
ENV PATH="${VENV_PATH}/bin:${PATH}"

# Install torch CPU-only first (saves ~1.5GB vs the default CUDA build).
RUN pip install --upgrade pip setuptools wheel \
    && pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.4.1"

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Pre-fetch the spaCy English large model + MiniLM sentence-transformer so
# the first request after deploy doesn't pay the download tax.
RUN python -m spacy download en_core_web_lg \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"


FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VENV_PATH=/opt/venv \
    HF_HOME=/opt/hf-cache \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system aegis \
    && useradd --system --gid aegis --home /app --shell /usr/sbin/nologin aegis

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache

WORKDIR /app
COPY --chown=aegis:aegis app ./app
COPY --chown=aegis:aegis cli ./cli
COPY --chown=aegis:aegis alembic.ini ./alembic.ini
COPY --chown=aegis:aegis docker-entrypoint.sh ./docker-entrypoint.sh

RUN chmod +x ./docker-entrypoint.sh \
    && chown -R aegis:aegis /opt/hf-cache

USER aegis
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
