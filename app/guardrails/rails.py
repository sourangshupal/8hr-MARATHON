import logfire
from langchain_openai import ChatOpenAI
from nemoguardrails import LLMRails, RailsConfig

from app.config import settings
from app.guardrails.colang_rules import COLANG_CONTENT, RAIL_INDICATORS, YAML_CONTENT

_rails: LLMRails | None = None


def initialize_rails() -> None:
    """
    Build the NeMo LLMRails singleton at app startup.
    Uses OpenAI gpt-5-mini for fast intent classification at the gate.
    """
    global _rails

    guard_llm = ChatOpenAI(api_key=settings.OPENAI_API_KEY, model="gpt-5-mini")

    config = RailsConfig.from_content(colang_content=COLANG_CONTENT, yaml_content=YAML_CONTENT)

    _rails = LLMRails(config, llm=guard_llm)
    logfire.info("🛡️ NeMo Guardrails initialised (gpt-5-mini).")


def guard(message: str) -> tuple[bool, str | None]:
    """
    Run a user message through the NeMo rails gate.

    Returns:
        (True,  rail_response) — a rail fired; return this response immediately,
                                skip the RAG pipeline entirely.
        (False, None)          — message is clean; proceed to LangGraph.
    """
    if _rails is None:
        logfire.warning("⚠️ Guardrails not initialised — skipping gate.")
        return False, None

    with logfire.span("🛡️ Guardrails Check"):
        result = _rails.generate(messages=[{"role": "user", "content": message}])

        # NeMo returns {'role': 'assistant', 'content': '...'} — extract text
        content = result.get("content", "") if isinstance(result, dict) else str(result)

        fired = any(indicator in content for indicator in RAIL_INDICATORS)

        if fired:
            logfire.info(f"🛡️ Guardrails fired | query='{message[:80]}'")
            return True, content

        logfire.info("✅ Guardrails passed.")
        return False, None
