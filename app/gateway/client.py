from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from portkey_ai import PORTKEY_GATEWAY_URL, Portkey, createHeaders

from app.config import settings

# Portkey routing strategy:
#   - Primary/fallback logic lives in a Portkey saved config (required when
#     block_inline_config is enabled on the workspace).
#   - We reference that config via x-portkey-config-id or @slug/model headers.
#   - The simple "config dict" approach is disabled for this account, so all
#     retry/fallback/cache behavior must be configured inside the Portkey UI.


def _make_headers(feature: str = "rag") -> dict:
    """Build Portkey headers that reference a saved config by slug."""
    return createHeaders(
        api_key=settings.PORTKEY_API_KEY,
        config=settings.PORTKEY_PRIMARY_SLUG,
        metadata={
            "feature": feature,
            "_user": "rag-system",
            "environment": "production",
        },
    )


# Native Portkey client backed by the saved config slug.
portkey_client = Portkey(
    api_key=settings.PORTKEY_API_KEY,
    config=settings.PORTKEY_PRIMARY_SLUG,
)


def get_langchain_llm(feature: str = "rag") -> ChatOpenAI:
    """
    Returns a Portkey-backed ChatOpenAI — a drop-in for LangChain nodes.

    Why ChatOpenAI:
      Portkey is a proxy. It exposes an OpenAI-compatible endpoint at PORTKEY_GATEWAY_URL.
      ChatOpenAI supports base_url (points at Portkey) and default_headers (passes Portkey
      auth + saved-config reference). The @slug/model-name format is Portkey-specific — the
      upstream provider's own client does not understand it. Portkey is just in the middle.
    """
    return ChatOpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        model=f"@{settings.PORTKEY_PRIMARY_SLUG}/gpt-5-mini",
        temperature=0,
        default_headers=_make_headers(feature),
    )


def get_async_openai_client(feature: str = "rag") -> AsyncOpenAI:
    """
    Returns an async OpenAI client that routes through the Portkey gateway.
    Use this for non-LangChain async LLM calls (e.g. async FastAPI endpoints).
    """
    return AsyncOpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        default_headers=_make_headers(feature),
    )


def extract_cache_status(response) -> str:
    """
    Pull x-portkey-cache-status from the Portkey native client response headers.
    Tries multiple attribute paths defensively — returns 'MISS' if not found.
    """
    for attr in ("_raw_response", "_response", "_http_response"):
        raw = getattr(response, attr, None)
        if raw is not None:
            status = getattr(raw, "headers", {}).get("x-portkey-cache-status", "")
            if status:
                return status.upper()
    return "MISS"
