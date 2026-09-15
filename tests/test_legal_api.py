import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.auth import ApiKey, require_api_key
from gateway import domain_runtime
from gateway.routes import agents, legal


def metadata(*source_ids: str) -> dict:
    return {
        "as_of": "2026-09-15",
        "sources": [
            {
                "source_id": source_id,
                "citation": f"Caller contract record {source_id}",
                "as_of": "2026-09-15",
            }
            for source_id in source_ids
        ],
    }


def contract(
    text: str,
    *,
    document_id: str = "agreement-v1",
    source_id: str = "contract-source",
) -> dict:
    return {
        "document_id": document_id,
        "source_id": source_id,
        "text": text,
        "document_type": "master_services_agreement",
        "jurisdiction": "United States",
    }


def review_payload() -> dict:
    return {
        "metadata": metadata("contract-source"),
        "document": contract(
            "1. Services\nSupplier provides support.\n"
            "2. Limitation of Liability\nSupplier's liability shall be unlimited.\n"
            "3. Confidentiality\nEach party protects Confidential Information.\n"
            "4. Data Protection\nProcessor protects personal data.\n"
        ),
        "playbook": {
            "playbook_id": "commercial-v1",
            "version": "1",
            "required_clause_types": [
                "confidentiality",
                "data_protection",
                "governing_law",
            ],
            "liability_cap_required": True,
        },
    }


class LegalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(legal.router)
        app.include_router(agents.router)
        app.dependency_overrides[require_api_key] = lambda: ApiKey(
            id=902,
            name="legal-tests",
            rpm_limit=1000,
            allowed_models=None,
        )
        cls.client = TestClient(app)

    @patch("gateway.routes.legal.record_domain_run", new_callable=AsyncMock)
    def test_review_returns_bound_source_spans_and_guardrails(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/legal/contracts/review",
            headers={"X-Grandice-Privacy-Mode": "sidecar"},
            json=review_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        analysis = body["data"]
        self.assertFalse(analysis["legal_advice"])
        self.assertFalse(analysis["content_persisted"])
        self.assertTrue(analysis["professional_review_required"])
        self.assertEqual(len(analysis["document_sha256"]), 64)
        self.assertTrue(
            all(
                clause["document_sha256"] == analysis["document_sha256"]
                and len(clause["span_sha256"]) == 64
                and clause["coordinate_unit"] == "unicode_codepoints_v1"
                for clause in analysis["clauses"]
            )
        )
        rules = {finding["rule_id"] for finding in analysis["findings"]}
        self.assertIn("unlimited-liability", rules)
        self.assertIn("missing-governing_law", rules)
        self.assertEqual(
            response.headers["X-Grandice-Legal-Advice"], "false"
        )
        audit.assert_awaited_once()

    @patch("gateway.routes.legal.record_domain_run", new_callable=AsyncMock)
    def test_privacy_scan_precedes_legal_schema_validation(
        self, audit: AsyncMock
    ) -> None:
        payload = review_payload()
        payload["unsupported"] = "alice@example.com"

        response = self.client.post(
            "/v1/legal/contracts/review",
            json=payload,
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("EMAIL_ADDRESS", response.text)
        self.assertNotIn("unsupported", response.text)
        audit.assert_awaited_once()

    @patch("gateway.routes.legal.record_domain_run", new_callable=AsyncMock)
    def test_compare_is_directional_and_reports_risk_delta(
        self, audit: AsyncMock
    ) -> None:
        original = (
            "1. Services\nSupplier provides hosting.\n"
            "2. Confidentiality\nEach party protects Confidential Information.\n"
            "3. Data Protection\nProcessor protects personal data.\n"
        )
        revised = (
            "1. Services\nSupplier provides hosting and support.\n"
            "2. Liability\nSupplier's liability shall be unlimited.\n"
            "3. Confidentiality\nEach party protects Confidential Information.\n"
            "4. Data Protection\nProcessor protects personal data.\n"
        )
        response = self.client.post(
            "/v1/legal/contracts/compare",
            headers={"X-Grandice-Privacy-Mode": "client"},
            json={
                "metadata": metadata("original-source", "revised-source"),
                "original": contract(
                    original,
                    document_id="original-v1",
                    source_id="original-source",
                ),
                "revised": contract(
                    revised,
                    document_id="revised-v2",
                    source_id="revised-source",
                ),
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        comparison = response.json()["data"]
        self.assertEqual(comparison["direction"], "original_to_revised")
        self.assertTrue(
            any(
                "unlimited-liability" in risk_id
                for risk_id in comparison["risk_delta"]["new_risk_ids"]
            )
        )
        self.assertTrue(
            all(
                change["original_document_sha256"]
                in {None, comparison["original_document_sha256"]}
                and change["revised_document_sha256"]
                in {None, comparison["revised_document_sha256"]}
                for change in comparison["changes"]
            )
        )
        audit.assert_awaited_once()

    @patch("gateway.routes.agents.record_domain_run", new_callable=AsyncMock)
    def test_managed_legal_agent_dispatches_typed_workflow(
        self, audit: AsyncMock
    ) -> None:
        response = self.client.post(
            "/v1/agents/runs",
            headers={"X-Grandice-Privacy-Mode": "sidecar"},
            json={
                "agent": "legal.contract-reviewer.v1",
                "inputs": review_payload(),
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["agent"]["domain"], "legal")
        self.assertTrue(body["agent"]["deterministic"])
        self.assertEqual(body["status"], "completed")
        audit.assert_awaited_once()


class DomainAuditRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_opportunistically_purges_expired_metadata(
        self,
    ) -> None:
        domain_runtime._last_domain_audit_purge = 0.0
        execute = AsyncMock()
        with (
            patch.object(domain_runtime.db, "execute", execute),
            patch.object(
                domain_runtime,
                "get_settings",
                return_value=SimpleNamespace(
                    domain_audit_retention_days=90
                ),
            ),
        ):
            await domain_runtime.record_domain_run(
                request_id="dom-retention-test",
                key=ApiKey(
                    id=903,
                    name="retention-tests",
                    rpm_limit=1000,
                    allowed_models=None,
                ),
                endpoint="/v1/legal/contracts/review",
                privacy_mode="sidecar",
                status=200,
                started=0.0,
                input_schema="ContractAnalysisInput",
                output_schema="ContractAnalysis",
                source_count=1,
                warning_count=1,
                methodology_version="test",
            )

        self.assertEqual(execute.await_count, 2)
        self.assertTrue(
            execute.await_args_list[1].args[0].startswith(
                "DELETE FROM domain_runs"
            )
        )


if __name__ == "__main__":
    unittest.main()

