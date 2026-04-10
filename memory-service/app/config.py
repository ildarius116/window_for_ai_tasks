import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://mws:password@postgres:5432/memory",
)

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")

# Embedding model served via LiteLLM / OpenRouter
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mws/nemotron-nano")
EMBEDDING_DIMENSIONS = 768  # will be set dynamically on first call

# Extraction model
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "mws/nemotron")
