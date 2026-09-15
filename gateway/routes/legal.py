from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from grandice_domain import (
    LEGAL_METHODOLOGY_VERSION,
    ContractAnalysisInput,
    ContractComparisonInput,
    analyze_contract,
    compare_contracts,
)

from ..auth import ApiKey, require_api_key
from ..domain_runtime import (
    domain_envelope,
    enforce_domain_privacy,
    new_request_id,
    read_domain_json,
    record_domain_run,
)
from .openai import _enforce_limit

router = APIRouter(prefix="/v1/legal/contracts", tags=["legal"])

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


async def _execute(
    *,
    endpoint: str,
    request: Request,
    response: Response,
    key: ApiKey,
    input_model: type[InputModel],
    analyzer: Callable[[InputModel], OutputModel],
    timeout_seconds: float,
) -> dict[str, Any]:
    _enforce_limit(key)
    started = time.monotonic()
    request_id = new_request_id()
    privacy_mode = "unvalidated"
    payload: InputModel | None = None
    try:
        raw_payload = await read_domain_json(request)
        privacy_mode = enforce_domain_privacy(
            request,
            raw_payload,
            policy="legal-strict-v1",
        )
        payload = input_model.model_validate_json(
            json.dumps(raw_payload, separators=(",", ":"))
        )
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(analyzer, payload),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise HTTPException(
                503,
                "Legal analysis exceeded its bounded processing deadline.",
            ) from exc
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False)
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint=endpoint,
            privacy_mode=privacy_mode,
            status=422,
            started=started,
            input_schema=input_model.__name__,
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=LEGAL_METHODOLOGY_VERSION,
        )
        raise HTTPException(
            422,
            {
                "message": "Legal inputs failed strict schema validation.",
                "errors": errors[:50],
                "omitted_error_count": max(0, len(errors) - 50),
            },
        ) from exc
    except Exception as exc:
        status = getattr(exc, "status_code", 500)
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint=endpoint,
            privacy_mode=privacy_mode,
            status=status,
            started=started,
            input_schema=input_model.__name__,
            output_schema=None,
            source_count=(
                len(payload.metadata.sources) if payload is not None else 0
            ),
            warning_count=0,
            methodology_version=LEGAL_METHODOLOGY_VERSION,
        )
        raise

    await record_domain_run(
        request_id=request_id,
        key=key,
        endpoint=endpoint,
        privacy_mode=privacy_mode,
        status=200,
        started=started,
        input_schema=input_model.__name__,
        output_schema=type(result).__name__,
        source_count=len(payload.metadata.sources),
        warning_count=len(result.warnings),
        methodology_version=result.methodology_version,
    )
    response.headers["X-Grandice-Request-Id"] = request_id
    response.headers["X-Grandice-Content-Retained"] = "false"
    response.headers["X-Grandice-Legal-Advice"] = "false"
    return domain_envelope(
        request_id=request_id,
        privacy_mode=privacy_mode,
        data=result,
    )


@router.post(
    "/review",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ContractAnalysisInput.model_json_schema()
                }
            },
        }
    },
)
async def review_contract(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/legal/contracts/review",
        request=request,
        response=response,
        key=key,
        input_model=ContractAnalysisInput,
        analyzer=analyze_contract,
        timeout_seconds=5.0,
    )


@router.post(
    "/compare",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ContractComparisonInput.model_json_schema()
                }
            },
        }
    },
)
async def compare_contract_versions(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/legal/contracts/compare",
        request=request,
        response=response,
        key=key,
        input_model=ContractComparisonInput,
        analyzer=compare_contracts,
        timeout_seconds=10.0,
    )

