import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import logfire
from app.config import settings
from app.services.retrieval.embedding import embed_query

# Global redis client cache reference
_redis_client = None
# In-memory fallback semantic cache if Redis is offline
_in_memory_semantic_cache: List[Tuple[str, List[float], Dict[str, Any]]] = []

# Default Cosine Similarity threshold for semantic matching (0.75 = 75% similarity)
DEFAULT_SEMANTIC_THRESHOLD = 0.75


def _get_redis():
    """Lazy-initialize Redis client for caching."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    try:
        from limits.storage import RedisStorage

        storage = RedisStorage(settings.redis_url)
        if storage.check() and storage.storage.ping():
            _redis_client = storage.storage
            return _redis_client
    except Exception as e:
        logfire.warning(f"⚠️ Response cache Redis connection unavailable: {e}")

    return None


def normalize_query(query: str) -> str:
    """Normalize query text by lowercasing, stripping punctuation, and collapsing whitespace."""
    if not query:
        return ""
    normalized = query.strip().lower()
    normalized = re.sub(r"[?\!.,;:]+$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def get_cache_key(query: str) -> str:
    """Generate SHA256 cache key from normalized query string."""
    norm = normalize_query(query)
    query_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    return f"rag:query_cache:{query_hash}"


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    try:
        import numpy as np

        arr1 = np.array(v1, dtype=np.float32)
        arr2 = np.array(v2, dtype=np.float32)
        dot = np.dot(arr1, arr2)
        norm1 = np.linalg.norm(arr1)
        norm2 = np.linalg.norm(arr2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot / (norm1 * norm2))
    except ImportError:
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot / (norm1 * norm2))


# ── Layer 1: Exact Hash Cache ──────────────────────────────────────────────────


def get_exact_cache(query: str) -> Optional[Dict[str, Any]]:
    """Retrieve exact hash match from Redis."""
    r = _get_redis()
    if r is None:
        return None

    try:
        key = get_cache_key(query)
        cached_data = r.get(key)
        if cached_data:
            if isinstance(cached_data, bytes):
                cached_data = cached_data.decode("utf-8")
            data = json.loads(cached_data)
            logfire.info("⚡ Exact Response Cache Hit", query=query)
            return data
    except Exception as e:
        logfire.warning(f"⚠️ Exact cache read error: {e}")

    return None


# ── Layer 2: Semantic Vector Cache ─────────────────────────────────────────────


def get_semantic_cache(
    query: str, threshold: float = DEFAULT_SEMANTIC_THRESHOLD
) -> Optional[Dict[str, Any]]:
    """
    Computes vector embedding of the query and searches cached query embeddings.
    Returns cached response if similarity >= threshold.
    """
    try:
        query_vector = embed_query(query)
    except Exception as e:
        logfire.warning(f"⚠️ Could not compute query embedding for semantic cache: {e}")
        return None

    best_score = 0.0
    best_response = None
    best_cached_query = ""

    r = _get_redis()
    if r is not None:
        try:
            # Read all semantic cache keys from Redis
            keys = r.keys("rag:semantic_entry:*")
            for key in keys:
                raw = r.get(key)
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                entry = json.loads(raw)
                cached_vec = entry.get("vector")
                if not cached_vec:
                    continue

                sim = cosine_similarity(query_vector, cached_vec)
                if sim > best_score:
                    best_score = sim
                    best_response = entry.get("response")
                    best_cached_query = entry.get("query", "")
        except Exception as e:
            logfire.warning(f"⚠️ Semantic cache Redis scan error: {e}")

    # Fallback to in-memory semantic cache if Redis scan returned nothing or unavailable
    if best_response is None:
        for cached_q, cached_v, resp in _in_memory_semantic_cache:
            sim = cosine_similarity(query_vector, cached_v)
            if sim > best_score:
                best_score = sim
                best_response = resp
                best_cached_query = cached_q

    if best_response and best_score >= threshold:
        logfire.info(
            f"🧠 Semantic Cache Hit! (Similarity: {best_score:.4f} >= {threshold})",
            original_query=query,
            matched_query=best_cached_query,
            similarity=round(best_score, 4),
        )
        res = dict(best_response)
        res["cached"] = True
        res["cache_type"] = "semantic"
        res["similarity_score"] = round(best_score, 4)
        res["matched_query"] = best_cached_query
        return res

    return None


def get_cached_response(
    query: str, threshold: float = DEFAULT_SEMANTIC_THRESHOLD
) -> Optional[Dict[str, Any]]:
    """
    Combined Cache Interface:
    1. Checks Exact Hash Cache (Layer 1: < 3ms).
    2. Checks Semantic Embedding Cache (Layer 2: ~30ms).
    """
    # 1. Exact match
    exact = get_exact_cache(query)
    if exact:
        exact["cached"] = True
        exact["cache_type"] = "exact"
        exact["similarity_score"] = 1.0
        return exact

    # 2. Semantic vector match for similar questions
    return get_semantic_cache(query, threshold=threshold)


def set_cached_response(
    query: str, response_data: Dict[str, Any], ttl_seconds: int = 3600
) -> bool:
    """Stores query response in both Exact Hash Cache and Semantic Vector Cache."""
    norm = normalize_query(query)
    key_exact = get_cache_key(query)

    # Compute query embedding vector for semantic matching
    query_vector = None
    try:
        query_vector = embed_query(query)
    except Exception as e:
        logfire.warning(f"⚠️ Failed to generate embedding for semantic cache index: {e}")

    # Save to Redis
    r = _get_redis()
    if r is not None:
        try:
            payload = json.dumps(response_data)
            r.setex(key_exact, ttl_seconds, payload)

            if query_vector:
                semantic_key = f"rag:semantic_entry:{hashlib.sha256(norm.encode()).hexdigest()}"
                semantic_entry = {
                    "query": query,
                    "normalized_query": norm,
                    "vector": query_vector,
                    "response": response_data,
                }
                r.setex(semantic_key, ttl_seconds, json.dumps(semantic_entry))

            logfire.info("💾 Cached response in Exact & Semantic Redis index", query=query)
            return True
        except Exception as e:
            logfire.warning(f"⚠️ Cache write error: {e}")

    # Also store in memory as fallback
    if query_vector:
        _in_memory_semantic_cache.append((query, query_vector, response_data))
        if len(_in_memory_semantic_cache) > 200:
            _in_memory_semantic_cache.pop(0)

    return False


def clear_query_cache() -> bool:
    """Clear all exact and semantic query cache entries."""
    global _in_memory_semantic_cache
    _in_memory_semantic_cache.clear()

    r = _get_redis()
    if r is None:
        return True

    try:
        keys = r.keys("rag:*query_cache:*") + r.keys("rag:semantic_entry:*")
        if keys:
            r.delete(*keys)
        return True
    except Exception as e:
        logfire.warning(f"⚠️ Cache clear error: {e}")
        return False
