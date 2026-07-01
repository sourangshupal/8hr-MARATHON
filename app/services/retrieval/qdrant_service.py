import logfire
from qdrant_client import QdrantClient
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.retrieval.embedding import embed_query

# Initialize Qdrant Client
client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _search_enterprise_knowledge(query: str, limit: int = 8):
    """Internal search with retry logic."""
    query_vector = embed_query(query)

    # Using query_points - the modern standard for Qdrant
    response = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_vector,
        limit=limit,
        with_payload=True,  # JSON
    )

    results = []
    for res in response.points:
        results.append(
            {"content": res.payload.get("text", ""), "source": res.payload.get("source", "Unknown"), "score": res.score}
        )

    return results


def search_enterprise_knowledge(query: str, limit: int = 8):
    """
    Performs a high-precision search in the enterprise knowledge base.
    Uses the modern query_points interface. Retries transient failures
    and gracefully degrades to an empty result set on persistent failure.
    """
    try:
        return _search_enterprise_knowledge(query, limit=limit)
    except Exception as e:
        logfire.error(f"❌ Qdrant Search Failed after retries: {e}")
        return []
