"""Standalone and startup-time connection health checks for all external services.

Run from the command line:
    source .venv/bin/activate
    python -m app.services.health.connection_checker
"""

from __future__ import annotations

import sys
from typing import Callable

import logfire
import requests
from psycopg_pool import ConnectionPool
from qdrant_client import QdrantClient
from redis import Redis

from app.config import settings
from app.gateway.client import portkey_client


class ConnectionResult:
    """Result of a single connectivity check."""

    def __init__(self, name: str, healthy: bool, message: str = ""):
        self.name = name
        self.healthy = healthy
        self.message = message

    def to_dict(self) -> dict[str, object]:
        status = "ok" if self.healthy else "unavailable"
        if self.message:
            status = f"{status}: {self.message}"
        return {"status": status, "healthy": self.healthy, "message": self.message}


def _check_neon_postgres() -> ConnectionResult:
    """Verify Neon Postgres is reachable and accepts queries."""
    pool = None
    conn = None
    try:
        pool = ConnectionPool(
            conninfo=settings.postgres_uri,
            min_size=1,
            max_size=2,
            open=True,
            timeout=5,
            check=ConnectionPool.check_connection,
        )
        conn = pool.getconn(timeout=5)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return ConnectionResult("postgres", True, "Neon Postgres reachable")
    except Exception as e:
        logfire.warning(f"Postgres health check failed: {e}")
        return ConnectionResult("postgres", False, str(e))
    finally:
        if conn is not None and pool is not None:
            try:
                pool.putconn(conn)
            except Exception:
                pass
        if pool is not None:
            try:
                pool.close(timeout=5)
            except Exception:
                pass


def _check_upstash_redis() -> ConnectionResult:
    """Verify Upstash Redis is reachable."""
    try:
        r = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        r.ping()
        return ConnectionResult("redis", True, "Upstash Redis reachable")
    except Exception as e:
        logfire.warning(f"Redis health check failed: {e}")
        return ConnectionResult("redis", False, str(e))


def _check_qdrant() -> ConnectionResult:
    """Verify Qdrant cluster is reachable."""
    try:
        client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=5,
        )
        client.get_collections()
        return ConnectionResult("qdrant", True, "Qdrant reachable")
    except Exception as e:
        logfire.warning(f"Qdrant health check failed: {e}")
        return ConnectionResult("qdrant", False, str(e))


def _check_portkey_gateway() -> ConnectionResult:
    """Verify Portkey LLM gateway responds to a minimal completion."""
    try:
        resp = portkey_client.chat.completions.create(
            model=f"@{settings.PORTKEY_PRIMARY_SLUG}/gpt-5-mini",
            messages=[{"role": "user", "content": "Say hello in one word."}],
            max_completion_tokens=100,
            timeout=10,
        )
        if resp.choices and resp.choices[0].message.content is not None:
            return ConnectionResult("llm_gateway", True, "Portkey gateway reachable")
        raise RuntimeError("empty response")
    except Exception as e:
        logfire.warning(f"LLM gateway health check failed: {e}")
        return ConnectionResult("llm_gateway", False, str(e))


def _check_jina_embeddings() -> ConnectionResult:
    """Verify Jina Embeddings API accepts a probe request."""
    if not settings.JINA_API_KEY:
        return ConnectionResult("jina_embeddings", False, "JINA_API_KEY not set")
    try:
        response = requests.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {settings.JINA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "jina-embeddings-v3",
                "task": "retrieval.query",
                "normalized": True,
                "input": ["probe"],
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("data"):
            raise RuntimeError("empty embedding data")
        return ConnectionResult("jina_embeddings", True, "Jina Embeddings API reachable")
    except Exception as e:
        logfire.warning(f"Jina Embeddings health check failed: {e}")
        return ConnectionResult("jina_embeddings", False, str(e))


def _check_jina_reranker() -> ConnectionResult:
    """Verify Jina Reranker API accepts a probe request."""
    if not settings.JINA_API_KEY:
        return ConnectionResult("jina_reranker", False, "JINA_API_KEY not set")
    try:
        response = requests.post(
            "https://api.jina.ai/v1/rerank",
            headers={
                "Authorization": f"Bearer {settings.JINA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "jina-reranker-v3",
                "query": "health check",
                "documents": ["document one", "document two"],
                "top_n": 2,
                "return_documents": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if "results" not in payload:
            raise RuntimeError("missing results key")
        return ConnectionResult("jina_reranker", True, "Jina Reranker API reachable")
    except Exception as e:
        logfire.warning(f"Jina Reranker health check failed: {e}")
        return ConnectionResult("jina_reranker", False, str(e))


def _check_logfire() -> ConnectionResult:
    """Verify Logfire is configured with a token.

    logfire.configure() already ran at process start; token presence confirms
    the SDK is active. A missing token means all spans are silently dropped.
    """
    if not settings.LOGFIRE_TOKEN:
        return ConnectionResult("logfire", False, "LOGFIRE_TOKEN not set — spans dropped")
    return ConnectionResult("logfire", True, "Logfire configured")


def _check_langsmith() -> ConnectionResult:
    """Verify LangSmith API key is valid and the endpoint is reachable."""
    if not settings.LANGSMITH_API_KEY:
        return ConnectionResult("langsmith", False, "LANGSMITH_API_KEY not set — tracing disabled")
    try:
        response = requests.get(
            f"{settings.LANGSMITH_ENDPOINT}/ok",
            headers={"x-api-key": settings.LANGSMITH_API_KEY},
            timeout=5,
        )
        response.raise_for_status()
        return ConnectionResult("langsmith", True, f"LangSmith reachable (project: {settings.LANGSMITH_PROJECT})")
    except Exception as e:
        logfire.warning(f"LangSmith health check failed: {e}")
        return ConnectionResult("langsmith", False, str(e))


# Ordered list of all checks to run during startup and /ready.
_CHECKERS: list[Callable[[], ConnectionResult]] = [
    _check_neon_postgres,
    _check_upstash_redis,
    _check_qdrant,
    _check_portkey_gateway,
    _check_jina_embeddings,
    _check_jina_reranker,
    _check_logfire,
    _check_langsmith,
]


def check_all_connections() -> dict[str, ConnectionResult]:
    """Run all connection checks and return a map of service name to result."""
    results: dict[str, ConnectionResult] = {}
    for checker in _CHECKERS:
        result = checker()
        results[result.name] = result
    return results


def log_connection_summary(results: dict[str, ConnectionResult]) -> bool:
    """Log a human-readable summary. Returns True if all checks passed."""
    healthy = all(r.healthy for r in results.values())
    for name, result in results.items():
        icon = "✅" if result.healthy else "❌"
        logfire.info(f"{icon} {name}: {result.message or result.to_dict()['status']}")
    if healthy:
        logfire.info("🟢 All external connections healthy.")
    else:
        logfire.warning("🟠 Some external connections are unavailable.")
    return healthy


def _print_cli_report(results: dict[str, ConnectionResult]) -> int:
    """Print a CLI report and return an exit code."""
    healthy = True
    print("\nExternal Connection Health Report")
    print("=" * 50)
    for name, result in results.items():
        status = "OK" if result.healthy else "FAIL"
        print(f"{status:4} {name:20} {result.message}")
        if not result.healthy:
            healthy = False
    print("=" * 50)
    if healthy:
        print("All connections healthy.")
        return 0
    print("One or more connections failed.")
    return 1


if __name__ == "__main__":
    sys.exit(_print_cli_report(check_all_connections()))
