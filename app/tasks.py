"""Celery tasks for running the LangGraph RAG pipeline asynchronously."""
import os
from dotenv import load_dotenv

# Load environment variables before any other app imports.
load_dotenv()

import logfire
from celery import Celery
from celery.signals import worker_process_init

from app.config import settings
from app.agents.graph import build_graph
from app.guardrails import initialize_rails, guard

# Configure Celery to use Redis as both broker and result backend.
celery_app = Celery(
    "enterprise_rag",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_result_expires=3600,  # 1 hour
    result_extended=True,
)


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize guardrails once per Celery worker process."""
    logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))
    initialize_rails()
    logfire.info("🛡️ Celery worker initialized guardrails.")


@celery_app.task(bind=True, max_retries=3)
def run_rag_pipeline(self, query: str, thread_id: str):
    """
    Celery task that runs the full RAG pipeline (guardrails + LangGraph).
    Stores the final API-style response as the task result.
    """
    with logfire.span("🚀 Celery RAG pipeline", task_id=self.request.id, thread_id=thread_id):
        try:
            # Gate 1: NeMo Guardrails
            rail_fired, rail_response = guard(query)
            if rail_fired:
                logfire.info(f"🛡️ Request blocked by guardrails | thread={thread_id}")
                return {
                    "question": query,
                    "answer": rail_response,
                    "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                    "status": "Blocked by guardrails.",
                    "sources": [],
                }

            # Gate 2: LangGraph RAG pipeline
            rag_agent = build_graph()
            initial_state = {
                "messages": [{"role": "user", "content": query}],
                "current_query": query,
                "documents": [],
                "plan": ["Start"],
                "status": "Initializing Graph...",
            }
            config = {"configurable": {"thread_id": thread_id}}
            final_output = rag_agent.invoke(initial_state, config=config)

            return {
                "question": query,
                "answer": final_output.get("final_answer"),
                "thought_process": final_output.get("plan"),
                "status": final_output.get("status"),
                "sources": final_output.get("documents", []),
            }

        except Exception as e:
            logfire.error(f"❌ Celery RAG pipeline failed: {e}")
            # Retry on transient failures; raise final exception on last retry.
            raise self.retry(exc=e, countdown=2 ** self.request.retries)
