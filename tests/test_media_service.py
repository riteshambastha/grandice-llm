import base64
import unittest
from unittest.mock import patch

from grandice_privacy.media import MediaPrivacyError
from grandice_privacy.media_service import (
    protect_base64_media,
    protect_embedded_data_urls,
    restore_embedded_data_urls,
)
from grandice_privacy.multimodal import MediaProcessingResult


class FakeProcessor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def process(self, source, destination, media_type):
        destination.write_bytes(b"protected-media")
        return MediaProcessingResult(
            output_path=destination,
            sensitive_text_regions=2,
            faces_redacted=1,
        )


class MediaServiceTests(unittest.TestCase):
    @patch("grandice_privacy.media_service.LocalMultimodalProcessor", FakeProcessor)
    def test_sidecar_media_receipt_claims_zero_transfer(self) -> None:
        result = protect_base64_media(
            content_base64=base64.b64encode(b"original-media").decode(),
            media_type="image/png",
            policy_id="strict-v1",
            processing_location="sidecar",
        )

        self.assertEqual(base64.b64decode(result.content_base64), b"protected-media")
        self.assertFalse(result.receipt.raw_content_transferred)
        self.assertEqual(result.receipt.entities_transformed["OCR_TEXT_REGION"], 2)
        self.assertEqual(result.receipt.entities_transformed["FACE"], 1)

    @patch("grandice_privacy.media_service.LocalMultimodalProcessor", FakeProcessor)
    def test_embedded_media_is_sanitized_before_text_processing(self) -> None:
        original = base64.b64encode(b"original-media").decode()
        without_media, protected = protect_embedded_data_urls(
            {"image_url": {"url": f"data:image/png;base64,{original}"}},
            policy_id="strict-v1",
            processing_location="sidecar",
        )

        self.assertIn("<GI_PROTECTED_MEDIA_", str(without_media))
        self.assertNotIn(original, str(without_media))
        restored = restore_embedded_data_urls(without_media, protected)
        restored_url = restored["image_url"]["url"]
        self.assertNotIn(original, restored_url)
        self.assertEqual(
            base64.b64decode(restored_url.split(",", 1)[1]),
            b"protected-media",
        )

    def test_hosted_media_refuses_disk_backed_processing(self) -> None:
        with self.assertRaisesRegex(MediaPrivacyError, "temporary files"):
            protect_base64_media(
                content_base64=base64.b64encode(b"original-media").decode(),
                media_type="audio/wav",
                policy_id="strict-v1",
                processing_location="hosted",
            )

    def test_invalid_or_oversized_content_is_refused(self) -> None:
        with self.assertRaises(MediaPrivacyError):
            protect_base64_media(
                content_base64="not base64!",
                media_type="image/png",
                policy_id="strict-v1",
                processing_location="client",
            )
        with self.assertRaises(MediaPrivacyError):
            protect_base64_media(
                content_base64=base64.b64encode(b"too large").decode(),
                media_type="image/png",
                policy_id="strict-v1",
                processing_location="client",
                max_bytes=2,
            )


if __name__ == "__main__":
    unittest.main()

