import base64
import unittest

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from gateway.auth import ApiKey, require_api_key
from gateway.routes.openai import _privacy_context
from gateway.routes.privacy import router


class PrivacyApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_api_key] = lambda: ApiKey(
            id=1,
            name="privacy-tests",
            rpm_limit=100,
            allowed_models=None,
        )
        cls.client = TestClient(app)

    def test_hosted_redaction_is_explicit_about_transfer(self) -> None:
        response = self.client.post(
            "/v1/privacy/redact",
            json={
                "policy": "financial-strict-v1",
                "data": {"prompt": "Email alice@example.com about account 87432291."},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("alice@example.com", str(payload["data"]))
        self.assertTrue(payload["receipt"]["raw_content_transferred"])
        self.assertFalse(payload["receipt"]["content_retained"])
        self.assertNotIn("alice@example.com", str(payload["findings"]))

    def test_unknown_policy_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/privacy/detect",
            json={"policy": "unknown", "data": "alice@example.com"},
        )
        self.assertEqual(response.status_code, 400)

    def test_media_endpoint_fails_closed_without_local_runtime(self) -> None:
        response = self.client.post(
            "/v1/privacy/media/redact",
            json={
                "policy": "strict-v1",
                "media_type": "image/png",
                "content_base64": base64.b64encode(b"synthetic-image").decode(),
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("temporary files", response.json()["detail"])

    def test_spoofed_local_mode_with_residual_pii_is_rejected(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [
                    (b"x-grandice-privacy-mode", b"client"),
                    (b"x-grandice-privacy-policy", b"strict-v1"),
                ],
            }
        )
        with self.assertRaises(HTTPException) as caught:
            _privacy_context(
                request,
                {"messages": [{"role": "user", "content": "alice@example.com"}]},
            )
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()

