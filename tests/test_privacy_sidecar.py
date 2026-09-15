import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import privacy_sidecar.main as sidecar


class PrivacySidecarTests(unittest.TestCase):
    def test_media_route_processes_locally_without_upstream(self) -> None:
        class Result:
            @staticmethod
            def as_dict():
                return {
                    "content_base64": "cHJvdGVjdGVk",
                    "receipt": {"raw_content_transferred": False},
                }

        with (
            patch(
                "privacy_sidecar.main.protect_base64_media",
                return_value=Result(),
            ) as protect,
            TestClient(sidecar.app) as client,
        ):
            response = client.post(
                "/v1/privacy/media/redact",
                json={
                    "content_base64": "b3JpZ2luYWw=",
                    "media_type": "image/png",
                    "policy": "strict-v1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["receipt"]["raw_content_transferred"])
        protect.assert_called_once()

    def test_original_value_never_reaches_upstream(self) -> None:
        captured: dict[str, str] = {}

        async def upstream(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            protected = json.loads(captured["body"])
            content = protected["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": f"Received {content}; contact bob@example.com"
                            }
                        }
                    ]
                },
            )

        with TestClient(sidecar.app) as client:
            original_client = sidecar._client
            sidecar._client = httpx.AsyncClient(
                transport=httpx.MockTransport(upstream)
            )
            try:
                response = client.post(
                    "/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer synthetic-test-key",
                        "X-Grandice-Privacy-Policy": "strict-v1",
                    },
                    json={
                        "model": "chat",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Reply to alice@example.com",
                            }
                        ],
                    },
                )
            finally:
                replacement_client = sidecar._client
                sidecar._client = original_client
                if replacement_client is not None:
                    import asyncio

                    asyncio.run(replacement_client.aclose())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("alice@example.com", captured["body"])
        output = response.json()["choices"][0]["message"]["content"]
        self.assertIn("alice@example.com", output)
        self.assertNotIn("bob@example.com", output)

    def test_streaming_is_refused_instead_of_partially_protected(self) -> None:
        with TestClient(sidecar.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"content-type": "application/json"},
                json={"model": "chat", "stream": True, "messages": []},
            )
        self.assertEqual(response.status_code, 400)

    def test_query_parameters_are_never_forwarded(self) -> None:
        with TestClient(sidecar.app) as client:
            response = client.get(
                "/v1/models",
                params={"email": "alice@example.com"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Query parameters", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()

