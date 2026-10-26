# Illudium-Q36 — portable image for local Compose, AWS (ECS/EKS), and GCP (Cloud Run/GKE)
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    # Gunicorn / Flask
    WEB_CONCURRENCY=1 \
    GUNICORN_TIMEOUT=120

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 illudium \
    && useradd --system --uid 10001 --gid illudium --home /app --shell /usr/sbin/nologin illudium

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY --chown=illudium:illudium app.py core.py atlas.py illudium-q36.py session_store.py pricing.py report_export.py spend_ledger.py transcribe.py anonymize.py ./
COPY --chown=illudium:illudium data ./data
COPY --chown=illudium:illudium templates ./templates
COPY --chown=illudium:illudium static ./static
COPY --chown=illudium:illudium intel/.gitkeep intel/.gitkeep
COPY --chown=illudium:illudium briefings/.gitkeep briefings/.gitkeep

# Optional: docker compose / CI can pass --build-arg BUILD_SHA=$(git rev-parse --short HEAD)
ARG BUILD_SHA=unknown
ENV BUILD_SHA=${BUILD_SHA}

RUN mkdir -p /app/intel /app/briefings \
    && chown -R illudium:illudium /app

USER illudium

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# Single process entry; WEB_CONCURRENCY controls workers.
# worker-tmp-dir under /tmp avoids permission issues on read-only rootfs overlays.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --timeout ${GUNICORN_TIMEOUT} --pid /tmp/gunicorn.pid --worker-tmp-dir /tmp --access-logfile - --error-logfile - app:app"]
