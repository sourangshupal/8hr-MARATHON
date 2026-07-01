"""Health and readiness checks for the Enterprise RAG API."""

import logfire
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.gateway.client import portkey_client
from app.services.retrieval.qdrant_service import search_enterprise_knowledge

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Liveness probe — returns 200 if the process is running."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    """
    Readiness probe — verifies that critical dependencies are reachable.
    Returns 200 only if Qdrant, the LLM gateway, and Postgres are healthy.
    """
    checks = {}
    healthy = True

    # 1. Qdrant: perform a lightweight search with a dummy vector.
    try:
        search_enterprise_knowledge("health check", limit=1)
        checks["qdrant"] = "ok"
    except Exception as e:
        logfire.warning(f"Readiness check failed for Qdrant: {e}")
        checks["qdrant"] = f"unavailable: {e}"
        healthy = False

    # 2. LLM Gateway: a minimal non-cached completion via Portkey.
    try:
        resp = portkey_client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            temperature=0,
        )
        if resp.choices and resp.choices[0].message.content is not None:
            checks["llm_gateway"] = "ok"
        else:
            raise RuntimeError("empty response")
    except Exception as e:
        logfire.warning(f"Readiness check failed for LLM gateway: {e}")
        checks["llm_gateway"] = f"unavailable: {e}"
        healthy = False

    # 3. Postgres: verify the checkpointer connection is alive.
    try:
        checkpointer = getattr(request.app.state.rag_agent, "checkpointer", None)
        if checkpointer is not None and hasattr(checkpointer, "conn"):
            pool = checkpointer.conn
            conn = pool.getconn()
            conn.execute("SELECT 1")
            pool.putconn(conn)
            checks["postgres"] = "ok"
        else:
            checks["postgres"] = "not_configured"
    except Exception as e:
        logfire.warning(f"Readiness check failed for Postgres: {e}")
        checks["postgres"] = f"unavailable: {e}"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )
