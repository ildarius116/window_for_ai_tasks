import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.embedding import EmbeddingError, get_embedding
from app.episodes import SummaryError, generate_summary
from app.models import ConversationEpisode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/episodes", tags=["episodes"])


class EpisodeCreate(BaseModel):
    user_id: str
    chat_id: str
    messages: list[dict]
    message_indices: list[int]
    turn_start_at: datetime
    turn_end_at: datetime


class EpisodeOut(BaseModel):
    id: UUID
    user_id: str
    chat_id: str
    turn_start_at: datetime
    turn_end_at: datetime
    summary: str
    message_indices: list[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class EpisodeRecall(BaseModel):
    user_id: str
    query: str
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = 5


class EpisodeRecallResult(BaseModel):
    id: UUID
    chat_id: str
    turn_start_at: datetime
    turn_end_at: datetime
    summary: str
    message_indices: list[int]
    score: float


@router.post("", response_model=EpisodeOut)
async def create_episode(
    body: EpisodeCreate, db: AsyncSession = Depends(get_db)
):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    try:
        summary = await generate_summary(body.messages)
    except SummaryError as e:
        logger.error("Summary generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"summary failed: {e}")

    try:
        embedding = await get_embedding(summary)
    except EmbeddingError as e:
        logger.error("Embedding failed: %s", e)
        raise HTTPException(status_code=502, detail=f"embedding failed: {e}")

    episode = ConversationEpisode(
        user_id=body.user_id,
        chat_id=body.chat_id,
        turn_start_at=body.turn_start_at,
        turn_end_at=body.turn_end_at,
        summary=summary,
        message_indices=body.message_indices,
        embedding=embedding,
    )
    db.add(episode)
    await db.commit()
    await db.refresh(episode)
    return episode


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


@router.post("/recall", response_model=list[EpisodeRecallResult])
async def recall_episodes(
    body: EpisodeRecall, db: AsyncSession = Depends(get_db)
):
    try:
        qvec = await get_embedding(body.query)
    except EmbeddingError as e:
        logger.error("Embedding failed: %s", e)
        raise HTTPException(status_code=502, detail=f"embedding failed: {e}")

    limit = max(1, min(body.limit, 20))
    qvec_str = _vec_literal(qvec)

    sql = text(
        """
        SELECT id, chat_id, turn_start_at, turn_end_at, summary, message_indices,
               1 - (embedding <=> CAST(:qvec AS vector)) AS score
        FROM conversation_episodes
        WHERE user_id = :user_id
          AND (CAST(:date_from AS timestamptz) IS NULL OR turn_end_at >= CAST(:date_from AS timestamptz))
          AND (CAST(:date_to   AS timestamptz) IS NULL OR turn_end_at <= CAST(:date_to   AS timestamptz))
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
        """
    )
    result = await db.execute(
        sql,
        {
            "qvec": qvec_str,
            "user_id": body.user_id,
            "date_from": body.date_from,
            "date_to": body.date_to,
            "limit": limit,
        },
    )
    rows = result.mappings().all()
    return [EpisodeRecallResult(**dict(row)) for row in rows]
