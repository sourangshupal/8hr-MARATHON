"""Tests for retry-with-backoff behavior on external service calls."""

from unittest.mock import MagicMock, patch

from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_documents


def test_qdrant_search_retries_then_returns_empty():
    """Qdrant search should retry transient failures and finally return []."""
    with (
        patch("app.services.retrieval.qdrant_service.client") as mock_client,
        patch("app.services.retrieval.qdrant_service.embed_query") as mock_embed,
    ):
        mock_embed.return_value = [0.0] * 10
        mock_client.query_points.side_effect = RuntimeError("transient")

        results = search_enterprise_knowledge("test query", limit=1)

        assert results == []
        # Tenacity default: initial call + 2 retries = 3 attempts
        assert mock_client.query_points.call_count == 3


def test_rerank_retries_then_falls_back():
    """Reranker should retry transient failures and fall back to input order."""
    docs = ["doc one", "doc two", "doc three"]
    with patch("app.services.retrieval.ranking_service._get_ranker") as mock_get_ranker:
        mock_ranker = MagicMock()
        mock_ranker.rerank.side_effect = RuntimeError("transient")
        mock_get_ranker.return_value = mock_ranker

        results = rerank_documents("query", docs, top_n=2)

        assert results == docs[:2]
        assert mock_ranker.rerank.call_count == 3
