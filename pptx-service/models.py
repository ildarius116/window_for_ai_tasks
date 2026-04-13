from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Slide(BaseModel):
    title: str
    bullets: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    image_prompt: Optional[str] = None


class PresentationSchema(BaseModel):
    title: str
    subtitle: Optional[str] = None
    slides: list[Slide] = Field(default_factory=list)
    style: str = "mws"
    cover_image_prompt: Optional[str] = None
