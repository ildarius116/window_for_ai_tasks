import logging

from fastapi import FastAPI

from app.database import engine
from app.models import Base
from app.routers import memories

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="MWS Memory Service", version="1.0.0")
app.include_router(memories.router)


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text(
                "CREATE EXTENSION IF NOT EXISTS vector"
            )
        )
        await conn.run_sync(Base.metadata.create_all)


@app.get("/health")
async def health():
    return {"status": "ok"}
