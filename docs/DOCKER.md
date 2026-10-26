# Docker & multi-cloud deploy

Illudium-Q36 ships as a single container. **Docker Compose** is the local / shared env build; the **same image** runs on **AWS** and **GCP**.

For the short day-to-day loop (`.env` → start detached → full purge), see **[QUICK_START.md](QUICK_START.md)**.

## Local Compose

```bash
# Demo (no Anthropic key)
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build

# Production-like (needs .env with ANTHROPIC_API_KEY + FLASK_SECRET_KEY)
cp .env.example .env
docker compose up --build
```

Open http://127.0.0.1:8080 — health: http://127.0.0.1:8080/healthz

| Env var | Purpose |
|---------|---------|
| `ANTHROPIC_API_KEY` | Claude API key (omit if `DEMO_MODE=true`) |
| `DEMO_MODE` | `true` = sample chat/briefings |
| `FLASK_SECRET_KEY` | Stable session secret (required in real deploys) |
| `HOST` / `PORT` | Bind address (image defaults `0.0.0.0:8080`) |
| `WEB_CONCURRENCY` | Gunicorn workers (**keep `1` per replica** for sticky in-memory chat) |
| `USE_VAULT` / `VAULT_*` | Optional HashiCorp Vault |
| `ILLUDIUM_IMAGE` | Override image name when pulling from a registry |
| `HOST_PORT` | Host port mapped to container 8080 |

Volumes `illudium_intel` and `illudium_briefings` persist uploads and reports.

### Sticky sessions (required for scaled chat memory)

Chat history is in-process memory. Each replica must run **one** Gunicorn worker, and clients must stick to one replica:

```bash
# Local multi-replica with nginx ip_hash stickiness
docker compose -f docker-compose.yml -f docker-compose.sticky.yml up --build --scale illudium-q36=2
```

| Cloud | Stickiness |
|-------|------------|
| **AWS ALB** | Target group stickiness (cookie) enabled |
| **GCP Cloud Run / GCLB** | Session affinity enabled |
| **Local Compose** | `docker-compose.sticky.yml` (nginx `ip_hash`) |

Single-replica laptop use needs only `WEB_CONCURRENCY=1` (Compose default).

## Cloud contract (AWS + GCP)

Keep these identical across clouds:

1. **Image** from `Dockerfile` (listens on **8080**, `GET /healthz`)
2. **Secrets as env vars** — never in the image layers
3. **Same variable names** as Compose

| Cloud | Build registry | Secrets | Typical runtime |
|-------|----------------|---------|-----------------|
| **AWS** | ECR | Secrets Manager / SSM | ECS Fargate or EKS |
| **GCP** | Artifact Registry | Secret Manager | Cloud Run or GKE |

Details:

- [deploy/aws/README.md](../deploy/aws/README.md)
- [deploy/gcp/README.md](../deploy/gcp/README.md)

## Why this shape

- One Compose file for developers and CI smoke tests
- One image for both hyperscalers (no AWS-/GCP-specific packages required at runtime)
- Orchestrator injects secrets; app already reads them via `os.environ` / `.env`
