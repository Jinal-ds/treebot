"""Request/response models used by router.py."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    language: str = "en"  # "en" | "gu" | "hi" — see service.LANGUAGE_NAMES


class ChatResponse(BaseModel):
    reply: str
