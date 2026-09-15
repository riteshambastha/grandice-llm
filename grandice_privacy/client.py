from __future__ import annotations

from typing import Any

import httpx

from .engine import PrivacyEngine, TokenVault
from .models import PrivacyResult


class GrandicePrivacyClient:
    """Local privacy wrapper for Grandice's JSON APIs.

    Original values and the token vault remain in this process. Only tokenized
    JSON is sent to the configured Grandice endpoint.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        policy: str = "strict-v1",
        timeout: float = 600.0,
        custom_literals: dict[str, list[str]] | None = None,
    ) -> None:
        self._policy = policy
        self._custom_literals = custom_literals
        self._vault = TokenVault()
        self.privacy = PrivacyEngine(
            policy,
            custom_literals=custom_literals,
            processing_location="client",
            vault=self._vault,
        )
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Grandice-Privacy-Mode": "client",
                "X-Grandice-Privacy-Policy": policy,
            },
        )

    def protect(self, data: Any) -> PrivacyResult:
        return self.privacy.tokenize(data)

    def restore(self, data: Any) -> Any:
        return self.privacy.restore(data)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        restore_response: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        request_engine = PrivacyEngine(
            self._policy,
            custom_literals=self._custom_literals,
            processing_location="client",
        )
        if json is not None:
            if isinstance(json, dict) and json.get("stream"):
                raise ValueError(
                    "The privacy client does not yet rehydrate streaming responses "
                    "safely. Send stream=false."
                )
            json = request_engine.tokenize(json).data

        response = self._client.request(method, path, json=json, **kwargs)
        if restore_response and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            locally_safe = request_engine.mask(response.json()).data
            restored = request_engine.restore_response(locally_safe)
            response._content = httpx.Response(  # noqa: SLF001
                response.status_code, json=restored
            ).content
            for header in (
                "content-encoding",
                "transfer-encoding",
                "etag",
                "content-md5",
            ):
                response.headers.pop(header, None)
            response.headers["content-length"] = str(len(response.content))
        return response

    def post(self, path: str, *, json: Any, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, json=json, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GrandicePrivacyClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncGrandicePrivacyClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        policy: str = "strict-v1",
        timeout: float = 600.0,
        custom_literals: dict[str, list[str]] | None = None,
    ) -> None:
        self._policy = policy
        self._custom_literals = custom_literals
        self._vault = TokenVault()
        self.privacy = PrivacyEngine(
            policy,
            custom_literals=custom_literals,
            processing_location="client",
            vault=self._vault,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Grandice-Privacy-Mode": "client",
                "X-Grandice-Privacy-Policy": policy,
            },
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        restore_response: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        request_engine = PrivacyEngine(
            self._policy,
            custom_literals=self._custom_literals,
            processing_location="client",
        )
        if json is not None:
            if isinstance(json, dict) and json.get("stream"):
                raise ValueError(
                    "The privacy client does not yet rehydrate streaming responses "
                    "safely. Send stream=false."
                )
            json = request_engine.tokenize(json).data

        response = await self._client.request(method, path, json=json, **kwargs)
        if restore_response and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            locally_safe = request_engine.mask(response.json()).data
            restored = request_engine.restore_response(locally_safe)
            response._content = httpx.Response(  # noqa: SLF001
                response.status_code, json=restored
            ).content
            for header in (
                "content-encoding",
                "transfer-encoding",
                "etag",
                "content-md5",
            ):
                response.headers.pop(header, None)
            response.headers["content-length"] = str(len(response.content))
        return response

    async def post(self, path: str, *, json: Any, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, json=json, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncGrandicePrivacyClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

