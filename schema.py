"""Request/response models used by router.py."""

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    language: str = "en"  # "en" | "gu" | "hi" — see service.LANGUAGE_NAMES


class ChatResponse(BaseModel):
    reply: str
    # TEMPORARY — latency diagnosis only (Render/Oregon vs Neon/Singapore
    # region mismatch investigation). Remove once resolved; see service.chat().
    timing_ms: Optional[dict] = None
