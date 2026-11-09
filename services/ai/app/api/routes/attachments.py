"""Bounded clipboard uploads through the authenticated desktop bridge."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from app import attachments

router = APIRouter(prefix="/attachments", tags=["attachments"])


class Upload(BaseModel):
    filename: str = Field(max_length=512)
    data: str = Field(max_length=((attachments.MAX_BYTES + 2) // 3) * 4)
    draft_id: str
    conversation_id: str | None = None
    mode: Literal['vision', 'ocr'] = 'vision'


class Owner(BaseModel):
    draft_id: str | None = None
    conversation_id: str | None = None


class Preview(Owner):
    id: str


@router.post("")
def upload(req: Upload):
    try:
        return attachments.create(req.filename, req.data, req.draft_id, req.conversation_id, req.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/preview")
def preview(req: Preview):
    try:
        return attachments.preview(req.id, req.conversation_id, req.draft_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.delete("/{key}")
def discard(key: str, req: Owner):
    try:
        attachments.discard(key, req.draft_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
