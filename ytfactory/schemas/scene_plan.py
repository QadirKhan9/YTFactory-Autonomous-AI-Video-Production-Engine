"""Scene Director (A7) + Edit Decision List schemas (design.md §2.2/§2.3)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SceneSchema(BaseModel):
    ord: int = Field(..., description="1-based scene sequence index")
    sentence_ids: List[int] = Field(..., min_length=1, description="Mapped sentence ordinals")
    visual_intent: str = Field(..., description="Visual concept description")
    stock_queries: List[str] = Field(..., max_length=5, description="Search queries for stock media")
    gen_prompt: str = Field(..., description="FLUX.1 prompt for image synthesis fallback")
    motion: str = Field("kenburns_zoom_in", description="Camera animation type")


class ScenePlanSchema(BaseModel):
    scenes: List[SceneSchema] = Field(..., min_length=1)


MOTIONS = (
    "kenburns_zoom_in",
    "kenburns_zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "static",
)


class EDLSceneAsset(BaseModel):
    sha256: str
    kind: str  # "image" | "video" | "typographic_card"
    license_id: str
    gate_status: str  # must be "approved"


class EDLScene(BaseModel):
    ord: int
    start_ms: int
    end_ms: int
    duration_ms: int
    frames: int
    asset: EDLSceneAsset
    motion: dict
    overlay: Optional[dict] = None


class EDLSchema(BaseModel):
    version: int = 1
    output_width: int = 1920
    output_height: int = 1080
    fps: int = 30
    pix_fmt: str = "yuv420p"
    master_audio_sha256: str
    master_audio_lufs: float = -14.0
    master_audio_duration_ms: int
    scenes: List[EDLScene] = Field(..., min_length=1)
