import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, monitoring
from .auth import ApiKey
from .config import get_settings

_client: httpx.AsyncClient | None = None

# How much of the tail of a stream to retain while hunting for the usage chunk.
_USAGE_SCAN_BYTES = 65_536


async def startup() -> None:
    global _client
    settings = get_settings()
    _client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=httpx.Timeout(settings.request_timeout, connect=10.0),
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
    )


async def shutdown() -> None:
    if _client is not None:
        await _client.aclose()


def client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Upstream client used before startup().")
    return _client


async def record_usage(
    *,
    key: ApiKey | None,
    model: str | None,
    endpoint: str,
    usage: dict[str, Any] | None,
    started: float,
    status_code: int,
) -> None:
    usage = usage or {}
    await db.execute(
        """INSERT INTO usage_log
           (ts, key_id, app_name, model, endpoint, prompt_tokens, completion_tokens,
            total_tokens, duration_ms, status, org_id, member_id, org_name, member_name)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            key.id if key else None,
            key.name if key else None,
            model,
            endpoint,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            int((time.monotonic() - started) * 1000),
            status_code,
            key.org_id if key else None,
            key.member_id if key else None,
            key.org_name if key else None,
            key.member_name if key else None,
        ),
    )


def _usage_from_stream_tail(tail: bytes) -> dict[str, Any] | None:
    """Pull the usage object out of the last SSE data frame that carries one."""
    for line in reversed(tail.split(b"\n")):
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload in (b"[DONE]", b""):
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("usage"):
            return obj["usage"]
    return None


async def forward(
    *,
    path: str,
    body: dict[str, Any],
    key: ApiKey,
    model: str | None,
    stream: bool,
) -> JSONResponse | StreamingResponse:
    started = time.monotonic()

    if not stream:
        async with monitoring.request_slot():
            try:
                resp = await client().post(path, json=body)
            except httpx.TimeoutException as exc:
                await record_usage(
                    key=key, model=model, endpoint=path, usage=None, started=started, status_code=504
                )
                raise HTTPException(504, f"Model timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                await record_usage(
                    key=key, model=model, endpoint=path, usage=None, started=started, status_code=502
                )
                raise HTTPException(502, f"Cannot reach Ollama: {exc}") from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": {"message": resp.text}}

        await record_usage(
            key=key,
            model=model,
            endpoint=path,
            usage=payload.get("usage") if isinstance(payload, dict) else None,
            started=started,
            status_code=resp.status_code,
        )
        return JSONResponse(payload, status_code=resp.status_code)

    async def relay() -> AsyncIterator[bytes]:
        tail = b""
        status_code = 200
        async with monitoring.request_slot():
            try:
                req = client().build_request("POST", path, json=body)
                resp = await client().send(req, stream=True)
                status_code = resp.status_code
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                        tail = (tail + chunk)[-_USAGE_SCAN_BYTES:]
                finally:
                    await resp.aclose()
            except httpx.HTTPError as exc:
                status_code = 502
                error = {"error": {"message": f"Upstream failure: {exc}", "type": "upstream_error"}}
                yield f"data: {json.dumps(error)}\n\n".encode()
            finally:
                await record_usage(
                    key=key,
                    model=model,
                    endpoint=path,
                    usage=_usage_from_stream_tail(tail),
                    started=started,
                    status_code=status_code,
                )

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
