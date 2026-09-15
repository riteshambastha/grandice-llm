import asyncio
import multiprocessing
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.auth import ApiKey, require_api_key
from gateway.routes import agents, market
from grandice_domain import MarketResearchInput, analyze_market_evidence


def slow_market_analyzer(payload: MarketResearchInput):
    time.sleep(3)
    return analyze_market_evidence(payload)


def source(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "citation": f"Caller-attributed market evidence {source_id}",
        "as_of": "2026-09-15",
    }


def dimension() -> dict:
    return {
        "metric_name": "Revenue",
        "metric_unit": "USD",
        "value_kind": "currency",
        "scale": "millions",
        "currency": "USD",
        "period_type": "fiscal_year",
        "accounting_basis": "gaap",
        "fiscal_calendar_id": "calendar-dec31",
    }


def evidence(
    evidence_id: str,
    subject_id: str,
    value: str,
    observed_date: str,
    source_id: str,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "subject_id": subject_id,
        "subject_type": "entity",
        "evidence_kind": "metric",
        "observed_date": observed_date,
        "title": f"Reported metric {evidence_id}",
        "statement": f"Caller supplied revenue observation {evidence_id}.",
        "metric_value": value,
        "metric_period_start": f"{observed_date[:4]}-01-01",
        "metric_period_end": observed_date,
        "directional_signal": "positive",
        "tags": ["caller-supplied"],
        **dimension(),
    }


def research_payload() -> dict:
    return {
        "metadata": {
            "as_of": "2026-09-15",
            "sources": [source("source-a"), source("source-b")],
        },
        "scope": {
            "scope_id": "market-scope",
            "title": "Declared software market research",
            "start_date": "2024-01-01",
            "end_date": "2026-09-15",
            "subject_ids": ["alpha"],
            "metric_dimensions": [dimension()],
        },
        "evidence": [
            evidence("alpha-2024", "alpha", "100", "2024-12-31", "source-a"),
            evidence("alpha-2025", "alpha", "120", "2025-12-31", "source-b"),
        ],
    }


def competitive_payload() -> dict:
    payload = research_payload()
    payload["scope"]["subject_ids"] = ["alpha", "beta"]
    payload["entities"] = [
        {"entity_id": "alpha", "name": "Alpha"},
        {"entity_id": "beta", "name": "Beta"},
    ]
    payload["evidence"] = [
        evidence("alpha-current", "alpha", "120", "2025-12-31", "source-a"),
        evidence("beta-current", "beta", "95", "2025-12-31", "source-b"),
    ]
    return payload


class MarketApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(market.router)
        app.include_router(agents.router)
        app.dependency_overrides[require_api_key] = lambda: ApiKey(
            id=904,
            name="market-tests",
            rpm_limit=1000,
            allowed_models=None,
        )
        cls.client = TestClient(app)

    @patch("gateway.routes.market.record_domain_run", new_callable=AsyncMock)
    def test_market_analysis_is_source_bound_and_non_advisory(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/market/research/analyze",
            headers={"X-Grandice-Privacy-Mode": "sidecar"},
            json=research_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        analysis = response.json()["data"]
        self.assertFalse(response.json()["privacy"]["mode_attested"])
        self.assertEqual(analysis["evidence_provenance"], "caller_provided")
        self.assertEqual(analysis["verification_status"], "unverified")
        self.assertFalse(analysis["market_research_advice"])
        self.assertFalse(analysis["content_persisted"])
        self.assertTrue(analysis["professional_review_required"])
        self.assertEqual(analysis["trends"][0]["interval_change"], "20")
        self.assertEqual(
            analysis["trends"][0]["bindings"][0]["verification_status"],
            "unverified",
        )
        self.assertEqual(
            response.headers["X-Grandice-Source-Verification"],
            "unverified",
        )
        audit.assert_awaited_once()

    @patch("gateway.routes.market.record_domain_run", new_callable=AsyncMock)
    def test_privacy_scan_precedes_market_schema_validation(
        self, audit: AsyncMock
    ) -> None:
        payload = research_payload()
        payload["unsupported"] = "alice@example.com"

        response = self.client.post(
            "/v1/market/research/analyze",
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("EMAIL_ADDRESS", response.text)
        self.assertNotIn("unsupported", response.text)
        audit.assert_awaited_once()

    @patch("gateway.routes.market.record_domain_run", new_callable=AsyncMock)
    def test_competitive_comparison_never_ranks(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/market/competitive-landscape/compare",
            headers={"X-Grandice-Privacy-Mode": "client"},
            json=competitive_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        comparison = response.json()["data"]
        self.assertFalse(comparison["strategic_recommendations_generated"])
        self.assertFalse(comparison["metric_comparisons"][0]["ranking_performed"])
        self.assertTrue(comparison["metric_comparisons"][0]["comparable"])
        self.assertEqual(
            {row["subject_id"] for row in comparison["entity_profiles"]},
            {"alpha", "beta"},
        )
        audit.assert_awaited_once()

    @patch("gateway.routes.agents.record_domain_run", new_callable=AsyncMock)
    def test_managed_market_agent_dispatches_typed_workflow(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/agents/runs",
            headers={"X-Grandice-Privacy-Mode": "sidecar"},
            json={
                "agent": "market.evidence-analyst.v1",
                "inputs": research_payload(),
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["agent"]["domain"], "market_research")
        self.assertTrue(body["agent"]["deterministic"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(
            response.headers["X-Grandice-Source-Verification"],
            "unverified",
        )
        audit.assert_awaited_once()


class MarketProcessIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_terminates_market_worker_process(self) -> None:
        payload = MarketResearchInput.model_validate(research_payload())
        started = time.monotonic()

        with self.assertRaises(TimeoutError):
            await market._run_in_killable_process(
                slow_market_analyzer,
                payload,
                deadline=time.monotonic() + 0.25,
            )

        self.assertLess(time.monotonic() - started, 1.5)
        await asyncio.sleep(0.05)
        self.assertFalse(
            any(
                child.name == "grandice-market-analysis" and child.is_alive()
                for child in multiprocessing.active_children()
            )
        )


if __name__ == "__main__":
    unittest.main()

