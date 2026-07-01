import time

import logfire
from flashrank import Ranker, RerankRequest
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

# Lazy initialization - Ranker is loaded on first use to ensure logfire.configure() has run
_ranker = None


def _get_ranker() -> Ranker:
    """
    Initializes the FlashRank engine lazily.
    FlashRank uses a local ONNX model (ms-marco-MiniLM-L-6-v2) for ultra-fast reranking.
    """
    global _ranker
    if _ranker is None:
        logfire.info("🧠 Initializing FlashRank Model (TinyBERT) locally...")
        try:
            # We use a specific cache directory to avoid permission issues in production
            _ranker = Ranker(cache_dir="/tmp/flashrank")
        except Exception:
            _ranker = Ranker()
    return _ranker


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _rerank(query: str, documents: list[str], top_n: int) -> list[str]:
    """Core FlashRank reranking with retry on transient failures."""
    ranker = _get_ranker()

    # FlashRank expects a list of dictionaries with 'id' and 'text'
    passages = [{"id": i, "text": doc} for i, doc in enumerate(documents)]

    request = RerankRequest(query=query, passages=passages)
    results = ranker.rerank(request)

    # Results are returned sorted by highest semantic score first
    reranked_docs = []
    for res in results[:top_n]:
        reranked_docs.append(res["text"])

    return reranked_docs


def rerank_documents(query: str, documents: list[str], top_n: int = 5) -> list[str]:
    """
    Refines retrieval results by re-scoring documents against the query semantically.
    Retries transient failures and falls back to the original Qdrant order if
    reranking ultimately fails, ensuring the user still receives an answer.
    """
    if not documents:
        return []

    start_time = time.time()
    logfire.info(f"📡 [Reranker] Sending {len(documents)} docs to FlashRank Cross-Encoder...")

    try:
        reranked_docs = _rerank(query, documents, top_n)
        duration = time.time() - start_time
        logfire.info(f"✅ [Reranker] Done in {duration:.2f}s.")
        return reranked_docs
    except Exception as e:
        logfire.error(f"❌ [Reranker] Semantic Reranking Failed after retries: {e}")
        # Fallback to the original Qdrant order to ensure the user still gets an answer
        return documents[:top_n]
