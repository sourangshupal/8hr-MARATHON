import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    # --- JINA EMBEDDINGS API ---
    JINA_API_KEY = os.getenv("JINA_API_KEY")

    # --- VECTOR DB (QDRANT) ---
    QDRANT_URL = os.getenv("QDRANT_CLUSTER_ENDPOINT")
    QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
    QDRANT_COLLECTION = "enterprise_rag"

    # --- OPENAI LLM ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    JUDGE_OPENAI_API_KEY = os.getenv("JUDGE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

    # --- LLM GATEWAY (PORTKEY) ---
    PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
    PORTKEY_PRIMARY_SLUG = "marathon-api"  # primary: @marathon-api/gpt-5-mini
    PORTKEY_FALLBACK_SLUG = "anthropic-fallback"  # fallback: @anthropic-fallback/claude-haiku-4-5-20251001

    # --- PRODUCTION PERSISTENCE ---
    POSTGRES_URI = os.getenv("POSTGRES_URI", "postgresql://postgres:postgres@localhost:5432/enterprise_rag")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    API_KEY = os.getenv("RAG_API_KEY")  # Required in production for /query auth
    RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

    # --- OBSERVABILITY ---
    LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "true")
    LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "rag_scale_test")
    LANGSMITH_ENDPOINT = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")


# Apply LangChain environment variables for automatic tracing
os.environ["LANGCHAIN_TRACING_V2"] = os.getenv("LANGSMITH_TRACING", "true")
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "rag_scale_test")
os.environ["LANGCHAIN_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

settings = Settings()
