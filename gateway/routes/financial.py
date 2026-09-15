from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from grandice_domain import (
    METHODOLOGY_VERSION,
    CompanyAnalysisInput,
    PortfolioAnalysisInput,
    RiskAssessmentInput,
    analyze_company,
    analyze_portfolio,
    assess_client_risk,
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

router = APIRouter(prefix="/v1/financial", tags=["financial"])

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
    agent: str | None = None,
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
            policy="financial-strict-v1",
        )
        payload = input_model.model_validate_json(
            json.dumps(raw_payload, separators=(",", ":"))
        )
        result = analyzer(payload)
    except ValidationError as exc:
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
            methodology_version=METHODOLOGY_VERSION,
            agent=agent,
        )
        raise HTTPException(
            422,
            {
                "message": "Domain inputs failed strict schema validation.",
                "errors": exc.errors(
                    include_input=False,
                    include_context=False,
                ),
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
            methodology_version=METHODOLOGY_VERSION,
            agent=agent,
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
        agent=agent,
    )
    response.headers["X-Grandice-Request-Id"] = request_id
    response.headers["X-Grandice-Content-Retained"] = "false"
    return domain_envelope(
        request_id=request_id,
        privacy_mode=privacy_mode,
        data=result,
    )


@router.post(
    "/portfolio/analyze",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": PortfolioAnalysisInput.model_json_schema()
                }
            },
        }
    },
)
async def portfolio_analysis(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/financial/portfolio/analyze",
        request=request,
        response=response,
        key=key,
        input_model=PortfolioAnalysisInput,
        analyzer=analyze_portfolio,
    )


@router.post(
    "/risk-assessment",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": RiskAssessmentInput.model_json_schema()
                }
            },
        }
    },
)
async def risk_assessment(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/financial/risk-assessment",
        request=request,
        response=response,
        key=key,
        input_model=RiskAssessmentInput,
        analyzer=assess_client_risk,
    )


@router.post(
    "/company/analyze",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CompanyAnalysisInput.model_json_schema()
                }
            },
        }
    },
)
async def company_analysis(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/financial/company/analyze",
        request=request,
        response=response,
        key=key,
        input_model=CompanyAnalysisInput,
        analyzer=analyze_company,
    )

