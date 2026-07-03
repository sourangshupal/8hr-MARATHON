"""Tests for API authentication and rate limiting."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_query_open_when_no_api_key_configured():
    """If RAG_API_KEY is unset, /query accepts requests without auth."""
    original_key = settings.API_KEY
    try:
        settings.API_KEY = None
        client = TestClient(app)
        with patch("app.main.guard") as mock_guard:
            mock_guard.return_value = (True, "blocked")
            response = client.post("/query", json={"q": "hi"})
        assert response.json()["status"] == "Blocked by guardrails."
    finally:
        settings.API_KEY = original_key


def test_query_rejects_invalid_api_key():
    """If RAG_API_KEY is set, invalid bearer tokens are rejected."""
    original_key = settings.API_KEY
    try:
        settings.API_KEY = "super-secret"
        client = TestClient(app)
        response = client.post(
            "/query",
            json={"q": "hi"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401
        assert "Invalid or missing API key" in response.json()["detail"]
    finally:
        settings.API_KEY = original_key


def test_query_accepts_valid_api_key():
    """If RAG_API_KEY is set, the correct bearer token is accepted."""
    original_key = settings.API_KEY
    try:
        settings.API_KEY = "super-secret"
        client = TestClient(app)
        with patch("app.main.guard") as mock_guard:
            mock_guard.return_value = (True, "blocked")
            response = client.post(
                "/query",
                json={"q": "hi"},
                headers={"Authorization": "Bearer super-secret"},
            )
            assert response.status_code == 200
    finally:
        settings.API_KEY = original_key
