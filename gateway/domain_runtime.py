from __future__ import annotations

import asyncio
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from grandice_privacy import PrivacyEngine
from grandice_privacy.media import MediaPrivacyError

from . import db
from .auth import ApiKey
from .config import get_settings

DOMAIN_PRIVACY_MODES = {"off", "client", "sidecar", "hosted"}
MAX_DOMAIN_BODY_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 14
MAX_JSON_NODES = 25_000
DOMAIN_AUDIT_PURGE_INTERVAL_SECONDS = 60 * 60
_last_domain_audit_purge = 0.0
_domain_audit_purge_lock = asyncio.Lock()


def new_request_id() -> str:
    return f"dom_{secrets.token_urlsafe(14)}"


async def read_domain_json(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_DOMAIN_BODY_BYTES:
                raise HTTPException(413, "Domain request exceeds the 2 MiB limit.")
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length header.") from exc
    body = await request.body()
    if len(body) > MAX_DOMAIN_BODY_BYTES:
        raise HTTPException(413, "Domain request exceeds the 2 MiB limit.")

    def reject_constant(value: str) -> None:
        raise ValueError(f"Invalid JSON number {value}")

    try:
        data = json.loads(body, parse_constant=reject_constant)
    except RecursionError as exc:
        raise HTTPException(413, "Domain request nesting is too deep.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, "Request body must be valid UTF-8 JSON.") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "Domain request body must be a JSON object.")

    nodes = 0
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise HTTPException(413, "Domain request contains too many JSON values.")
        if depth > MAX_JSON_DEPTH:
            raise HTTPException(413, "Domain request nesting is too deep.")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    return data


def enforce_domain_privacy(
    request: Request,
    payload: BaseModel | dict[str, Any],
    *,
    policy: str,
) -> str:
    """Reject sensitive values before deterministic domain processing.

    Domain calculation schemas intentionally do not accept identity fields.
    Hosted mode therefore validates and rejects residual values instead of
    mutating typed financial inputs after validation.
    """
    mode = request.headers.get("X-Grandice-Privacy-Mode", "off").strip().lower()
    if mode not in DOMAIN_PRIVACY_MODES:
        raise HTTPException(
            400,
            "X-Grandice-Privacy-Mode must be off, client, sidecar, or hosted.",
        )
    data = (
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else payload
    )
    try:
        residual = PrivacyEngine(
            policy, processing_location="hosted"
        ).inspect(data)
    except (ValueError, MediaPrivacyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if residual.findings:
        raise HTTPException(
            422,
            "Domain APIs do not accept detectable personal or secret values. "
            f"Detected categories: {residual.receipt.entities_detected}. "
            "Tokenize locally and resubmit; the request was not processed.",
        )
    return mode


async def record_domain_run(
    *,
    request_id: str,
    key: ApiKey,
    endpoint: str,
    privacy_mode: str,
    status: int,
    started: float,
    input_schema: str,
    output_schema: str | None,
    source_count: int,
    warning_count: int,
    methodology_version: str | None,
    agent: str | None = None,
) -> None:
    """Persist audit metadata only; request and response bodies are excluded."""
    await db.execute(
        """INSERT INTO domain_runs
           (request_id, ts, key_id, org_id, member_id, endpoint, agent,
            privacy_mode, status, duration_ms, input_schema, output_schema,
            source_count, warning_count, methodology_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            key.id,
            key.org_id,
            key.member_id,
            endpoint,
            agent,
            privacy_mode,
            status,
            int((time.monotonic() - started) * 1000),
            input_schema,
            output_schema,
            source_count,
            warning_count,
            methodology_version,
        ),
    )
    await _purge_expired_domain_runs()


async def _purge_expired_domain_runs() -> None:
    global _last_domain_audit_purge
    now = time.monotonic()
    if now - _last_domain_audit_purge < DOMAIN_AUDIT_PURGE_INTERVAL_SECONDS:
        return
    async with _domain_audit_purge_lock:
        now = time.monotonic()
        if now - _last_domain_audit_purge < DOMAIN_AUDIT_PURGE_INTERVAL_SECONDS:
            return
        retention_days = max(1, get_settings().domain_audit_retention_days)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat(timespec="seconds")
        await db.execute("DELETE FROM domain_runs WHERE ts < ?", (cutoff,))
        _last_domain_audit_purge = now


def domain_envelope(
    *,
    request_id: str,
    privacy_mode: str,
    data: BaseModel,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "privacy": {
            "mode": privacy_mode,
            "mode_attested": False,
            "mode_source": "caller_asserted_header",
            "residual_check": {
                "status": "passed",
                "coverage": "configured_policy_detectors",
                "guarantee": "best_effort",
            },
            "content_retained": False,
        },
        "data": data.model_dump(mode="json"),
    }

