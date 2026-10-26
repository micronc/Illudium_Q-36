# Deploy Illudium-Q36 on AWS

Use the same image built from the repo `Dockerfile`. Inject secrets at runtime — never bake `ANTHROPIC_API_KEY` into the image.

## 1. Build & push (ECR)

```bash
AWS_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO=illudium-q36

aws ecr create-repository --repository-name "$REPO" --region "$AWS_REGION" || true
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$REPO:latest" .
docker tag "$REPO:latest" "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest"
docker push "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest"
```

## 2. Secrets

Store in **Secrets Manager** or **SSM Parameter Store**:

- `ANTHROPIC_API_KEY`
- `FLASK_SECRET_KEY` (long random string)

Map them into the task/pod as environment variables (same names as `docker-compose.yml`).

## 3. Runtime options

### ECS (Fargate) — simplest Compose-like path

- Task definition: container port **8080**
- Health check: `GET /healthz`
- Env: `PORT=8080`, `HOST=0.0.0.0`, `WEB_CONCURRENCY=1`, secrets as above
- **ALB stickiness** on (cookie) so in-memory chat history stays on one task
- Optional EFS mounts for `/app/intel` and `/app/briefings` if you need durable analyst files across tasks

`deploy/aws/task-definition.example.json` is a starting skeleton.

### EKS

```bash
kubectl create secret generic illudium-secrets \
  --from-literal=ANTHROPIC_API_KEY=... \
  --from-literal=FLASK_SECRET_KEY=...

# Then apply a Deployment + Service using the ECR image,
# envFrom that secret, containerPort 8080, readinessProbe /healthz
```

## 4. Local parity

```bash
ILLUDIUM_IMAGE=illudium-q36:local docker compose up --build
```

Same env contract as ECS/EKS keeps laptop and cloud aligned.
