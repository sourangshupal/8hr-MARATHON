"""Health and readiness checks for the Enterprise RAG API."""

import logfire
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.health.connection_checker import check_all_connections

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Liveness probe — returns 200 if the process is running."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    """
    Readiness probe — verifies that critical external dependencies are reachable.
    Returns 200 only if Postgres, Redis, Qdrant, the LLM gateway, Jina Embeddings,
    and Jina Reranker are all healthy.
    """
    results = check_all_connections()
    checks = {name: result.to_dict()["status"] for name, result in results.items()}
    healthy = all(r.healthy for r in results.values())

    if not healthy:
        logfire.warning("Readiness check failed", checks=checks)

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if healthy else "not_ready", "checks": checks},
    )
