"""Tests for Celery task retry behavior."""
from unittest.mock import patch
import requests

from app.tasks import run_rag_pipeline


def test_task_does_not_retry_on_non_retryable_error():
    """A ValueError from the graph should bubble up without calling retry."""
    with patch("app.tasks.guard") as mock_guard, patch("app.tasks.build_graph") as mock_build, patch.object(
        run_rag_pipeline, "retry"
    ) as mock_retry:
        mock_guard.return_value = (False, "")
        mock_build.side_effect = ValueError("bad input")

        try:
            run_rag_pipeline.run("hello", "thread-1", rag_request_id="req-1")
        except ValueError:
            pass

    mock_retry.assert_not_called()


def test_task_retries_on_transient_network_error():
    """A requests ConnectionError should trigger the Celery retry path."""
    with patch("app.tasks.guard") as mock_guard, patch("app.tasks.build_graph") as mock_build, patch.object(
        run_rag_pipeline, "retry"
    ) as mock_retry:
        mock_guard.return_value = (False, "")
        mock_build.side_effect = requests.ConnectionError("network hiccup")
        # Simulate Celery retry raising the original exception.
        mock_retry.side_effect = lambda exc, countdown: exc

        try:
            run_rag_pipeline.run("hello", "thread-1", rag_request_id="req-2")
        except requests.ConnectionError:
            pass

    mock_retry.assert_called_once()
