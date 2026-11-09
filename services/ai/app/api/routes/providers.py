"""Authenticated desktop endpoints for user-owned cloud API accounts."""

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app import providers
from app import provider_store as store
from app.credentials import CredentialUnavailable

router = APIRouter(prefix="/providers", tags=["providers"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Config(Strict):
    model: str | None = None
    vision: bool | None = None
    input_price_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)


class Policy(Strict):
    local_only: bool | None = None
    monthly_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)


class Consent(Strict):
    scopes: list[str] = Field(max_length=6)


class Model(Strict):
    model: str = Field(min_length=1, max_length=200)


async def execute(work):
    import inspect
    try:
        result = work()
        return await result if inspect.isawaitable(result) else result
    except CredentialUnavailable:
        raise HTTPException(400, {"code": "credential_store", "message": "Unlock your operating system credential store and retry. No key was stored in a file."}) from None
    except store.ProviderError as exc:
        raise HTTPException(400, {"code": exc.code, "message": str(exc)}) from None


@router.get("")
async def status():
    return await execute(providers.status)


@router.patch("/policy")
async def policy(body: Policy):
    return await execute(lambda: store.configure_policy(body.model_dump(exclude_unset=True)))


@router.put("/{provider}/credential")
async def credential(provider: str, request: Request):
    # FastAPI's standard validation response includes the rejected input. Parse
    # this one secret-bearing body explicitly so a malformed key is never echoed.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(400, {"code": "credential", "message": "The API key is too long."})
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {"key"}:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(400, {"code": "credential", "message": "Enter one API key."}) from None
    return await execute(lambda: providers.set_key(provider, body["key"]))


@router.delete("/{provider}/credential")
async def disconnect(provider: str):
    return await execute(lambda: providers.disconnect(provider))


@router.get("/{provider}/models")
async def models(provider: str):
    return await execute(lambda: providers.list_models(provider))


@router.post("/{provider}/check")
async def check(provider: str, body: Model):
    return await execute(lambda: providers.check_model(provider, body.model))


@router.patch("/{provider}/config")
async def config(provider: str, body: Config):
    return await execute(lambda: store.configure(provider, body.model_dump(exclude_unset=True)))


@router.put("/{provider}/consent")
async def consent(provider: str, body: Consent):
    return await execute(lambda: store.approve(provider, body.scopes))


@router.delete("/{provider}/consent")
async def revoke(provider: str):
    return await execute(lambda: store.revoke(provider))
