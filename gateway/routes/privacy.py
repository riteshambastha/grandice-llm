from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from grandice_privacy import POLICIES, PrivacyEngine, get_policy, public_policy
from grandice_privacy.media import MediaPrivacyError, media_capabilities
from grandice_privacy.media_service import protect_base64_media

from ..auth import ApiKey, require_api_key
from ..config import get_settings
from .openai import _enforce_limit

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])


class PrivacyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any
    policy: str = "general-v1"
    custom_literals: dict[str, list[str]] = Field(default_factory=dict)


class PrivacyResponse(BaseModel):
    data: Any
    findings: list[dict[str, Any]]
    receipt: dict[str, Any]


class MediaPrivacyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str
    media_type: str
    policy: str = "strict-v1"


def _run(request: PrivacyRequest, operation: Literal["detect", "mask", "tokenize"]):
    try:
        engine = PrivacyEngine(
            request.policy,
            custom_literals=request.custom_literals,
            processing_location="hosted",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        if operation == "detect":
            result = engine.inspect(request.data)
        elif operation == "mask":
            result = engine.mask(request.data)
        else:
            result = engine.tokenize(request.data)
    except MediaPrivacyError as exc:
        raise HTTPException(422, str(exc)) from exc

    return PrivacyResponse(
        data=result.data,
        findings=[finding.public_dict() for finding in result.findings],
        receipt=result.receipt.as_dict(),
    )


@router.get("/capabilities")
async def capabilities(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    settings = get_settings()
    return {
        "processing_location": "hosted",
        "raw_content_transferred": True,
        "content_retained": False,
        "structured_data": True,
        "media": media_capabilities(
            settings.privacy_whisper_model_path
        ).as_dict(),
    }


@router.get("/policies")
async def list_policies(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    return {"data": [public_policy(policy) for policy in POLICIES.values()]}


@router.get("/policies/{policy_id}")
async def policy(policy_id: str, key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    try:
        return public_policy(get_policy(policy_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/detect", response_model=PrivacyResponse)
async def detect(
    request: PrivacyRequest, key: ApiKey = Depends(require_api_key)
) -> PrivacyResponse:
    _enforce_limit(key)
    return _run(request, "detect")


@router.post("/redact", response_model=PrivacyResponse)
async def redact(
    request: PrivacyRequest, key: ApiKey = Depends(require_api_key)
) -> PrivacyResponse:
    _enforce_limit(key)
    return _run(request, "mask")


@router.post("/tokenize", response_model=PrivacyResponse)
async def tokenize(
    request: PrivacyRequest, key: ApiKey = Depends(require_api_key)
) -> PrivacyResponse:
    _enforce_limit(key)
    return _run(request, "tokenize")


@router.post("/media/redact")
async def redact_media(
    request: MediaPrivacyRequest, key: ApiKey = Depends(require_api_key)
) -> dict[str, Any]:
    _enforce_limit(key)
    settings = get_settings()
    try:
        result = protect_base64_media(
            content_base64=request.content_base64,
            media_type=request.media_type,
            policy_id=request.policy,
            processing_location="hosted",
            whisper_model_path=settings.privacy_whisper_model_path or None,
            max_bytes=settings.privacy_media_max_bytes,
        )
    except (MediaPrivacyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return result.as_dict()

