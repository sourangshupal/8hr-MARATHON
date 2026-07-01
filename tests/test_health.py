"""Tests for health and readiness endpoints."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def _patch_all_dependencies(search_ok=True, gateway_ok=True, postgres_ok=True):
    stack = patch("app.health.search_enterprise_knowledge")
    mock_search = stack.start()
    if search_ok:
        mock_search.return_value = []
    else:
        mock_search.side_effect = RuntimeError("qdrant down")

    stack2 = patch("app.health.portkey_client")
    mock_gateway = stack2.start()
    if gateway_ok:
        mock_choice = MagicMock()
        mock_choice.message.content = "hi"
        mock_gateway.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
    else:
        mock_gateway.chat.completions.create.side_effect = RuntimeError("gateway down")

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn
    mock_checkpointer = MagicMock()
    mock_checkpointer.conn = mock_pool
    mock_agent = MagicMock()
    mock_agent.checkpointer = mock_checkpointer
    app.state.rag_agent = mock_agent

    return stack, stack2


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_when_all_dependencies_healthy():
    s1, s2 = _patch_all_dependencies()
    try:
        client = TestClient(app)
        response = client.get("/ready")
    finally:
        s1.stop()
        s2.stop()

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["qdrant"] == "ok"
    assert data["checks"]["llm_gateway"] == "ok"
    assert data["checks"]["postgres"] == "ok"


def test_ready_returns_503_when_qdrant_fails():
    s1, s2 = _patch_all_dependencies(search_ok=False)
    try:
        client = TestClient(app)
        response = client.get("/ready")
    finally:
        s1.stop()
        s2.stop()

    assert response.status_code == 503
    assert response.json()["checks"]["qdrant"].startswith("unavailable")
