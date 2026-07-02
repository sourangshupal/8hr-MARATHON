# AWS ECS Fargate Deployment Plan — Enterprise Agentic RAG

> **Approved approach:** Option A — Managed Qdrant with Fargate-hosted API, Worker, and optional UI.  
> This plan keeps all stateful services (Redis via Upstash, Postgres via Neon, Qdrant) outside of Fargate for simpler operations and reliable auto-scaling.

---

## 1. Objective

Deploy the Enterprise Agentic RAG application on AWS using **Amazon ECS on Fargate** in a microservices architecture, with:

- Separate scaling for synchronous API traffic and asynchronous RAG jobs
- Managed persistence for Postgres (Neon), Redis (Upstash), and Qdrant (Qdrant Cloud)
- Auto-scaling policies for each compute service
- A local `docker-compose.yml` for pre-cloud validation
- CI/CD via GitHub Actions → Amazon ECR → ECS

---

## 2. Architecture Overview

### 2.1 Fargate Services (stateless)

| Service | Container Command | Responsibility |
|---|---|---|
| **rag-api** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` | Public HTTP API (`/query`, `/health`, `/ready`, `/metrics`, `/graph`) |
| **rag-worker** | `celery -A app.tasks worker --loglevel=info -Q celery -c 4` | Async LangGraph RAG pipeline execution |
| **rag-ui** (optional) | `streamlit run ui/app.py --server.port 8501` | End-user chat interface |

All three services use the **same Docker image** from Amazon ECR. Only the command differs.

### 2.2 Managed Services (stateful)

| Component | AWS Service | Purpose |
|---|---|---|
| **Redis** | Upstash Redis (managed) | Celery broker/backend + FastAPI rate-limit store |
| **Postgres** | Neon (managed PostgreSQL) | LangGraph checkpointer (conversation memory) |
| **Qdrant** | Qdrant Cloud managed service | Vector database for retrieval |
| **Secrets** | AWS Secrets Manager | API keys, DB URIs, Redis URL |
| **Ingress** | Application Load Balancer | Public HTTPS access to `rag-api` and `rag-ui` |
| **Observability** | Amazon CloudWatch Logs + Metrics, optional Managed Prometheus | Logs, custom dashboards, `/metrics` scraping |
| **CI/CD** | GitHub Actions + Amazon ECR + ECS | Build image, push, and deploy |

---

## 3. Why Option A (Managed Qdrant)?

**Selected:** Qdrant Cloud as a managed vector database.

- No persistent state inside Fargate tasks
- Qdrant Cloud handles backups, HA, and scaling
- ECS services remain stateless and easy to auto-scale
- Fargate + EFS for Qdrant (Option B) is possible but adds operational complexity, latency, and limits horizontal scaling

---

## 4. Networking

1. **VPC** with public and private subnets across at least 2 Availability Zones.
2. **Public subnets:** ALB, NAT Gateways.
3. **Private subnets:** Fargate tasks only (Neon, Upstash, and Qdrant Cloud are accessed over the public internet via NAT Gateway).
4. **Security Groups:**

| Security Group | Inbound | Outbound |
|---|---|---|
| `alb-sg` | 80/443 from internet | To `api-sg` and `ui-sg` |
| `api-sg` | 8080 from `alb-sg` | Public internet for Neon, Upstash, Qdrant Cloud, and LLM APIs |
| `ui-sg` | 8501 from `alb-sg` | `api-sg` (8080) |
| `worker-sg` | None (private) | Public internet for Neon, Upstash, Qdrant Cloud, and LLM APIs |

---

## 5. Secrets & Environment Variables

Store all sensitive values in **AWS Secrets Manager** and inject them into task definitions.

### Secrets (Secrets Manager)

- `NEON_DB_URL`
- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`
- `QDRANT_URL`
- `QDRANT_API_KEY`
- `OPENAI_API_KEY`
- `JINA_API_KEY`
- `PORTKEY_API_KEY`
- `RAG_API_KEY` (production auth)
- `LOGFIRE_TOKEN`
- `LANGSMITH_API_KEY`
- `JUDGE_OPENAI_API_KEY` (optional) — Dedicated OpenAI key for RAGAS eval judge. Falls back to `OPENAI_API_KEY` if omitted; not required in the ECS API/worker task definitions.

### Plain environment variables

- `QDRANT_COLLECTION=enterprise_rag`
- `RATE_LIMIT_PER_MINUTE=60`
- `PYTHONUNBUFFERED=1`

---

## 6. Container Image

A single Docker image is built and pushed to Amazon ECR.

- **Repository:** `enterprise-rag`
- **Tags:** `git-sha` and `latest`
- **Lifecycle policy:** keep last 30 images

### Dockerfile production hardening

The existing `Dockerfile` is already close. Add the following for production:

```dockerfile
EXPOSE 8080
RUN useradd -m appuser && chown -R appuser /app
USER appuser
```

This runs the container as a non-root user.

---

## 7. ECS Task Definitions

### 7.1 rag-api

```json
{
  "family": "rag-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "ecsTaskExecutionRole",
  "taskRoleArn": "rag-api-task-role",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "<ecr>/enterprise-rag:<git-sha>",
      "command": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"],
      "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
      "environment": [
        {"name": "QDRANT_COLLECTION", "value": "enterprise_rag"},
        {"name": "RATE_LIMIT_PER_MINUTE", "value": "60"},
        {"name": "PYTHONUNBUFFERED", "value": "1"}
      ],
      "secrets": [
        {"name": "NEON_DB_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/neon-db-url"},
        {"name": "UPSTASH_REDIS_REST_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/upstash-redis-rest-url"},
        {"name": "UPSTASH_REDIS_REST_TOKEN", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/upstash-redis-rest-token"},
        {"name": "QDRANT_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/qdrant-url"},
        {"name": "QDRANT_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/qdrant-api-key"},
        {"name": "OPENAI_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/openai-api-key"},
        {"name": "JINA_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/jina-api-key"},
        {"name": "PORTKEY_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/portkey-api-key"},
        {"name": "RAG_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/rag-api-key"},
        {"name": "LOGFIRE_TOKEN", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/logfire-token"},
        {"name": "LANGSMITH_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/langsmith-api-key"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/rag-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "api"
        }
      }
    }
  ]
}
```

### 7.2 rag-worker

Uses the same image, but with larger CPU/memory because it runs embeddings, reranking, and LLM calls.

```json
{
  "family": "rag-worker",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "ecsTaskExecutionRole",
  "taskRoleArn": "rag-worker-task-role",
  "containerDefinitions": [
    {
      "name": "worker",
      "image": "<ecr>/enterprise-rag:<git-sha>",
      "command": ["celery", "-A", "app.tasks", "worker", "--loglevel=info", "-Q", "celery", "-c", "4"],
      "environment": [
        {"name": "QDRANT_COLLECTION", "value": "enterprise_rag"},
        {"name": "PYTHONUNBUFFERED", "value": "1"}
      ],
      "secrets": [
        {"name": "NEON_DB_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/neon-db-url"},
        {"name": "UPSTASH_REDIS_REST_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/upstash-redis-rest-url"},
        {"name": "UPSTASH_REDIS_REST_TOKEN", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/upstash-redis-rest-token"},
        {"name": "QDRANT_URL", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/qdrant-url"},
        {"name": "QDRANT_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/qdrant-api-key"},
        {"name": "OPENAI_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/openai-api-key"},
        {"name": "JINA_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/jina-api-key"},
        {"name": "PORTKEY_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/portkey-api-key"},
        {"name": "RAG_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/rag-api-key"},
        {"name": "LOGFIRE_TOKEN", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/logfire-token"},
        {"name": "LANGSMITH_API_KEY", "valueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:rag/langsmith-api-key"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/rag-worker",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "worker"
        }
      }
    }
  ]
}
```

### 7.3 rag-ui (optional)

```json
{
  "family": "rag-ui",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "ecsTaskExecutionRole",
  "taskRoleArn": "rag-ui-task-role",
  "containerDefinitions": [
    {
      "name": "ui",
      "image": "<ecr>/enterprise-rag:<git-sha>",
      "command": ["streamlit", "run", "ui/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"],
      "portMappings": [{"containerPort": 8501, "protocol": "tcp"}],
      "environment": [
        {"name": "API_BASE_URL", "value": "https://api.yourdomain.com"},
        {"name": "PYTHONUNBUFFERED", "value": "1"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/rag-ui",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ui"
        }
      }
    }
  ]
}
```

---

## 8. Auto-Scaling

### 8.1 rag-api

Target tracking policies:

- **ALB Request Count Per Target** > 1000
- **CPU Utilization** > 70%
- **Memory Utilization** > 70%

**Scale:** min 2, max 10 tasks.

### 8.2 rag-worker

Celery uses Redis via Upstash. Upstash manages the Redis cluster, so no Redis-specific scaling infrastructure is required.

**Option A — Custom CloudWatch metric (recommended):**

- Deploy a small Lambda or sidecar that periodically publishes `CeleryQueueLength` to CloudWatch.
- Scale the worker service on that metric.

**Option B — Switch Celery broker to Amazon SQS:**

- This is outside the scope of this plan. If you choose SQS later, change the Celery broker URL and use the native `ApproximateNumberOfMessagesVisible` metric for target tracking.

**Scale:** min 1, max 20 tasks.

### 8.3 rag-ui

If the UI is public:

- Scale on ALB request count or CPU utilization
- **Scale:** min 1, max 4 tasks

If the UI is internal-only, run a fixed count of 1.

---

## 9. Local `docker-compose.yml`

Use this file to validate the full stack locally before deploying to AWS.

```yaml
version: "3.8"

services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-data:/qdrant/storage
    environment:
      QDRANT__SERVICE__GRPC_PORT: 6334

  api:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8080"
    environment:
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ""
      QDRANT_COLLECTION: enterprise_rag
      RATE_LIMIT_PER_MINUTE: "60"
      RAG_API_KEY: ""
    env_file:
      - .env
    depends_on:
      - qdrant
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

  worker:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ""
      QDRANT_COLLECTION: enterprise_rag
      RAG_API_KEY: ""
    env_file:
      - .env
    depends_on:
      - qdrant
    command: ["celery", "-A", "app.tasks", "worker", "--loglevel=info", "-Q", "celery", "-c", "2"]

  ui:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8501:8501"
    environment:
      API_BASE_URL: http://api:8080
    depends_on:
      - api
    command: ["streamlit", "run", "ui/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]

volumes:
  qdrant-data:
```

Run locally:

```bash
docker compose up --build
```

Test:

```bash
curl http://localhost:8000/health
```

---

## 10. CI/CD Pipeline

The repository now contains two workflows:

- `.github/workflows/ci.yml` — linting and unit tests.
- `.github/workflows/cd.yml` — build image, push to ECR, and deploy to ECS (triggered after CI succeeds on `main` or `deployment`).

The CD workflow uses `aws-actions/amazon-ecs-deploy-task-definition` with rendered task-definition JSON files stored in `.aws/task-definitions/`.

### GitHub Secrets Required

Set these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS IAM access key with ECS, ECR, and Secrets Manager permissions |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret key |
| `AWS_REGION` | Target AWS region (e.g., `us-east-1`) |
| `ECR_REPOSITORY` | ECR repository name (default: `enterprise-rag`) |
| `ECS_CLUSTER` | ECS cluster name (default: `rag-cluster`) |
| `ECS_SERVICE_API` | ECS service name for API (default: `rag-api`) |
| `ECS_SERVICE_WORKER` | ECS service name for worker (default: `rag-worker`) |
| `ECS_SERVICE_UI` | ECS service name for UI (default: `rag-ui`) |
| `NEON_DB_URL_ARN` | Secrets Manager ARN for `NEON_DB_URL` |
| `UPSTASH_REDIS_REST_URL_ARN` | Secrets Manager ARN for `UPSTASH_REDIS_REST_URL` |
| `UPSTASH_REDIS_REST_TOKEN_ARN` | Secrets Manager ARN for `UPSTASH_REDIS_REST_TOKEN` |
| `QDRANT_URL_ARN` | Secrets Manager ARN for `QDRANT_URL` |
| `QDRANT_API_KEY_ARN` | Secrets Manager ARN for `QDRANT_API_KEY` |
| `OPENAI_API_KEY_ARN` | Secrets Manager ARN for `OPENAI_API_KEY` |
| `JINA_API_KEY_ARN` | Secrets Manager ARN for `JINA_API_KEY` |
| `PORTKEY_API_KEY_ARN` | Secrets Manager ARN for `PORTKEY_API_KEY` |
| `RAG_API_KEY_ARN` | Secrets Manager ARN for `RAG_API_KEY` |
| `LOGFIRE_TOKEN_ARN` | Secrets Manager ARN for `LOGFIRE_TOKEN` |
| `LANGSMITH_API_KEY_ARN` | Secrets Manager ARN for `LANGSMITH_API_KEY` |

### What the CD workflow does

1. Waits for the `CI` workflow to succeed on `main` or `deployment`.
2. Logs in to Amazon ECR.
3. Builds the Docker image tagged with the commit SHA and `latest`.
4. Pushes both tags to ECR.
5. Renders task-definition templates from `.aws/task-definitions/` by substituting placeholders with image URIs and secret ARNs.
6. Deploys `rag-api`, `rag-worker`, and optionally `rag-ui` to ECS.
7. Waits for each service to reach a stable state before continuing.

### Files added for CD

```text
.github/workflows/cd.yml
.aws/task-definitions/rag-api.json
.aws/task-definitions/rag-worker.json
.aws/task-definitions/rag-ui.json
```

---

## 11. Data Ingestion in Production

Do **not** run ingestion as a long-running ECS service. Use one of these patterns:

1. **Fargate one-off task:** run `python -m app.ingestion.processor s3://bucket/data --wipe` via ECS `RunTask`.
2. **AWS Batch:** for large or scheduled ingestion jobs.
3. **GitHub Actions:** for small static datasets, run ingestion as a CI/CD step after deployment.

If using S3, download files into the task's ephemeral storage before running the processor.

---

## 12. Monitoring & Alerting

1. **CloudWatch Logs:** all services log to `/ecs/<service>` log groups.
2. **CloudWatch Alarms:**
   - `rag-api` 5xx error rate > 1%
   - `rag-worker` task failures > threshold
3. **Prometheus:** scrape `/metrics` from `rag-api`. Use Amazon Managed Prometheus or a self-hosted Prometheus sidecar.
4. **Custom dashboard metrics:**
   - Celery queue length
   - `/query` p50/p95 latency
   - Guardrails block rate
   - RAG answer token count
5. **Logfire & LangSmith:** continue using existing integrations for distributed tracing and agent step tracing.

---

## 13. Cost & Operational Notes

- **Fargate** is easy to operate but more expensive per vCPU than EC2. For steady high throughput, consider EC2-backed ECS or EKS.
- **Neon** and **Upstash** are managed services that remove operational overhead for Postgres and Redis; pricing is usage-based.
- **Qdrant Cloud** is the simplest vector DB option. Only self-host Qdrant if data residency requirements demand it.
- Keep `requirements-prod.txt` lean. Do not include `streamlit`, `ragas`, `sentence-transformers`, or `deepeval` in the production image unless required.

---

## 14. Deployment Sequence

1. Create VPC, public/private subnets, IGW, NAT Gateways, and security groups.
2. Sign up for Neon PostgreSQL and Upstash Redis; copy connection strings.
3. Sign up for Qdrant Cloud and create the `enterprise_rag` collection.
4. Create ECR repository and push the Docker image.
5. Create Secrets Manager entries for all environment variables.
6. Create IAM roles: `ecsTaskExecutionRole` and task-specific roles.
7. Create the ECS cluster.
8. Register task definitions for `rag-api`, `rag-worker`, and optional `rag-ui`.
9. Create Application Load Balancer and target groups.
10. Create ECS services with initial desired counts.
11. Configure auto-scaling policies.
12. Verify endpoints:
    - `GET /health`
    - `GET /ready`
    - `POST /query`
    - `GET /query/status/{job_id}`
    - `GET /metrics`
13. Run ingestion job and validate RAG answers.
14. Enable CloudWatch alarms and dashboards.

---

## 15. Alternative: Option B — Self-hosted Qdrant

If you later decide you need full data residency, you can add a `rag-qdrant` Fargate service using the official `qdrant/qdrant` image with an Amazon EFS volume mounted at `/qdrant/storage`.

Trade-offs:

- **Pros:** data stays in your AWS account; no third-party dependency
- **Cons:** you manage backups, HA, and scaling; EFS latency can affect query performance; horizontal scaling requires Qdrant clustering

For most production workloads, **Qdrant Cloud (Option A)** is the better choice.
