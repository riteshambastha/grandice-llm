import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.auth import ApiKey, require_api_key
from gateway.routes import agents, domain, financial


def portfolio_payload() -> dict:
    return {
        "metadata": {
            "as_of": "2026-09-14",
            "sources": [
                {
                    "source_id": "custodian",
                    "citation": "Caller-supplied custodian record",
                    "as_of": "2026-09-14",
                }
            ],
        },
        "reporting_currency": "USD",
        "positions": [
            {
                "instrument_id": "FUND-A",
                "market_value": "75.00",
                "asset_class": "Equity",
                "sector": "Diversified",
                "country": "US",
                "source_id": "custodian",
            },
            {
                "instrument_id": "FUND-B",
                "market_value": "25.00",
                "asset_class": "Fixed Income",
                "sector": "Government",
                "country": "US",
                "source_id": "custodian",
            },
        ],
    }


class DomainApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(financial.router)
        app.include_router(agents.router)
        app.include_router(domain.router)
        app.dependency_overrides[require_api_key] = lambda: ApiKey(
            id=701,
            name="domain-tests",
            rpm_limit=1000,
            allowed_models=None,
        )
        cls.client = TestClient(app)

    @patch(
        "gateway.routes.financial.record_domain_run",
        new_callable=AsyncMock,
    )
    def test_portfolio_api_returns_auditable_deterministic_result(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/financial/portfolio/analyze",
            headers={"X-Grandice-Privacy-Mode": "client"},
            json=portfolio_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["privacy"]["residual_check"]["status"], "passed"
        )
        self.assertEqual(
            body["privacy"]["residual_check"]["guarantee"], "best_effort"
        )
        self.assertFalse(body["privacy"]["content_retained"])
        self.assertEqual(body["data"]["metrics"]["hhi"], "0.625000")
        self.assertTrue(body["data"]["professional_review_required"])
        self.assertEqual(
            response.headers["X-Grandice-Content-Retained"], "false"
        )
        audit.assert_awaited_once()

    @patch(
        "gateway.routes.financial.record_domain_run",
        new_callable=AsyncMock,
    )
    def test_financial_api_rejects_residual_personal_data(
        self, audit: AsyncMock
    ) -> None:
        payload = portfolio_payload()
        payload["metadata"]["sources"][0]["citation"] = "alice@example.com"

        response = self.client.post(
            "/v1/financial/portfolio/analyze",
            headers={"X-Grandice-Privacy-Mode": "client"},
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("EMAIL_ADDRESS", response.text)
        audit.assert_awaited_once()

    @patch(
        "gateway.routes.financial.record_domain_run",
        new_callable=AsyncMock,
    )
    def test_privacy_scan_runs_before_schema_validation(
        self, audit: AsyncMock
    ) -> None:
        payload = portfolio_payload()
        payload["unsupported_field"] = "alice@example.com"

        response = self.client.post(
            "/v1/financial/portfolio/analyze",
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("EMAIL_ADDRESS", response.text)
        self.assertNotIn("unsupported_field", response.text)
        audit.assert_awaited_once()

    @patch(
        "gateway.routes.financial.record_domain_run",
        new_callable=AsyncMock,
    )
    def test_model_validation_errors_are_sanitized_and_serializable(
        self, audit: AsyncMock
    ) -> None:
        payload = portfolio_payload()
        payload["positions"][0]["source_id"] = "missing"

        response = self.client.post(
            "/v1/financial/portfolio/analyze",
            json=payload,
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("strict schema validation", response.text)
        self.assertNotIn("custodian record", response.text)
        audit.assert_awaited_once()

    @patch(
        "gateway.routes.agents.record_domain_run",
        new_callable=AsyncMock,
    )
    def test_managed_agent_dispatches_strict_typed_workflow(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/agents/runs",
            headers={"X-Grandice-Privacy-Mode": "sidecar"},
            json={
                "agent": "financial.portfolio-analyst.v1",
                "inputs": portfolio_payload(),
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(
            body["agent"]["id"], "financial.portfolio-analyst.v1"
        )
        self.assertTrue(body["agent"]["deterministic"])
        audit.assert_awaited_once()

    def test_agent_registry_exposes_guardrails(self) -> None:
        response = self.client.get("/v1/agents")
        self.assertEqual(response.status_code, 200)
        definitions = response.json()["data"]
        self.assertEqual(len(definitions), 3)
        self.assertTrue(
            all(item["professional_review_required"] for item in definitions)
        )
        self.assertTrue(all(item["source_grounded"] for item in definitions))

    @patch(
        "gateway.routes.domain.db.fetch_one",
        new_callable=AsyncMock,
    )
    def test_audit_receipt_contains_metadata_without_content(
        self, fetch_one: AsyncMock
    ) -> None:
        fetch_one.return_value = {
            "request_id": "dom_test",
            "ts": "2026-09-14T18:00:00+00:00",
            "endpoint": "/v1/financial/portfolio/analyze",
            "agent": None,
            "privacy_mode": "client",
            "status": 200,
            "duration_ms": 3,
            "input_schema": "PortfolioAnalysisInput",
            "output_schema": "PortfolioAnalysis",
            "source_count": 1,
            "warning_count": 2,
            "methodology_version": "grandice-domain-1.0.0",
        }

        response = self.client.get("/v1/domain/runs/dom_test")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["content_retained"])
        self.assertNotIn("inputs", response.json())
        self.assertNotIn("output", response.json())
        self.assertEqual(fetch_one.await_args.args[1], ("dom_test", 701))


if __name__ == "__main__":
    unittest.main()

