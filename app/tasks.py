"""Celery tasks for running the LangGraph RAG pipeline asynchronously."""

import os

from dotenv import load_dotenv

# Load environment variables before any other app imports.
load_dotenv()

import logfire
from celery import Celery
from celery.signals import worker_process_init
from prometheus_client import Counter

from app.agents.graph import build_graph
from app.config import settings
from app.guardrails import guard, initialize_rails
from app.logging import set_request_id

CELERY_JOBS_TOTAL = Counter(
    "celery_jobs_total",
    "Celery RAG job outcomes",
    ["status"],
)

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
    task_acks_late=True,  # Ack after task completes so a killed worker can retry.
    task_reject_on_worker_lost=True,
)


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize guardrails once per Celery worker process."""
    logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))
    initialize_rails()
    logfire.info("🛡️ Celery worker initialized guardrails.")


@celery_app.task(bind=True, max_retries=3)
def run_rag_pipeline(self, query: str, thread_id: str, rag_request_id: str | None = None):
    """
    Celery task that runs the full RAG pipeline (guardrails + LangGraph).
    Stores the final API-style response as the task result.
    """
    set_request_id(rag_request_id)
    with logfire.span(
        "🚀 Celery RAG pipeline", task_id=self.request.id, thread_id=thread_id, request_id=rag_request_id
    ):
        try:
            # Gate 1: NeMo Guardrails
            rail_fired, rail_response = guard(query)
            if rail_fired:
                CELERY_JOBS_TOTAL.labels(status="blocked").inc()
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

            CELERY_JOBS_TOTAL.labels(status="success").inc()
            return {
                "question": query,
                "answer": final_output.get("final_answer"),
                "thought_process": final_output.get("plan"),
                "status": final_output.get("status"),
                "sources": final_output.get("documents", []),
            }

        except Exception as e:
            CELERY_JOBS_TOTAL.labels(status="failure").inc()
            logfire.error(f"❌ Celery RAG pipeline failed: {e}")
            # Only retry exceptions that look transient (network/LLM/API issues).
            if not _is_retryable_error(e):
                raise e
            raise self.retry(exc=e, countdown=2**self.request.retries)


def _is_retryable_error(exc: Exception) -> bool:
    """Return True for exceptions likely to be transient and worth retrying."""
    retryable_names = {"APIError", "APIConnectionError", "RateLimitError", "InternalServerError"}
    exc_type_name = type(exc).__name__
    if exc_type_name in retryable_names:
        return True
    if exc_type_name in {"RequestException", "HTTPError", "Timeout", "ConnectionError"}:
        return True
    return False
