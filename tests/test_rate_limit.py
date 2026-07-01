"""Tests for rate limiting."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.main import app


def test_rate_limit_blocks_excessive_requests():
    """With a 1/minute limit, the second request within the window is rejected."""
    original_rate = settings.RATE_LIMIT_PER_MINUTE
    try:
        settings.RATE_LIMIT_PER_MINUTE = 1
        # Force an in-memory limiter for this test so state is isolated.
        app.state.limiter = Limiter(key_func=get_remote_address)

        client = TestClient(app)
        with patch("app.main.guard") as mock_guard, patch("app.main.run_rag_pipeline") as mock_task:
            mock_guard.return_value = (True, "blocked")
            mock_task.delay.return_value.id = "job-123"
            response1 = client.post("/query", json={"q": "hi"})
            response2 = client.post("/query", json={"q": "hi again"})

        assert response1.status_code == 200
        assert response2.status_code == 429
    finally:
        settings.RATE_LIMIT_PER_MINUTE = original_rate
