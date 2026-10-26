"""A8 SEO & Thumbnail output schema (S18 packaging)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChapterSchema(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    start_ms: int = Field(ge=0)


class ThumbSchema(BaseModel):
    headline: str = Field(min_length=1, max_length=90)
    subhead: str = Field(default="", max_length=90)
    palette: str = Field(default="midnight")


class SeoSchema(BaseModel):
    title_variants: list[str] = Field(min_length=1, max_length=3)
    description: str = Field(min_length=1, max_length=5000)
    chapters: list[ChapterSchema] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=20)
    thumb: ThumbSchema
