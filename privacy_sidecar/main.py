from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from grandice_privacy import PrivacyEngine, TokenVault
from grandice_privacy.media import MediaPrivacyError, media_capabilities
from grandice_privacy.media_service import (
    protect_base64_media,
    protect_embedded_data_urls,
    restore_embedded_data_urls,
)

UPSTREAM = os.getenv("GRANDICE_SIDECAR_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_POLICY = os.getenv("GRANDICE_PRIVACY_POLICY", "strict-v1")
_client: httpx.AsyncClient | None = None


def _whisper_model_path() -> str | None:
    configured = os.getenv("GRANDICE_PRIVACY_WHISPER_MODEL")
    path = (
        Path(configured).expanduser()
        if configured
        else Path.home()
        / ".grandice"
        / "models"
        / "faster-whisper-large-v3-turbo"
    )
    return str(path) if path.is_dir() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    yield
    await _client.aclose()
    _client = None


app = FastAPI(
    title="Grandice Local Privacy Sidecar",
    version="0.1.0",
    description="Local zero-transfer privacy proxy for Grandice JSON APIs.",
    lifespan=lifespan,
)


def _json_keys(data: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            keys.append(str(key))
            keys.extend(_json_keys(value))
    elif isinstance(data, list):
        for value in data:
            keys.extend(_json_keys(value))
    return keys


def _reject_residual(engine: PrivacyEngine, data: Any, location: str) -> None:
    result = engine.inspect(data)
    if result.findings:
        raise HTTPException(
            422,
            f"Sensitive data detected in {location}: "
            f"{result.receipt.entities_detected}. The request was not forwarded.",
        )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "processing_location": "sidecar",
        "upstream": UPSTREAM,
        "raw_content_transferred": False,
        "media": media_capabilities(_whisper_model_path()).as_dict(),
    }


class LocalMediaPrivacyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str
    media_type: str
    policy: str = DEFAULT_POLICY


@app.post("/v1/privacy/media/redact")
async def redact_media(request: LocalMediaPrivacyRequest) -> dict[str, Any]:
    try:
        result = protect_base64_media(
            content_base64=request.content_base64,
            media_type=request.media_type,
            policy_id=request.policy,
            processing_location="sidecar",
            whisper_model_path=_whisper_model_path(),
            max_bytes=int(
                os.getenv("GRANDICE_PRIVACY_MEDIA_MAX_BYTES", str(50 * 1024 * 1024))
            ),
        )
    except (MediaPrivacyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return result.as_dict()


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request):
    if _client is None:
        raise HTTPException(503, "Sidecar is still starting.")
    if request.query_params:
        raise HTTPException(
            422,
            "Query parameters are refused by zero-transfer mode. Put inputs in "
            "the protected JSON request body.",
        )

    content_type = request.headers.get("content-type", "")
    if request.method in {"POST", "PUT", "PATCH"} and not content_type.startswith(
        "application/json"
    ):
        raise HTTPException(
            415,
            "This sidecar build accepts JSON only. It refuses media and multipart "
            "payloads until complete local multimodal coverage is installed.",
        )

    body: Any | None = None
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "Request body must be valid JSON.") from exc
        if isinstance(body, dict) and body.get("stream"):
            raise HTTPException(
                400,
                "Streaming is refused until token-safe incremental rehydration is enabled.",
            )

    policy = request.headers.get("X-Grandice-Privacy-Policy", DEFAULT_POLICY)
    vault = TokenVault()
    try:
        engine = PrivacyEngine(
            policy, processing_location="sidecar", vault=vault
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    _reject_residual(engine, path, "the URL path")
    _reject_residual(engine, _json_keys(body), "JSON property names")
    whisper_model = _whisper_model_path()
    media_limit = int(
        os.getenv("GRANDICE_PRIVACY_MEDIA_MAX_BYTES", str(50 * 1024 * 1024))
    )
    try:
        body_without_media, protected_media = protect_embedded_data_urls(
            body,
            policy_id=policy,
            processing_location="sidecar",
            whisper_model_path=whisper_model,
            max_bytes=media_limit,
        )
        tokenized_body = (
            engine.tokenize(body_without_media).data
            if body_without_media is not None
            else None
        )
        protected_body = restore_embedded_data_urls(tokenized_body, protected_media)
    except MediaPrivacyError as exc:
        raise HTTPException(422, str(exc)) from exc
    allowed_headers = {"authorization", "x-api-key", "accept"}
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in allowed_headers
    }
    headers["X-Grandice-Privacy-Mode"] = "sidecar"
    headers["X-Grandice-Privacy-Policy"] = policy

    url = f"{UPSTREAM}/v1/{path}"
    try:
        upstream_response = await _client.request(
            request.method,
            url,
            params=request.query_params,
            json=protected_body,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Cannot reach Grandice upstream: {exc}") from exc

    response_type = upstream_response.headers.get("content-type", "")
    if not response_type.startswith("application/json"):
        raise HTTPException(
            502,
            "The upstream returned a non-JSON response that cannot be privacy checked.",
        )

    payload = upstream_response.json()
    # Remove newly generated sensitive values, then restore only values that were
    # explicitly substituted from this request inside the local process.
    try:
        payload_without_media, response_media = protect_embedded_data_urls(
            payload,
            policy_id=policy,
            processing_location="sidecar",
            whisper_model_path=whisper_model,
            max_bytes=media_limit,
        )
        locally_safe = engine.mask(payload_without_media).data
    except MediaPrivacyError as exc:
        raise HTTPException(
            502, f"Upstream response was blocked by local privacy policy: {exc}"
        ) from exc
    restored = restore_embedded_data_urls(
        engine.restore_response(locally_safe), response_media
    )
    response = JSONResponse(restored, status_code=upstream_response.status_code)
    for name, value in upstream_response.headers.items():
        lowered = name.lower()
        if lowered in {"retry-after", "x-request-id"} or lowered.startswith(
            "x-ratelimit-"
        ):
            response.headers[name] = value
    response.headers["X-Grandice-Privacy-Mode"] = "sidecar"
    response.headers["X-Grandice-Content-Retained"] = "false"
    return response


def run() -> None:
    import uvicorn

    host = os.getenv("GRANDICE_SIDECAR_HOST", "127.0.0.1")
    if host.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "The privacy sidecar must bind to loopback. Network exposure requires "
            "a separately authenticated and TLS-protected deployment."
        )
    port = int(os.getenv("GRANDICE_SIDECAR_PORT", "8090"))
    uvicorn.run("privacy_sidecar.main:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    run()

