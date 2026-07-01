# ============================================================
# CRITICAL: logfire MUST be configured before ALL other imports
# so that spans from all modules are captured from the start.
# ============================================================
import logfire
import os
from dotenv import load_dotenv

load_dotenv()
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))

# Now safe to import app modules - logfire is already active
import asyncio
import uuid
from fastapi import FastAPI, Response, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.agents.graph import build_graph
from app.guardrails import initialize_rails, guard
from app.health import router as health_router
from app.config import settings

from pydantic import BaseModel
from typing import Optional

_security = HTTPBearer(auto_error=False)


def _init_rate_limiter():
    """Initialize rate limiting. Use Redis in production; fall back to in-memory storage locally."""
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.extension import _rate_limit_exceeded_handler
    from limits.storage import RedisStorage

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
    Executes the LangGraph RAG flow with memory using a POST request.
    """
    q = body.q
    thread_id = body.thread_id
    request_id = str(uuid.uuid4())

    initial_state = {
        "messages": [{"role": "user", "content": q}],
        "current_query": q,
        "documents": [],
        "plan": ["Start"],
        "status": "Initializing Graph..."
    }

    # Configuration for Memory (Thread ID)
    config = {"configurable": {"thread_id": thread_id}}

    with logfire.span("🔍 /query request", request_id=request_id, thread_id=thread_id):
        try:
            # Gate 1: NeMo Guardrails — blocks off-topic, jailbreaks, and handles dialog
            rail_fired, rail_response = guard(q)
            if rail_fired:
                logfire.info(f"🛡️ Request blocked by guardrails | thread={thread_id}")
                return {
                    "question": q,
                    "answer": rail_response,
                    "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                    "status": "Blocked by guardrails.",
                    "sources": []
                }

            # Gate 2: LangGraph RAG pipeline
            # Run the graph synchronously to preserve Logfire context variables
            final_output = app.state.rag_agent.invoke(initial_state, config=config)

            return {
                "question": q,
                "answer": final_output.get("final_answer"),
                "thought_process": final_output.get("plan"),
                "status": final_output.get("status"),
                "sources": final_output.get("documents", [])
            }
        except Exception as e:
            logfire.error(
                f"❌ Backend Execution Failed: {e}",
                request_id=request_id,
                thread_id=thread_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "question": q,
                    "answer": "I apologize, but I encountered an internal error while processing your request. Please try again later.",
                    "thought_process": ["Error encountered during execution."],
                    "status": "error",
                    "sources": [],
                    "request_id": request_id,
                },
            )
