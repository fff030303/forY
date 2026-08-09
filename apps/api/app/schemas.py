from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    relationship_type: str = Field(min_length=1, max_length=80)
    consent_confirmed: bool


class ImportRequest(BaseModel):
    format: Literal["jsonl", "wechat_text"]
    content: str = Field(min_length=1, max_length=2_000_000)


class IdentityRequest(BaseModel):
    target_speaker: str = Field(min_length=1, max_length=80)
    user_speaker: str = Field(min_length=1, max_length=80)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    timezone: Optional[str] = Field(default=None, max_length=80)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)


class LifeGuidanceUpdate(BaseModel):
    guidance: str = Field(min_length=1, max_length=5000)
    timezone: Optional[str] = Field(default=None, max_length=80)


class FeedbackRequest(BaseModel):
    rating: Literal["like", "dislike"]
    reason: Optional[str] = Field(default=None, max_length=300)
    ideal_reply: Optional[str] = Field(default=None, max_length=2000)


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    importance: float = Field(default=0.5, ge=0, le=1)
    event_date: Optional[date] = None


class TraitUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)


class EvidenceRequest(BaseModel):
    message_ids: list[str] = Field(min_length=1, max_length=50)


class CandidatePublishRequest(BaseModel):
    feedback_ids: list[str] = Field(default_factory=list)


class WechatHistoryRequest(BaseModel):
    chat: str = Field(min_length=1, max_length=100)
    self_speaker: str = Field(default="我", min_length=1, max_length=80)
    since: Optional[date] = None
    until: Optional[date] = None
    limit: int = Field(default=5000, ge=1, le=10000)


class WechatFullImportRequest(BaseModel):
    chat: str = Field(min_length=1, max_length=100)
    self_speaker: str = Field(default="我", min_length=1, max_length=80)
    since: Optional[date] = None
    until: Optional[date] = None
    page_size: int = Field(default=1000, ge=100, le=2000)
    analyze: bool = True
