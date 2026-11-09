from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app import visuals

router = APIRouter(prefix='/visuals', tags=['visuals'])


class Capture(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(max_length=36)
    source_content: str = Field(max_length=200_000)
    source_key: str = Field(max_length=100_000)
    spec: dict


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(max_length=36)
    conversation_id: str = Field(max_length=36)
    expected_version: int = Field(ge=1, le=100)
    parent: int = Field(ge=1, le=100)
    spec: dict


class ImageReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(max_length=36)
    attachment_id: str = Field(max_length=36)


class Export(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(max_length=36)
    spec: dict


@router.post('/validate')
def validate_export(body: Export):
    try:
        spec = visuals.validate(body.spec)
        if spec['type'] == 'image':
            visuals._saved_image(spec['attachment_id'], body.conversation_id)
        return {'spec': spec}
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.post('/capture-image')
def capture_image(body: ImageReference):
    try:
        return visuals.capture_image(**body.model_dump())
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.post('/capture')
def capture(body: Capture):
    try:
        return visuals.capture(**body.model_dump())
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from None


@router.post('/revise')
def revise(body: Revision):
    try:
        values = body.model_dump()
        values['identifier'] = values.pop('id')
        return visuals.revise(**values)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, str(error)) from None
