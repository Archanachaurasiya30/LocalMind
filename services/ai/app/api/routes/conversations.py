"""
REST routes for conversation persistence - Phase 3.5.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db import conversations as db

router = APIRouter(prefix="/conversations", tags=["conversations"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    title: str


class SaveTurnRequest(BaseModel):
    user_content: str
    assistant_content: str
    assistant_thinking: str | None = None
    sources: list[dict] = []
    attachment_ids: list[str] = []


class RenameRequest(BaseModel):
    title: str


class InterruptedTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=36, max_length=36)
    user_content: str = Field(max_length=1_000_000)
    assistant_content: str = Field(max_length=2_000_000)
    assistant_thinking: str | None = Field(default=None, max_length=2_000_000)
    error: str | None = Field(default=None, max_length=2000)
    cancelled: bool = False
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("")
def create_conversation(req: CreateConversationRequest) -> dict:
    return db.create_conversation(req.title)


@router.get("")
def list_conversations() -> dict:
    return {"conversations": db.list_conversations()}


@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str) -> dict:
    return {"messages": db.get_messages(conversation_id)}


@router.post("/{conversation_id}/turn")
def save_turn(conversation_id: str, req: SaveTurnRequest) -> dict:
    try:
        db.save_turn(
            conversation_id, req.user_content, req.assistant_content,
            req.assistant_thinking, req.sources, req.attachment_ids,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True}


@router.post("/{conversation_id}/interrupted")
def save_interrupted_turn(conversation_id: str, req: InterruptedTurnRequest) -> dict:
    try:
        db.save_interrupted_turn(conversation_id, **req.model_dump())
    except LookupError:
        raise HTTPException(404, "Conversation not found.") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    if not db.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.patch("/{conversation_id}")
def rename_conversation(conversation_id: str, req: RenameRequest) -> dict:
    if not db.rename_conversation(conversation_id, req.title):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}
