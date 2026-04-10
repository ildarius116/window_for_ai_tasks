from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MemoryCreate(BaseModel):
    user_id: str
    content: str
    source_chat_id: str | None = None


class MemoryOut(BaseModel):
    id: UUID
    user_id: str
    content: str
    source_chat_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemorySearch(BaseModel):
    user_id: str
    query: str
    limit: int = 5


class MemorySearchResult(MemoryOut):
    score: float


class ExtractRequest(BaseModel):
    user_id: str
    chat_id: str
    messages: list[dict]
