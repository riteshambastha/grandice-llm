from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grandice_domain import AGENTS, METHODOLOGY_VERSION, get_agent

from ..auth import ApiKey, require_api_key
from ..domain_runtime import (
    domain_envelope,
    enforce_domain_privacy,
    new_request_id,
    read_domain_json,
    record_domain_run,
)
from .market import MARKET_MAX_RESPONSE_BYTES, _run_in_killable_process
from .openai import _enforce_limit

router = APIRouter(prefix="/v1/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    inputs: dict[str, Any]


@router.get("")
async def list_agents(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    _enforce_limit(key)
    return {
        "object": "list",
        "data": [
            definition.public_dict()
            for definition in sorted(AGENTS.values(), key=lambda item: item.id)
        ],
    }


@router.post(
    "/runs",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AgentRunRequest.model_json_schema()
                }
            },
        }
    },
)
async def run_agent(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    _enforce_limit(key)
    started = time.monotonic()
    request_id = new_request_id()
    privacy_mode = "unvalidated"
    try:
        raw_run = await read_domain_json(request)
        privacy_mode = enforce_domain_privacy(
            request,
            raw_run,
            policy="strict-v1",
        )
        run = AgentRunRequest.model_validate_json(
            json.dumps(raw_run, separators=(",", ":"))
        )
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False)
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=422,
            started=started,
            input_schema="AgentRunRequest",
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
        )
        raise HTTPException(
            422,
            {
                "message": "Agent run envelope failed strict validation.",
                "errors": errors[:50],
                "omitted_error_count": max(0, len(errors) - 50),
            },
        ) from exc
    except HTTPException as exc:
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=exc.status_code,
            started=started,
            input_schema="AgentRunRequest",
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
        )
        raise
    try:
        definition = get_agent(run.agent)
    except ValueError as exc:
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=404,
            started=started,
            input_schema="AgentRunRequest",
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
            agent=None,
        )
        raise HTTPException(404, str(exc)) from exc

    try:
        privacy_mode = enforce_domain_privacy(
            request,
            run.inputs,
            policy=definition.privacy_policy,
        )
        # JSON-mode validation accepts exact decimal/date strings while retaining
        # strict schemas and rejecting floats, coercive booleans, and extra fields.
        payload = definition.input_model.model_validate_json(
            json.dumps(run.inputs, separators=(",", ":"))
        )
        try:
            if definition.domain == "market_research":
                result = await _run_in_killable_process(
                    definition.analyzer,
                    payload,
                    deadline=started + definition.execution_timeout_seconds,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(definition.analyzer, payload),
                    timeout=definition.execution_timeout_seconds,
                )
        except TimeoutError as exc:
            raise HTTPException(
                503,
                "Agent execution exceeded its bounded processing deadline.",
            ) from exc
        if (
            definition.domain == "market_research"
            and len(result.model_dump_json().encode("utf-8"))
            > MARKET_MAX_RESPONSE_BYTES
        ):
            raise HTTPException(
                500,
                "Market research output exceeded the bounded response limit.",
            )
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False)
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=422,
            started=started,
            input_schema=definition.input_model.__name__,
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
            agent=definition.id,
        )
        raise HTTPException(
            422,
            {
                "message": "Agent inputs failed strict schema validation.",
                "errors": errors[:50],
                "omitted_error_count": max(0, len(errors) - 50),
            },
        ) from exc
    except HTTPException as exc:
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=exc.status_code,
            started=started,
            input_schema=definition.input_model.__name__,
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
            agent=definition.id,
        )
        raise
    except Exception:
        await record_domain_run(
            request_id=request_id,
            key=key,
            endpoint="/v1/agents/runs",
            privacy_mode=privacy_mode,
            status=500,
            started=started,
            input_schema=definition.input_model.__name__,
            output_schema=None,
            source_count=0,
            warning_count=0,
            methodology_version=METHODOLOGY_VERSION,
            agent=definition.id,
        )
        raise

    await record_domain_run(
        request_id=request_id,
        key=key,
        endpoint="/v1/agents/runs",
        privacy_mode=privacy_mode,
        status=200,
        started=started,
        input_schema=type(payload).__name__,
        output_schema=type(result).__name__,
        source_count=len(payload.metadata.sources),
        warning_count=len(result.warnings),
        methodology_version=result.methodology_version,
        agent=definition.id,
    )
    response.headers["X-Grandice-Request-Id"] = request_id
    response.headers["X-Grandice-Content-Retained"] = "false"
    if definition.domain == "legal":
        response.headers["X-Grandice-Legal-Advice"] = "false"
    if definition.domain == "market_research":
        response.headers["X-Grandice-Market-Advice"] = "false"
        response.headers["X-Grandice-Source-Verification"] = "unverified"
    envelope = domain_envelope(
        request_id=request_id,
        privacy_mode=privacy_mode,
        data=result,
    )
    envelope["agent"] = definition.public_dict()
    envelope["status"] = "completed"
    return envelope

