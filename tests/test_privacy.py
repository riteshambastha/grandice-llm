import json
import unittest
from pathlib import Path

from grandice_privacy import PrivacyEngine, TokenVault
from grandice_privacy.media import MediaPrivacyError, require_media_capabilities


class PrivacyEngineTests(unittest.TestCase):
    def test_language_neutral_conformance_cases(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parent.parent
            / "config"
            / "privacy-conformance.json"
        )
        cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["id"]):
                result = PrivacyEngine(case["policy"]).inspect(case["input"])
                self.assertEqual(
                    [finding.entity_type for finding in result.findings],
                    case["entities"],
                )

    def test_detection_exposes_metadata_not_original_value(self) -> None:
        result = PrivacyEngine("strict-v1").inspect(
            "Email alice@example.com and SSN 123-45-6789."
        )

        self.assertEqual(
            [finding.entity_type for finding in result.findings],
            ["EMAIL_ADDRESS", "US_SSN"],
        )
        self.assertNotIn("alice@example.com", str(result.findings[0].public_dict()))
        self.assertEqual(result.data, "Email alice@example.com and SSN 123-45-6789.")

    def test_recursive_tokenization_and_local_restoration(self) -> None:
        original = {
            "messages": [
                {
                    "role": "user",
                    "content": "Contact alice@example.com about account 87432291.",
                }
            ]
        }
        vault = TokenVault()
        engine = PrivacyEngine(
            "financial-strict-v1", processing_location="client", vault=vault
        )

        protected = engine.tokenize(original)

        self.assertNotEqual(protected.data, original)
        self.assertIn("<GI_EMAIL_ADDRESS_001>", protected.data["messages"][0]["content"])
        self.assertEqual(engine.restore(protected.data), original)
        self.assertFalse(protected.receipt.raw_content_transferred)
        self.assertEqual(vault.size, 2)

    def test_masking_is_irreversible_and_preserves_email_domain(self) -> None:
        engine = PrivacyEngine("general-v1")
        result = engine.mask("Write to alice@example.com.")

        self.assertEqual(result.data, "Write to a***@example.com.")
        self.assertEqual(engine.restore(result.data), result.data)

    def test_invalid_credit_card_like_number_is_not_detected(self) -> None:
        result = PrivacyEngine("general-v1").inspect("Reference 1234 5678 9012 3456")
        self.assertNotIn("CREDIT_CARD", result.receipt.entities_detected)

    def test_custom_literals_are_tokenized(self) -> None:
        engine = PrivacyEngine(
            "strict-v1",
            custom_literals={"PERSON": ["Ada Lovelace"]},
        )
        result = engine.tokenize("Prepared for Ada Lovelace.")
        self.assertEqual(result.data, "Prepared for <GI_PERSON_001>.")

    def test_hosted_receipt_is_transparent_about_transfer(self) -> None:
        result = PrivacyEngine(
            "strict-v1", processing_location="hosted"
        ).mask("alice@example.com")
        self.assertTrue(result.receipt.raw_content_transferred)
        self.assertFalse(result.receipt.content_retained)

    def test_unsupported_media_fails_closed(self) -> None:
        with self.assertRaises(MediaPrivacyError):
            require_media_capabilities("application/octet-stream", strict=True)

    def test_embedded_image_is_not_silently_transferred(self) -> None:
        engine = PrivacyEngine("strict-v1")
        with self.assertRaises(MediaPrivacyError):
            engine.tokenize(
                {
                    "messages": [
                        {
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64," + ("A" * 300)
                                    },
                                }
                            ]
                        }
                    ]
                }
            )

    def test_remote_media_reference_is_refused(self) -> None:
        engine = PrivacyEngine("strict-v1")
        with self.assertRaisesRegex(MediaPrivacyError, "remote media"):
            engine.tokenize(
                {
                    "image_url": {
                        "url": "https://files.example/private-passport.png"
                    }
                }
            )

    def test_safe_response_restoration_excludes_tool_arguments(self) -> None:
        engine = PrivacyEngine("strict-v1")
        protected = engine.tokenize(
            {"content": "Email alice@example.com"}
        ).data
        token = protected["content"].split()[-1]
        response = {
            "content": f"Visible {token}",
            "tool_calls": [
                {"function": {"arguments": f'{{"email":"{token}"}}'}}
            ],
        }

        restored = engine.restore_response(response)

        self.assertEqual(restored["content"], "Visible alice@example.com")
        self.assertIn("GI_EMAIL_ADDRESS", restored["tool_calls"][0]["function"]["arguments"])


if __name__ == "__main__":
    unittest.main()

