import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from grandice_privacy import PrivacyEngine
from grandice_privacy.media import MediaPrivacyError

from .. import ratelimit, upstream
from ..auth import ApiKey, require_api_key
from ..config import default_reasoning_effort, get_settings, load_aliases, resolve_model

router = APIRouter(prefix="/v1", tags=["openai"])
_PRIVACY_MODES = {"off", "client", "sidecar", "hosted"}


def _same_model(left: str, right: str) -> bool:
    """Treat Ollama's implicit and explicit ``:latest`` tags as equivalent."""
    return left.removesuffix(":latest") == right.removesuffix(":latest")


async def _model_info(model: str) -> dict[str, Any]:
    """Translate Ollama capabilities into metadata understood by Open WebUI."""
    try:
        resp = await upstream.client().post("/api/show", json={"name": model})
        resp.raise_for_status()
        capabilities = set(resp.json().get("capabilities", []))
    except Exception:
        capabilities = set()

    return {
        "meta": {
            "capabilities": {
                # Open WebUI otherwise defaults this to true and injects tools
                # into requests for vision-only and embedding-only models.
                "builtin_tools": "tools" in capabilities,
                "vision": "vision" in capabilities,
                "reasoning": "thinking" in capabilities,
            }
        }
    }


async def _read_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    return body


def _enforce_limit(key: ApiKey) -> None:
    limits = [(("key", key.id), key.rpm_limit, "application")]
    if key.member_id is not None and key.member_rpm_limit is not None:
        limits.append(
            (("member", key.member_id), key.member_rpm_limit, "member")
        )
    if key.org_id is not None and key.org_rpm_limit is not None:
        limits.append(
            (("organization", key.org_id), key.org_rpm_limit, "organization")
        )

    allowed, scope, limit, retry_after = ratelimit.check_many(limits)
    if not allowed:
        raise HTTPException(
            429,
            f"{scope.title()} rate limit of {limit} requests/minute exceeded.",
            headers={"Retry-After": str(retry_after)},
        )


def _resolve_and_authorize(body: dict[str, Any], key: ApiKey) -> str:
    requested = body.get("model")
    if not requested:
        raise HTTPException(400, "Field 'model' is required.")
    model = resolve_model(requested)
    if not key.may_use(model):
        raise HTTPException(403, f"Key '{key.name}' is not permitted to use model '{model}'.")
    body["model"] = model
    return model


def _privacy_context(
    request: Request, body: dict[str, Any]
) -> tuple[dict[str, Any], PrivacyEngine | None, str]:
    mode = request.headers.get("X-Grandice-Privacy-Mode", "off").strip().lower()
    if mode not in _PRIVACY_MODES:
        raise HTTPException(
            400,
            "X-Grandice-Privacy-Mode must be off, client, sidecar, or hosted.",
        )
    if mode == "off":
        return body, None, mode

    policy = request.headers.get("X-Grandice-Privacy-Policy", "general-v1")
    try:
        engine = PrivacyEngine(policy, processing_location="hosted")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        if mode in {"client", "sidecar"}:
            residual = engine.inspect(body)
            if residual.findings:
                counts = residual.receipt.entities_detected
                raise HTTPException(
                    422,
                    "The locally protected request still contains detectable "
                    f"sensitive data: {counts}. It was not sent to the model.",
                )
            return body, None, mode
        protected = engine.mask(body).data
    except MediaPrivacyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return protected, engine, mode


def _protect_json_response(
    response: JSONResponse, engine: PrivacyEngine | None, mode: str
) -> JSONResponse:
    if engine is None:
        response.headers["X-Grandice-Privacy-Mode"] = mode
        response.headers["X-Grandice-Privacy-Residual-Check"] = (
            "passed" if mode in {"client", "sidecar"} else "not-run"
        )
        return response

    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError):
        payload = {"error": {"message": "Upstream returned invalid JSON."}}
    try:
        result = engine.mask(payload)
    except MediaPrivacyError as exc:
        raise HTTPException(
            502, f"Upstream response was blocked by hosted privacy policy: {exc}"
        ) from exc
    protected = JSONResponse(result.data, status_code=response.status_code)
    protected.headers["X-Grandice-Privacy-Mode"] = mode
    protected.headers["X-Grandice-Privacy-Request-Id"] = result.receipt.request_id
    protected.headers["X-Grandice-Content-Retained"] = "false"
    return protected


@router.get("/models")
async def list_models(key: ApiKey = Depends(require_api_key)) -> dict[str, Any]:
    try:
        resp = await upstream.client().get("/v1/models")
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(502, f"Cannot reach Ollama: {exc}") from exc

    entries = resp.json().get("data", [])
    if key.allowed_models is not None:
        allowed_targets = [resolve_model(model) for model in key.allowed_models]
        entries = [
            entry
            for entry in entries
            if entry.get("id")
            and any(_same_model(entry["id"], allowed) for allowed in allowed_targets)
        ]

    installed = {m.get("id") for m in entries if m.get("id")}
    model_info = {model: await _model_info(model) for model in installed}
    for entry in entries:
        entry["info"] = model_info.get(entry.get("id"), {})

    for alias, target in load_aliases().items():
        installed_target = next(
            (model for model in installed if _same_model(model, target)), None
        )
        if installed_target:
            entries.append(
                {
                    "id": alias,
                    "object": "model",
                    "owned_by": "grandice",
                    "aliases_to": installed_target,
                    "info": model_info.get(installed_target, {}),
                }
            )

    return {"object": "list", "data": entries}


@router.post("/chat/completions")
async def chat_completions(request: Request, key: ApiKey = Depends(require_api_key)):
    _enforce_limit(key)
    body = await _read_body(request)
    body, privacy_engine, privacy_mode = _privacy_context(request, body)
    model = _resolve_and_authorize(body, key)

    # Callers that want the model to think can say so; everyone else gets the
    # fast, non-reasoning behaviour they almost certainly expect.
    if "reasoning_effort" not in body and "chat_template_kwargs" not in body:
        effort = default_reasoning_effort(model)
        if effort:
            body["reasoning_effort"] = effort

    stream = bool(body.get("stream"))
    if stream and privacy_engine is not None:
        raise HTTPException(
            400,
            "Hosted privacy does not yet support streaming safely. Use client or "
            "sidecar privacy, or send stream=false.",
        )
    if stream and get_settings().inject_stream_usage and "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}

    response = await upstream.forward(
        path="/v1/chat/completions", body=body, key=key, model=model, stream=stream
    )
    if isinstance(response, JSONResponse):
        return _protect_json_response(response, privacy_engine, privacy_mode)
    response.headers["X-Grandice-Privacy-Mode"] = privacy_mode
    response.headers["X-Grandice-Privacy-Residual-Check"] = (
        "passed" if privacy_mode in {"client", "sidecar"} else "not-run"
    )
    return response


@router.post("/completions")
async def completions(request: Request, key: ApiKey = Depends(require_api_key)):
    _enforce_limit(key)
    body = await _read_body(request)
    body, privacy_engine, privacy_mode = _privacy_context(request, body)
    model = _resolve_and_authorize(body, key)
    stream = bool(body.get("stream"))
    if stream and privacy_engine is not None:
        raise HTTPException(
            400,
            "Hosted privacy does not yet support streaming safely. Use client or "
            "sidecar privacy, or send stream=false.",
        )
    response = await upstream.forward(
        path="/v1/completions", body=body, key=key, model=model, stream=stream
    )
    if isinstance(response, JSONResponse):
        return _protect_json_response(response, privacy_engine, privacy_mode)
    response.headers["X-Grandice-Privacy-Mode"] = privacy_mode
    response.headers["X-Grandice-Privacy-Residual-Check"] = (
        "passed" if privacy_mode in {"client", "sidecar"} else "not-run"
    )
    return response


@router.post("/embeddings")
async def embeddings(request: Request, key: ApiKey = Depends(require_api_key)):
    _enforce_limit(key)
    body = await _read_body(request)
    body, privacy_engine, privacy_mode = _privacy_context(request, body)
    model = _resolve_and_authorize(body, key)
    response = await upstream.forward(
        path="/v1/embeddings", body=body, key=key, model=model, stream=False
    )
    if isinstance(response, JSONResponse):
        return _protect_json_response(response, privacy_engine, privacy_mode)
    return response
