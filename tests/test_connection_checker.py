"""Tests for app.services.health.connection_checker."""

from unittest.mock import MagicMock, patch

import requests

from app.services.health.connection_checker import (
    ConnectionResult,
    _check_jina_embeddings,
    _check_jina_reranker,
    _check_neon_postgres,
    _check_portkey_gateway,
    _check_qdrant,
    _check_upstash_redis,
    check_all_connections,
)


def _ok(name: str) -> ConnectionResult:
    return ConnectionResult(name, True, "ok")


def _fail(name: str, message: str = "fail") -> ConnectionResult:
    return ConnectionResult(name, False, message)


def test_check_neon_postgres_success():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn
    with patch("app.services.health.connection_checker.ConnectionPool", return_value=mock_pool):
        result = _check_neon_postgres()
    assert result.healthy is True
    assert result.name == "postgres"


def test_check_neon_postgres_failure():
    with patch("app.services.health.connection_checker.ConnectionPool", side_effect=Exception("conn refused")):
        result = _check_neon_postgres()
    assert result.healthy is False
    assert "conn refused" in result.message


def test_check_upstash_redis_success():
    mock_redis = MagicMock()
    with patch("app.services.health.connection_checker.Redis.from_url", return_value=mock_redis):
        result = _check_upstash_redis()
    assert result.healthy is True
    assert result.name == "redis"
    mock_redis.ping.assert_called_once()


def test_check_upstash_redis_failure():
    with patch("app.services.health.connection_checker.Redis.from_url", side_effect=Exception("timeout")):
        result = _check_upstash_redis()
    assert result.healthy is False


def test_check_qdrant_success():
    mock_client = MagicMock()
    with patch("app.services.health.connection_checker.QdrantClient", return_value=mock_client):
        result = _check_qdrant()
    assert result.healthy is True
    assert result.name == "qdrant"
    mock_client.get_collections.assert_called_once()


def test_check_qdrant_failure():
    with patch("app.services.health.connection_checker.QdrantClient", side_effect=Exception("unauthorized")):
        result = _check_qdrant()
    assert result.healthy is False


def test_check_portkey_gateway_success():
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="hi"))]
    with patch("app.services.health.connection_checker.portkey_client.chat.completions.create", return_value=mock_resp):
        result = _check_portkey_gateway()
    assert result.healthy is True
    assert result.name == "llm_gateway"


def test_check_portkey_gateway_empty_response():
    mock_resp = MagicMock()
    mock_resp.choices = []
    with patch("app.services.health.connection_checker.portkey_client.chat.completions.create", return_value=mock_resp):
        result = _check_portkey_gateway()
    assert result.healthy is False


def test_check_jina_embeddings_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"embedding": [0.1]}]}
    mock_response.raise_for_status = MagicMock()
    with patch("app.services.health.connection_checker.requests.post", return_value=mock_response):
        result = _check_jina_embeddings()
    assert result.healthy is True
    assert result.name == "jina_embeddings"


def test_check_jina_embeddings_no_key():
    with patch("app.services.health.connection_checker.settings.JINA_API_KEY", None):
        result = _check_jina_embeddings()
    assert result.healthy is False
    assert "JINA_API_KEY not set" in result.message


def test_check_jina_reranker_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": [{"index": 0}]}
    mock_response.raise_for_status = MagicMock()
    with patch("app.services.health.connection_checker.requests.post", return_value=mock_response):
        result = _check_jina_reranker()
    assert result.healthy is True
    assert result.name == "jina_reranker"


def test_check_jina_reranker_failure():
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("service unavailable")
    with patch("app.services.health.connection_checker.requests.post", return_value=mock_response):
        result = _check_jina_reranker()
    assert result.healthy is False
    assert result.name == "jina_reranker"


def test_check_all_connections_returns_all_results():
    mock_checkers = [
        lambda: _ok("postgres"),
        lambda: _ok("redis"),
        lambda: _ok("qdrant"),
        lambda: _ok("llm_gateway"),
        lambda: _ok("jina_embeddings"),
        lambda: _fail("jina_reranker"),
    ]
    with patch("app.services.health.connection_checker._CHECKERS", mock_checkers):
        results = check_all_connections()
    assert len(results) == 6
    assert results["postgres"].healthy is True
    assert results["jina_reranker"].healthy is False
