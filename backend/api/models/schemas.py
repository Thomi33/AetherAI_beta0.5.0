from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class MessageBase(BaseModel):
    content: str
    role: str  # 'user' or 'assistant'

class MessageCreate(BaseModel):
    message: str

class Message(MessageBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True

class ConversationBase(BaseModel):
    title: str

class ConversationCreate(ConversationBase):
    pass

class Conversation(ConversationBase):
    id: int
    messages: List[Message] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ChatResponse(BaseModel):
    response: str
    timestamp: datetime

class ErrorResponse(BaseModel):
    detail: str
    status_code: int
