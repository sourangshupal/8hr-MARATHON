"""Tests for the Prometheus /metrics endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def test_metrics_endpoint_exposes_rag_counters():
    """Calling /query should increment metrics visible on /metrics."""
    client = TestClient(app)

    # Make a blocked /query request so no real Celery backend is needed.
    with patch("app.main.guard") as mock_guard:
        mock_guard.return_value = (True, "blocked")
        response = client.post("/query", json={"q": "test"})
    assert response.status_code == 200

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    body = metrics_resp.text

    assert 'rag_requests_total{status="blocked"}' in body
    assert 'guardrails_blocks_total{blocked="true"}' in body
    assert "rag_request_duration_seconds" in body
