import logfire
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agents.nodes.planner import planner_node
from app.agents.nodes.responder import generate_node
from app.agents.nodes.retriever import retrieve_node
from app.agents.state import AgentState
from app.config import settings


def create_checkpointer() -> BaseCheckpointSaver:
    """
    Create a durable Postgres checkpointer for production.
    Falls back to in-memory MemorySaver only if Postgres is unreachable
    (useful for local development without a database).
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=settings.POSTGRES_URI,
            max_size=20,
            open=False,
            timeout=2,
            num_workers=1,
        )
        # Verify connectivity before committing to Postgres; otherwise the first
        # graph invocation will hang on connection retries.
        pool.open()
        conn = pool.getconn()
        pool.putconn(conn)
        checkpointer = PostgresSaver(pool)
        logfire.info("🗄️ Postgres checkpointer configured.")
        return checkpointer
    except Exception as e:
        logfire.warning(
            f"⚠️ Postgres checkpointer unavailable ({e}); falling back to MemorySaver. "
            "Do not use MemorySaver in production — state is lost on restart."
        )
        return MemorySaver()


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> StateGraph:
    """
    Build and compile the LangGraph RAG agent.

    Args:
        checkpointer: Optional checkpointer. If None, a Postgres-backed
            checkpointer is created. Pass a MemorySaver in tests.
    """
    if checkpointer is None:
        checkpointer = create_checkpointer()

    # 1. Initialize the State Graph
    workflow = StateGraph(AgentState)

    # 2. Define the Nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("retriever", retrieve_node)
    workflow.add_node("responder", generate_node)

    # 3. Define the Edges & Routing Logic
    def route_planner(state: AgentState):
        """
        Routes the workflow based on the planner's decision.
        """
        if state["current_query"] == "CONVERSATIONAL":
            return "responder"
        return "retriever"

    workflow.set_entry_point("planner")

    # Conditional Edge: Planner -> Router -> (Retriever OR Responder)
    workflow.add_conditional_edges(
        "planner",
        route_planner,
        {
            "retriever": "retriever",
            "responder": "responder"
        }
    )

    workflow.add_edge("retriever", "responder")
    workflow.add_edge("responder", END)

    # 4. Compile the Graph with Memory
    return workflow.compile(checkpointer=checkpointer)


# Production code should call build_graph() explicitly (see app.main.startup_event).
# The old module-level `rag_agent` has been removed to avoid constructing two
# checkpointers and to make dependency injection/testability cleaner.
