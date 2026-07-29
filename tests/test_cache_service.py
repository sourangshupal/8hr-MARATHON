import pytest
from app.services.cache_service import (
    clear_query_cache,
    cosine_similarity,
    get_cache_key,
    get_cached_response,
    get_semantic_cache,
    normalize_query,
    set_cached_response,
)


def test_normalize_query():
    assert normalize_query("  What is Kubernetes Pod?  ") == "what is kubernetes pod"
    assert normalize_query("WHAT IS KUBERNETES POD???") == "what is kubernetes pod"
    assert normalize_query("hello world!") == "hello world"


def test_get_cache_key_consistency():
    key1 = get_cache_key("What is a pod?")
    key2 = get_cache_key("  what is a pod?  ")
    key3 = get_cache_key("WHAT IS A POD???")
    assert key1 == key2 == key3


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(v1, v2) == pytest.approx(1.0)
    assert cosine_similarity(v1, v3) == pytest.approx(0.0)


def test_cache_set_and_get():
    query = "What is a Kubernetes Pod?"
    data = {
        "question": query,
        "answer": "A Pod is the smallest deployable unit in Kubernetes.",
        "thought_process": ["Step 1", "Step 2"],
        "status": "Response generated.",
        "sources": [],
    }

    clear_query_cache()

    set_cached_response(query, data, ttl_seconds=60)

    # 1. Test exact match
    exact = get_cached_response(query)
    assert exact is not None
    assert exact["answer"] == data["answer"]
    assert exact["cache_type"] == "exact"

    # 2. Test semantic match for a similar phrasing of the same question!
    similar_query = "Can you explain what a Pod in Kubernetes is?"
    semantic = get_cached_response(similar_query, threshold=0.70)
    assert semantic is not None
    assert semantic["answer"] == data["answer"]
    assert semantic["cached"] is True
    assert semantic["cache_type"] in ["exact", "semantic"]
    assert "similarity_score" in semantic

    clear_query_cache()
