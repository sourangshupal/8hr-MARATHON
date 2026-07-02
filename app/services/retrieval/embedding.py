import logfire
import requests
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings

BATCH_SIZE = 64
_EMBEDDING_DIM = 1024
_JINA_EMBEDDING_URL = "https://api.jina.ai/v1/embeddings"
_JINA_MODEL = "jina-embeddings-v3"
_FALLBACK_MODEL = "mixedbread-ai/mxbai-embed-large-v1"

_active_model = None
_model_type: str | None = None  # "jina" or "fallback"


# ── Model initialisation ───────────────────────────────────────────────────────


def _load_fallback():
    """Load the local mxbai fallback model."""
    from sentence_transformers import SentenceTransformer

    logfire.info(f"Loading fallback embedding model ({_FALLBACK_MODEL}, {_EMBEDDING_DIM}-dim).")
    return SentenceTransformer(_FALLBACK_MODEL)


def _probe_jina_api() -> bool:
    """Verify the Jina Embeddings API is reachable with the configured key."""
    if not settings.JINA_API_KEY:
        logfire.info("JINA_API_KEY not set — will use local fallback embeddings.")
        return False

    try:
        response = requests.post(
            _JINA_EMBEDDING_URL,
            headers={
                "Authorization": f"Bearer {settings.JINA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": _JINA_MODEL,
                "task": "retrieval.query",
                "normalized": True,
                "input": ["probe"],
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("data"):
            raise RuntimeError("Jina API returned empty data")
        logfire.info("Jina Embeddings API ready (jina-embeddings-v3, 1024-dim).")
        return True
    except Exception as e:
        logfire.warning(f"Jina Embeddings API probe failed: {e}. Will use local fallback embeddings.")
        return False


def _init():
    """Initialise embedding provider once per process. Called lazily on first use."""
    global _active_model, _model_type
    if _active_model is not None or _model_type is not None:
        return

    if _probe_jina_api():
        _active_model = None  # Jina API is stateless; no local model to keep
        _model_type = "jina"
    else:
        _active_model = _load_fallback()
        _model_type = "fallback"


# ── Public helpers ─────────────────────────────────────────────────────────────


def get_embedding_dim() -> int:
    """Return the vector dimension for the active model."""
    _init()
    return _EMBEDDING_DIM


# ── Jina API embedding ─────────────────────────────────────────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _embed_jina_batch(texts: list[str], task: str) -> list[list[float]]:
    """Call the Jina Embeddings API for a single batch."""
    response = requests.post(
        _JINA_EMBEDDING_URL,
        headers={
            "Authorization": f"Bearer {settings.JINA_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": _JINA_MODEL,
            "task": task,
            "normalized": True,
            "input": texts,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()

    results = payload.get("data", [])
    # Sort by index because the API may not preserve order in rare cases
    results_sorted = sorted(results, key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in results_sorted]


def _embed_jina(texts: list[str], task: str) -> list[list[float]]:
    """Embed texts via the Jina API in batches with retry."""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        with logfire.span("Embed batch via Jina API", start=i, size=len(batch)):
            embeddings = _embed_jina_batch(batch, task)
            all_embeddings.extend(embeddings)
    return all_embeddings


# ── Fallback embedding ─────────────────────────────────────────────────────────


def _embed_fallback_batch(texts: list[str]) -> list[list[float]]:
    """Embed texts using the local mxbai model."""
    embeddings = _active_model.encode(texts, show_progress_bar=False)
    return embeddings.tolist()


def _embed_fallback(texts: list[str]) -> list[list[float]]:
    """Embed texts via the local fallback model in batches."""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        with logfire.span("Embed batch via fallback model", start=i, size=len(batch)):
            all_embeddings.extend(_embed_fallback_batch(batch))
    return all_embeddings


# ── Unified embedding with runtime fallback ────────────────────────────────────


def _ensure_fallback():
    """Switch to the local fallback model if not already active."""
    global _active_model, _model_type
    if _model_type != "fallback":
        logfire.warning("Switching to local fallback embeddings.")
        _active_model = _load_fallback()
        _model_type = "fallback"


def _embed(texts: list[str], task: str) -> list[list[float]]:
    """Embed texts using the active provider, falling back to local on failure."""
    _init()

    if _model_type == "jina":
        try:
            return _embed_jina(texts, task)
        except Exception as e:
            logfire.error(f"Jina Embeddings API failed: {e}. Falling back to local model.")
            _ensure_fallback()

    return _embed_fallback(texts)


# ── Public API (same signatures as before) ─────────────────────────────────────


def embed_query(query: str) -> list[float]:
    """Embed a single query."""
    return _embed([query], task="retrieval.query")[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of document texts."""
    return _embed(texts, task="retrieval.passage")
