# ============================================================
# CRITICAL: logfire MUST be configured before ALL other imports
# so that spans from all modules are captured from the start.
# ============================================================
import os

import logfire
from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))

# Now safe to import app modules - logfire is already active
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.agents.graph import build_graph
from app.config import settings
from app.guardrails import guard, initialize_rails
from app.health import router as health_router
from app.logging import set_request_id
from app.tasks import run_rag_pipeline

# Custom Prometheus metrics
RAG_REQUESTS_TOTAL = Counter(
    "rag_requests_total",
    "Total number of /query requests",
    ["status"],
)
RAG_REQUEST_DURATION = Histogram(
    "rag_request_duration_seconds",
    "Latency of /query requests in seconds",
)
GUARDRAILS_BLOCKS_TOTAL = Counter(
    "guardrails_blocks_total",
    "Number of requests blocked or allowed by guardrails",
    ["blocked"],
)

_security = HTTPBearer(auto_error=False)


def _init_rate_limiter():
    """Initialize rate limiting. Use Redis in production; fall back to in-memory storage locally."""
    from limits.storage import RedisStorage
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.extension import _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address

    try:
        storage = RedisStorage(settings.REDIS_URL)
        storage.check()  # raises if Redis is unreachable
        app.state.limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL)
        app.state.rate_limiter_storage = "redis"
        logfire.info("🚦 Rate limiting initialized via Redis.")
    except Exception as e:
        app.state.limiter = Limiter(key_func=get_remote_address)
        app.state.rate_limiter_storage = "memory"
        logfire.warning(f"⚠️ Redis unavailable ({e}); using in-memory rate limiting.")

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return True


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    """
    Require a valid bearer token when RAG_API_KEY is configured.
    In development, omit RAG_API_KEY to disable authentication.
    """
    if not settings.API_KEY:
        # Development mode: no API key required.
        return None

    if not credentials or credentials.credentials != settings.API_KEY:
        logfire.warning("🔒 Unauthorized /query request: invalid or missing API key.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def _get_limiter_rule(times: int, seconds: int) -> str:
    """Convert times/seconds into a slowapi limit string, e.g. '20/minute'."""
    if seconds % 60 == 0:
        return f"{times}/{seconds // 60}minute"
    if seconds % 3600 == 0:
        return f"{times}/{seconds // 3600}hour"
    return f"{times}/{seconds}second"


class _AppLimiter:
    """
    Thin wrapper around the Limiter instance that is initialized at startup.
    Allows routes to be decorated at import time while the real limiter
    (Redis-backed or in-memory) is configured in startup_event.
    """
    def limit(self, rule_or_callable):
        def decorator(func):
            import functools

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                limiter = getattr(app.state, "limiter", None)
                if limiter is None:
                    return func(*args, **kwargs)

                rule = rule_or_callable() if callable(rule_or_callable) else rule_or_callable
                # Build the slowapi wrapper at request time so the limiter
                # instance and storage backend are always current.
                return limiter.limit(rule)(func)(*args, **kwargs)

            return wrapper
        return decorator


app_limiter = _AppLimiter()


def rate_limit(times: int = None, seconds: int = None):
    """
    Decorator factory that applies slowapi rate limiting using the limiter
    initialized at startup. Falls back to a no-op if the limiter is missing.
    The rule is resolved at request time so settings can be overridden in tests.
    """
    def _resolve_rule() -> str:
        t = times or settings.RATE_LIMIT_PER_MINUTE
        s = seconds or 60
        return _get_limiter_rule(t, s)

    return app_limiter.limit(_resolve_rule)


# Initialize FastAPI
app = FastAPI(title="Enterprise Agentic RAG API")
app.include_router(health_router)

# Expose Prometheus metrics at /metrics with default request instrumentation.
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.on_event("startup")
def startup_event():
    initialize_rails()

    # Build the agent graph with the production checkpointer (Postgres by default).
    app.state.rag_agent = build_graph()

    # Ensure Postgres checkpointer tables exist.
    checkpointer = getattr(app.state.rag_agent, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "setup"):
        try:
            checkpointer.setup()
            logfire.info("🗄️ Postgres checkpointer tables initialized.")
        except Exception as e:
            logfire.error(f"❌ Failed to initialize Postgres checkpointer: {e}")

    app.state.rate_limiter_enabled = _init_rate_limiter()

    if not settings.API_KEY:
        logfire.warning("🔓 RAG_API_KEY is not set — /query is open to anyone. Set it in production.")

class QueryRequest(BaseModel):
    q: str
    thread_id: Optional[str] = "default_user"


@app.get("/")
def home():
    return {"message": "Enterprise LangGraph RAG API is live."}


@app.get("/graph")
def get_graph_image(_api_key: str = Depends(verify_api_key)):
    """
    Returns the Mermaid image of the agent's workflow.
    """
    try:
        png_bytes = app.state.rag_agent.get_graph().draw_mermaid_png()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return {"error": f"Could not generate graph image: {e}"}


@app.post("/query")
@rate_limit()
def query(
    request: Request,
    body: QueryRequest,
    _api_key: str = Depends(verify_api_key),
):
    """
    Enqueues the LangGraph RAG pipeline as a background Celery task.
    Returns immediately with a job_id that can be polled via /query/status/{job_id}.
    """
    q = body.q
    thread_id = body.thread_id
    request_id = str(uuid.uuid4())
    set_request_id(request_id)

    start = time.perf_counter()
    with logfire.span("🔍 /query enqueue", request_id=request_id, thread_id=thread_id):
        # Gate: run guardrails synchronously so blocked requests never hit the queue.
        rail_fired, rail_response = guard(q)
        if rail_fired:
            GUARDRAILS_BLOCKS_TOTAL.labels(blocked="true").inc()
            RAG_REQUESTS_TOTAL.labels(status="blocked").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.info("🛡️ Request blocked by guardrails", request_id=request_id, thread_id=thread_id)
            return {
                "question": q,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": [],
            }

        GUARDRAILS_BLOCKS_TOTAL.labels(blocked="false").inc()

        try:
            task = run_rag_pipeline.delay(q, thread_id, rag_request_id=request_id)
            RAG_REQUESTS_TOTAL.labels(status="queued").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.info(
                "📨 RAG pipeline enqueued",
                job_id=task.id,
                request_id=request_id,
                thread_id=thread_id,
            )
            return {
                "job_id": task.id,
                "request_id": request_id,
                "status": "queued",
                "poll_url": f"/query/status/{task.id}",
            }
        except Exception as e:
            RAG_REQUESTS_TOTAL.labels(status="error").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.error(
                f"❌ Failed to enqueue RAG pipeline: {e}",
                request_id=request_id,
                thread_id=thread_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "request_id": request_id,
                    "status": "error",
                    "message": "Failed to enqueue request. Please try again later.",
                },
            )


@app.get("/query/status/{job_id}")
def query_status(job_id: str):
    """
    Poll the status/result of an async RAG query job.
    """
    from celery.result import AsyncResult

    request_id = str(uuid.uuid4())
    set_request_id(request_id)

    with logfire.span("🔍 /query/status", job_id=job_id, request_id=request_id):
        result = AsyncResult(job_id, app=run_rag_pipeline.app)
        response = {
            "job_id": job_id,
            "request_id": request_id,
            "status": result.status,
        }

        if result.ready():
            if result.successful():
                response["result"] = result.get()
                logfire.info("✅ Job result returned", job_id=job_id, request_id=request_id)
            else:
                response["error"] = str(result.result)
                logfire.warning("❌ Job failed", job_id=job_id, request_id=request_id, error=response["error"])

    return response
