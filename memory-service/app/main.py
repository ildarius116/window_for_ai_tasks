import logging

from fastapi import FastAPI
from sqlalchemy import text

from app.database import engine
from app.models import Base
from app.routers import episodes, memories

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MWS Memory Service", version="1.0.0")
app.include_router(memories.router)
app.include_router(episodes.router)


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_episodes_embedding "
                "ON conversation_episodes USING ivfflat (embedding vector_cosine_ops) "
                "WITH (lists = 100)"
            )
        )


@app.get("/health")
async def health():
    return {"status": "ok"}
