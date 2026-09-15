from __future__ import annotations

import asyncio
import json
import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from grandice_domain import (
    MARKET_METHODOLOGY_VERSION,
    CompetitiveLandscapeInput,
    MarketResearchInput,
    analyze_market_evidence,
    compare_competitive_landscape,
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

router = APIRouter(prefix="/v1/market", tags=["market research"])
MARKET_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MARKET_PROCESS_CAPACITY = 2
_market_process_slots = asyncio.Semaphore(MARKET_PROCESS_CAPACITY)
_market_process_context = multiprocessing.get_context("spawn")

InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


def _market_process_worker(
    connection: Connection,
    analyzer: Callable[[Any], BaseModel],
    payload: BaseModel,
) -> None:
    try:
        connection.send(("ok", analyzer(payload)))
    except BaseException:
        connection.send(("error", None))
    finally:
        connection.close()


async def _run_in_killable_process(
    analyzer: Callable[[InputModel], OutputModel],
    payload: InputModel,
    *,
    deadline: float,
) -> OutputModel:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    await asyncio.wait_for(_market_process_slots.acquire(), timeout=remaining)
    receive_connection: Connection | None = None
    process: multiprocessing.Process | None = None
    try:
        receive_connection, send_connection = _market_process_context.Pipe(
            duplex=False
        )
        process = _market_process_context.Process(
            target=_market_process_worker,
            args=(send_connection, analyzer, payload),
            daemon=True,
            name="grandice-market-analysis",
        )
        process.start()
        send_connection.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            outcome, result = await asyncio.wait_for(
                asyncio.to_thread(receive_connection.recv),
                timeout=remaining,
            )
        except EOFError as exc:
            raise RuntimeError("Market analysis worker exited unexpectedly.") from exc
        if outcome != "ok":
            raise RuntimeError("Market analysis worker failed.")
        return result
    finally:
        if receive_connection is not None:
            receive_connection.close()
        if process is not None:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 1.0)
            process.close()
        _market_process_slots.release()


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
    deadline = started + timeout_seconds
    request_id = new_request_id()
    privacy_mode = "unvalidated"
    payload: InputModel | None = None
    try:
        raw_payload = await read_domain_json(request)
        privacy_mode = enforce_domain_privacy(
            request,
            raw_payload,
            policy="market-strict-v1",
        )
        payload = input_model.model_validate_json(
            json.dumps(raw_payload, separators=(",", ":"))
        )
        try:
            result = await _run_in_killable_process(
                analyzer,
                payload,
                deadline=deadline,
            )
        except TimeoutError as exc:
            raise HTTPException(
                503,
                "Market research analysis exceeded its bounded processing deadline.",
            ) from exc
        if len(result.model_dump_json().encode("utf-8")) > MARKET_MAX_RESPONSE_BYTES:
            raise HTTPException(
                500,
                "Market research output exceeded the bounded response limit.",
            )
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
            methodology_version=MARKET_METHODOLOGY_VERSION,
        )
        raise HTTPException(
            422,
            {
                "message": "Market research inputs failed strict schema validation.",
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
            methodology_version=MARKET_METHODOLOGY_VERSION,
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
    response.headers["X-Grandice-Market-Advice"] = "false"
    response.headers["X-Grandice-Source-Verification"] = "unverified"
    return domain_envelope(
        request_id=request_id,
        privacy_mode=privacy_mode,
        data=result,
    )


@router.post(
    "/research/analyze",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": MarketResearchInput.model_json_schema()
                }
            },
        }
    },
)
async def analyze_market_research(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/market/research/analyze",
        request=request,
        response=response,
        key=key,
        input_model=MarketResearchInput,
        analyzer=analyze_market_evidence,
        timeout_seconds=8.0,
    )


@router.post(
    "/competitive-landscape/compare",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CompetitiveLandscapeInput.model_json_schema()
                }
            },
        }
    },
)
async def compare_market_competitors(
    request: Request,
    response: Response,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    return await _execute(
        endpoint="/v1/market/competitive-landscape/compare",
        request=request,
        response=response,
        key=key,
        input_model=CompetitiveLandscapeInput,
        analyzer=compare_competitive_landscape,
        timeout_seconds=10.0,
    )

