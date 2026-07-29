"""Tests for synchronous /query endpoint."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_query_returns_direct_answer():
    """/query should run the pipeline synchronously and return the answer."""
    mock_state = {
        "final_answer": "hello",
        "plan": ["Intent: Technical", "Search Term: hi"],
        "status": "Response generated.",
        "documents": ["doc1"],
    }
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = mock_state

    # Ensure rag_agent exists on app.state before TestClient uses it.
    app.state.rag_agent = mock_agent

    from app.config import settings

    headers = {"Authorization": f"Bearer {settings.API_KEY}"} if settings.API_KEY else {}

    client = TestClient(app)
    with patch("app.main.guard") as mock_guard:
        mock_guard.return_value = (False, "")
        response = client.post("/query", json={"q": "hi", "thread_id": "t1"}, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "hello"
    assert data["status"] == "Response generated."
    assert data["sources"] == ["doc1"]
    mock_agent.invoke.assert_called_once()


def test_query_blocks_guardrails():
    """/query should return a blocked response when guardrails fire."""
    from app.config import settings

    headers = {"Authorization": f"Bearer {settings.API_KEY}"} if settings.API_KEY else {}
    client = TestClient(app)
    with patch("app.main.guard") as mock_guard:
        mock_guard.return_value = (True, "Blocked message")
        response = client.post("/query", json={"q": "bad prompt", "thread_id": "t2"}, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Blocked by guardrails."
    assert data["answer"] == "Blocked message"
