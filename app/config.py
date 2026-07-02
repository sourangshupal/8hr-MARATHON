"""Centralized, Pydantic-validated application settings."""

import os
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load and validate environment variables from `.env`.

    - `extra="ignore"` lets `.env` keep legacy keys (`POSTGRES_URI`, `REDIS_URL`,
      old Groq keys, etc.) without failing startup.
    - Required fields raise a clear validation error at import time if missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- JINA AI (embeddings + reranker) ---
    JINA_API_KEY: str

    # --- OPENAI LLM ---
    OPENAI_API_KEY: str
    JUDGE_OPENAI_API_KEY: str | None = None

    # --- PORTKEY LLM GATEWAY ---
    PORTKEY_API_KEY: str
    PORTKEY_PRIMARY_SLUG: str = "marathon-api"
    PORTKEY_FALLBACK_SLUG: str = "anthropic-fallback"

    # --- QDRANT VECTOR DB ---
    QDRANT_URL: str = Field(alias="QDRANT_CLUSTER_ENDPOINT")
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "enterprise_rag"

    # --- NEON SERVERLESS POSTGRES (LangGraph checkpointer) ---
    NEON_DB_URL: str

    # --- UPSTASH REDIS (Celery broker/backend + rate limiting) ---
    UPSTASH_REDIS_REST_URL: str
    UPSTASH_REDIS_REST_TOKEN: str

    # --- API SAFETY ---
    API_KEY: str | None = Field(default=None, alias="RAG_API_KEY")
    RATE_LIMIT_PER_MINUTE: int = 20
    STRICT_STARTUP: bool = False

    # --- OBSERVABILITY ---
    LOGFIRE_TOKEN: str | None = None
    LANGSMITH_TRACING: str = "true"
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "rag_scale_test"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    @property
    def judge_api_key(self) -> str:
        """Dedicated judge key, falling back to the main OpenAI key."""
        return self.JUDGE_OPENAI_API_KEY or self.OPENAI_API_KEY

    @property
    def postgres_uri(self) -> str:
        """LangGraph Postgres checkpointer URI (Neon)."""
        return self.NEON_DB_URL

    @property
    def redis_url(self) -> str:
        """TLS Redis URL derived from Upstash REST credentials.

        Upstash exposes the same host for REST and TLS Redis; the REST token is
        also the Redis password. Celery and `limits` need a RESP-compatible URL,
        so we build `rediss://default:<token>@<host>:6379/0`.
        """
        host = self.UPSTASH_REDIS_REST_URL.replace("https://", "").rstrip("/")
        token = quote(self.UPSTASH_REDIS_REST_TOKEN, safe="")
        return f"rediss://default:{token}@{host}:6379/0?ssl_cert_reqs=required"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url


# Singleton used across the app.
settings = Settings()


def apply_langchain_env():
    """Write LangSmith/LangChain settings to os.environ for automatic tracing."""
    if settings.LANGSMITH_TRACING:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.LANGSMITH_TRACING)
    if settings.LANGSMITH_API_KEY:
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    if settings.LANGSMITH_PROJECT:
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
    if settings.LANGSMITH_ENDPOINT:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", settings.LANGSMITH_ENDPOINT)


apply_langchain_env()
