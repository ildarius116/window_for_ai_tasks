import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://mws:password@postgres:5432/memory",
)

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")

# Embedding model — routed via LiteLLM to MWS GPT API
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mws/bge-m3")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))  # bge-m3 = 1024

# Extraction model — used by LLM-based fact extraction from conversations
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "mws/gpt-alpha")
