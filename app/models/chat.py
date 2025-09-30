from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class Role(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatMessage(BaseModel):
    role: Role
    text: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., min_items=1)
    fileContext: Optional[list[str]] = Field(
        default=None,
        description="Liste d'IDs de fichiers pour contextualiser"
    )
    maxTokens: Optional[int] = Field(default=None, ge=16, le=4096)


class ChatResponse(BaseModel):
    reply: str
