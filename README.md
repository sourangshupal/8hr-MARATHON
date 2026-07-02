# Enterprise Agentic RAG (Scalable Pipeline)

A production-grade, enterprise-level RAG system built with **LangGraph**, **Portkey LLM Gateway**, **OpenAI**, and **Jina AI Embeddings/Reranker**. The system distinguishes between technical "True Data" and random "Noisy Data" using semantic re-ranking, history-aware planning, and NeMo Guardrails for input/output safety.

## Key Features

- **Agentic Intelligence**: LangGraph for cyclic reasoning, multi-step planning, and conversation memory.
- **Guardrails**: NeMo Guardrails gate blocks off-topic, jailbreak, and injection inputs before any retrieval.
- **LLM Gateway**: Portkey routes all LLM calls with automatic fallback between OpenAI and Anthropic via your configured Portkey virtual providers.
- **Enterprise Search**: Qdrant Cloud for high-performance vector search + Jina AI Reranker API for semantic reranking.
- **Jina AI Embeddings**: `jina-embeddings-v3` (1024-dim) via Jina API, with local `mxbai-embed-large-v1` fallback.
- **Local Document Parsing**: PDF, HTML, TXT, DOCX, PPTX parsed entirely on-device — no external OCR service.
- **Observability**: Full trace nesting with **Pydantic Logfire** and **LangSmith** across every agent node.
- **Metrics**: Prometheus `/metrics` endpoint with custom RAG, guardrails, and Celery counters.
- **Async Job Queue**: `/query` enqueues the LangGraph pipeline to Celery/Redis and returns a `job_id`; clients poll `/query/status/{job_id}`.
- **API Key & Rate Limiting**: Optional bearer-token auth and Redis-backed (or in-memory) rate limiting.
- **Evaluation Suite**: RAGAS-powered eval pipeline (6 metrics) with a dedicated Streamlit demo app and a headless `evals/run_evals.py` script.

---

## Agent Intelligence Flow

```mermaid
graph TD
    User((User)) --> UI[Streamlit UI]
    UI --> API[FastAPI /query]
    API --> Guard{NeMo Guardrails}
    Guard -->|Blocked| UI
    Guard -->|Pass| Planner{Planner Node}
    Planner -->|Conversational| Responder[Responder Node]
    Planner -->|Technical| Retriever[Retriever Node]
    Retriever --> Reranker[Jina AI Reranker API]
    Reranker --> Responder
    Responder --> UI
    Responder -.-> Memory[(LangGraph MemorySaver)]
```

---

## Project Structure

```text
├── app/
│   ├── agents/
│   │   └── nodes/       # Planner, Retriever, Responder LangGraph nodes
│   ├── gateway/         # Portkey LLM gateway — primary + fallback Groq routing
│   ├── guardrails/      # NeMo Guardrails input/output filtering
│   ├── ingestion/
│   │   ├── chunking/    # Paragraph-based text splitter (1500 char max)
│   │   └── loaders/     # Local parsers — PDF (pypdf), HTML, TXT, DOCX, PPTX
│   ├── services/
│   │   └── retrieval/   # Jina AI embeddings + Qdrant search + Jina AI reranking
│   ├── config.py        # Centralized environment variable management
│   └── main.py          # FastAPI entrypoint — guardrails gate + /query endpoint
├── evals/               # RAGAS evaluation suite + Streamlit 3-tab demo
├── ui/                  # Streamlit chat interface with reasoning step transparency
├── processed_data/      # Auto-generated — parsed & chunked JSON output per document
├── DOCS/                # Architectural and operational guides
├── DATA/                # Sample datasets (True vs Noisy documentation)
├── Dockerfile           # Container definition (retained for reference)
└── requirements.txt     # Pinned dependencies
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | LangChain + LangGraph |
| LLMs | OpenAI `gpt-5-mini` + Anthropic fallback via **Portkey** gateway |
| Guardrails | NeMo Guardrails |
| Vector DB | Qdrant Cloud |
| Reranking | Jina AI Reranker API (`jina-reranker-v3`) |
| Embeddings | Jina AI `jina-embeddings-v3` (1024-dim) + local mxbai fallback |
| Document Parsing | pypdf + pdfplumber (local, no OCR service) |
| Observability | Pydantic Logfire + LangSmith |
| Evaluation | RAGAS + custom Tool Correctness (Jaccard) |

---

## Getting Started

### 1. Install dependencies

```powershell
python -m venv tenvv
.\tenvv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file with the following keys:

```env
# OpenAI LLM
OPENAI_API_KEY = "..."

# LLM Gateway
PORTKEY_API_KEY = "..."

# Jina AI Embeddings + Reranker API
JINA_API_KEY = "..."

# Vector DB
QDRANT_API_KEY = "..."
QDRANT_CLUSTER_ENDPOINT = "https://your-cluster.cloud.qdrant.io:6333"

# Production persistence (Neon) & cache (Upstash Redis)
NEON_DB_URL = "postgresql://user:password@host.neon.tech/enterprise_rag?sslmode=require"
UPSTASH_REDIS_REST_URL = "https://your-db.upstash.io"
UPSTASH_REDIS_REST_TOKEN = "your-upstash-token"

# API safety
RAG_API_KEY = ""                       # set in production to require bearer auth
RATE_LIMIT_PER_MINUTE = 20

# Observability
LOGFIRE_TOKEN = "..."
LANGSMITH_API_KEY = "..."
LANGSMITH_PROJECT = "enterprise_rag"
LANGSMITH_TRACING = true
LANGSMITH_ENDPOINT = https://api.smith.langchain.com

# Evals
JUDGE_OPENAI_API_KEY = "..."

# Backend (for Streamlit UI)
BACKEND_URL = "http://localhost:8000"
```

### 3. Run data ingestion

Parses all documents in `DATA/`, chunks them, saves metadata to `processed_data/`, and indexes vectors into Qdrant.

```powershell
python -m app.ingestion.processor DATA --wipe
```

> Pass `--wipe` to drop and recreate the Qdrant collection. Omit it to append to an existing collection.

### 4. Launch the app

The `/query` endpoint is now asynchronous: it enqueues work to Celery (backed by Upstash Redis) and returns a `job_id`.
You need a Celery worker, the FastAPI server, and (optionally) the Streamlit UI. Redis and Postgres are managed by Upstash and Neon; no local persistence services are required.

```powershell
# Terminal 1 — Celery worker
# On macOS, prefork can crash due to Objective-C fork-safety checks.
# Use OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES or --pool=solo for local dev.
celery -A app.tasks worker --loglevel=info -Q celery

# Terminal 2 — FastAPI backend
uvicorn app.main:app --reload --port 8000

# Terminal 3 — Streamlit UI
streamlit run ui/app.py
```

### 5. Query the API

```powershell
curl -X POST "http://localhost:8000/query" `
  -H "Content-Type: application/json" `
  -d '{"q": "How do I start Redis for a Kubernetes work queue?", "thread_id": "user-1"}'

# Response: {"job_id": "...", "status": "queued", "poll_url": "/query/status/..."}

curl "http://localhost:8000/query/status/<job_id>"
```

### 6. Run the eval suite

```powershell
# Headless CLI runner (requires backend on :8000)
python -m evals.run_evals

# Or use the Streamlit demo
streamlit run evals/app.py
```

### 7. Run tests locally

```powershell
# Lint + format checks
ruff check app tests evals
ruff format --check app tests evals

# Unit tests
$env:LOGFIRE_IGNORE_NO_CONFIG=1
pytest tests/
```

---

## Documentation Index

| # | Guide | What it covers |
|---|-------|---------------|
| 1 | [System Overview](DOCS/01_SYSTEM_OVERVIEW.md) | High-level vision and end-to-end flow |
| 2 | [Ingestion Engine](DOCS/02_INGESTION_ENGINE.md) | Document parsing and indexing pipeline |
| 3 | [Node Intelligence](DOCS/03_NODE_INTELLIGENCE.md) | Planner, Retriever, Responder internals |
| 4 | [Observability](DOCS/04_TRACING_AND_OBSERVABILITY.md) | Logfire + LangSmith tracing |
| 5 | [FlashRank Reranking](DOCS/13_FLASHRANK_RERANKING.md) | Local semantic reranker deep-dive |
| 6 | [Guardrails](DOCS/15_GUARDRAILS.md) | NeMo Guardrails implementation |
| 7 | [LLM Gateway](DOCS/16_LLM_GATEWAY.md) | Portkey routing, fallback, and observability |
| 8 | [Evals](DOCS/17_EVALS.md) | RAGAS metrics theory and token budget |
| 9 | [Evals Pipeline](DOCS/18_EVALS_PIPELINE.md) | Live eval pipeline and Streamlit demo |

---

*Built for High-Scale Enterprise Document Intelligence.*
