"""Tests for LangGraph checkpointer configuration."""

from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from app.agents.graph import build_graph, create_checkpointer


def test_build_graph_uses_provided_checkpointer():
    """build_graph should compile using the supplied checkpointer."""
    saver = MemorySaver()
    agent = build_graph(checkpointer=saver)
    assert agent is not None
    # The compiled graph should expose checkpointer
    assert agent.checkpointer is saver


def test_create_checkpointer_prefers_postgres_when_available():
    """When Postgres deps are available, a PostgresSaver is built."""
    mock_pg = MagicMock(name="langgraph.checkpoint.postgres")
    mock_pg.PostgresSaver = MagicMock(name="PostgresSaver")

    mock_pool = MagicMock(name="psycopg_pool")
    mock_pool.ConnectionPool = MagicMock(name="ConnectionPool")

    modules = {
        "langgraph.checkpoint.postgres": mock_pg,
        "psycopg_pool": mock_pool,
    }

    with patch.dict("sys.modules", modules), patch("app.agents.graph.logfire"):
        cp = create_checkpointer()

    mock_pool.ConnectionPool.assert_called_once()
    mock_pg.PostgresSaver.assert_called_once_with(mock_pool.ConnectionPool.return_value)
    assert cp is mock_pg.PostgresSaver.return_value


def test_create_checkpointer_falls_back_to_memory():
    """If Postgres dependencies are missing, fall back to MemorySaver."""
    with patch.dict("sys.modules", {"langgraph.checkpoint.postgres": None}), patch("app.agents.graph.logfire"):
        cp = create_checkpointer()
        assert isinstance(cp, MemorySaver)
