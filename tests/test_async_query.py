"""Tests for async /query enqueue and status endpoints."""
from unittest.mock import MagicMock, patch

import celery.result
from fastapi.testclient import TestClient

from app.main import app


def test_query_returns_job_id():
    """/query should enqueue a Celery task and return a job_id + poll URL."""
    mock_task = MagicMock()
    mock_task.id = "test-job-123"

    client = TestClient(app)
    with patch("app.main.run_rag_pipeline.delay") as mock_delay:
        mock_delay.return_value = mock_task
        response = client.post("/query", json={"q": "hi"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "test-job-123"
    assert data["status"] == "queued"
    assert data["poll_url"] == "/query/status/test-job-123"
    assert "request_id" in data
    # request_id should be passed through to the Celery task as rag_request_id.
    mock_delay.assert_called_once()
    _, kwargs = mock_delay.call_args
    assert kwargs.get("rag_request_id") == data["request_id"]


def test_query_status_returns_completed_result():
    """/query/status/{job_id} should return the task result when ready."""
    mock_result = MagicMock()
    mock_result.status = "SUCCESS"
    mock_result.ready.return_value = True
    mock_result.successful.return_value = True
    mock_result.get.return_value = {"answer": "hello"}

    client = TestClient(app)
    with patch.object(celery.result, "AsyncResult") as mock_async_result:
        mock_async_result.return_value = mock_result
        response = client.get("/query/status/test-job-123")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["result"]["answer"] == "hello"
    assert "request_id" in data
