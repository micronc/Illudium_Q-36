# Deploy Illudium-Q36 on GCP

Same container image as local Compose / AWS. Inject secrets from **Secret Manager**; do not bake keys into the image.

## 1. Build & push (Artifact Registry)

```bash
PROJECT_ID=your-gcp-project
REGION=us-central1
REPO=illudium
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/illudium-q36:latest"

gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION" || true

gcloud auth configure-docker "$REGION-docker.pkg.dev"
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

## 2. Secrets

```bash
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
openssl rand -hex 32 | gcloud secrets create flask-secret-key --data-file=-
```

Grant the Cloud Run / GKE runtime service account `roles/secretmanager.secretAccessor`.

## 3. Runtime options

### Cloud Run (fastest)

```bash
gcloud run deploy illudium-q36 \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --port=8080 \
  --allow-unauthenticated \
  --set-env-vars="HOST=0.0.0.0,PORT=8080,DEMO_MODE=false,WEB_CONCURRENCY=1" \
  --session-affinity \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest,FLASK_SECRET_KEY=flask-secret-key:latest"
```

Cloud Run expects process listen on `$PORT` (defaults to 8080 in our image).

For durable `intel/` / `briefings/`, attach a GCS FUSE sidecar or move object storage later; ephemeral disk is fine for demos.

### GKE

```bash
kubectl create secret generic illudium-secrets \
  --from-literal=ANTHROPIC_API_KEY=... \
  --from-literal=FLASK_SECRET_KEY=...

# Deployment: image from Artifact Registry, containerPort 8080,
# envFrom secret, readinessProbe httpGet /healthz
```

`deploy/gcp/cloudrun.example.yaml` is a deployable skeleton for `gcloud run services replace`.

## 4. Local parity

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

Env var names match Cloud Run / GKE so the same Compose contract transfers.
