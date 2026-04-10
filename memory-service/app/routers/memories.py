import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import delete, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.embedding import get_embedding
from app.extraction import extract_memories
from app.models import Memory
from app.schemas import (
    ExtractRequest,
    MemoryCreate,
    MemoryOut,
    MemorySearch,
    MemorySearchResult,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memories", tags=["memories"])


def _cosine_search_query(embedding: list[float], user_id: str, limit: int):
    """Build a cosine similarity search using pgvector ORM column."""
    distance = Memory.embedding.cosine_distance(embedding)
    return (
        select(
            Memory.id,
            Memory.user_id,
            Memory.content,
            Memory.source_chat_id,
            Memory.created_at,
            Memory.updated_at,
            (1 - distance).label("score"),
        )
        .where(Memory.user_id == user_id)
        .order_by(distance)
        .limit(limit)
    )


@router.post("", response_model=MemoryOut)
async def create_memory(body: MemoryCreate, db: AsyncSession = Depends(get_db)):
    embedding = await get_embedding(body.content)
    memory = Memory(
        user_id=body.user_id,
        content=body.content,
        embedding=embedding,
        source_chat_id=body.source_chat_id,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


@router.get("/{user_id}", response_model=list[MemoryOut])
async def list_memories(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.created_at.desc())
    )
    return result.scalars().all()


@router.post("/search", response_model=list[MemorySearchResult])
async def search_memories(body: MemorySearch, db: AsyncSession = Depends(get_db)):
    query_embedding = await get_embedding(body.query)
    stmt = _cosine_search_query(query_embedding, body.user_id, body.limit)
    result = await db.execute(stmt)
    rows = result.mappings().all()
    return [MemorySearchResult(**row) for row in rows]


@router.delete("/{memory_id}")
async def delete_memory(memory_id: UUID, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Memory).where(Memory.id == memory_id))
    await db.commit()
    return {"ok": True}


@router.delete("/user/{user_id}")
async def delete_user_memories(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(delete(Memory).where(Memory.user_id == user_id))
    await db.commit()
    return {"ok": True, "deleted": result.rowcount}


@router.post("/extract", response_model=list[MemoryOut])
async def extract_and_save(body: ExtractRequest, db: AsyncSession = Depends(get_db)):
    """Extract facts from conversation and save as memories (with dedup)."""
    facts = await extract_memories(body.messages)
    saved = []

    for fact in facts:
        embedding = await get_embedding(fact)

        # Dedup: check if very similar memory already exists
        stmt = _cosine_search_query(embedding, body.user_id, 1)
        existing = await db.execute(stmt)
        row = existing.mappings().first()

        if row and row["score"] > 0.9:
            logger.info("Skipping duplicate memory (score=%.3f): %s", row["score"], fact[:50])
            continue

        memory = Memory(
            user_id=body.user_id,
            content=fact,
            embedding=embedding,
            source_chat_id=body.chat_id,
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        saved.append(memory)

    return saved
