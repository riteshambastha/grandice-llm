from __future__ import annotations

import base64
import binascii
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .media import MediaPrivacyError, embedded_media_type
from .models import PrivacyReceipt
from .multimodal import LocalMultimodalProcessor, MediaProcessingResult

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


@dataclass(frozen=True)
class EncodedMediaResult:
    content_base64: str
    media_type: str
    receipt: PrivacyReceipt
    processing: MediaProcessingResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "content_base64": self.content_base64,
            "media_type": self.media_type,
            "processing": {
                "sensitive_text_regions": self.processing.sensitive_text_regions,
                "faces_redacted": self.processing.faces_redacted,
                "codes_redacted": self.processing.codes_redacted,
                "audio_intervals_muted": self.processing.audio_intervals_muted,
                "frames_processed": self.processing.frames_processed,
            },
            "receipt": self.receipt.as_dict(),
        }


def protect_embedded_data_urls(
    data: Any,
    *,
    policy_id: str,
    processing_location: Literal["client", "sidecar"],
    whisper_model_path: str | Path | None = None,
    max_bytes: int = 50 * 1024 * 1024,
) -> tuple[Any, dict[str, str]]:
    """Sanitize data-URL media and replace it with opaque temporary sentinels.

    Callers run their normal text privacy engine while the sentinels are in
    place, then use ``restore_embedded_data_urls`` immediately before local
    serialization. The map must never be sent separately.
    """
    protected: dict[str, str] = {}

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, str) and embedded_media_type(value, path):
            if not value.lower().startswith("data:"):
                raise MediaPrivacyError(
                    f"Embedded media at {path} has no declared MIME type."
                )
            prefix = value.split(",", 1)[0]
            media_type = prefix[5:].split(";", 1)[0].lower()
            result = protect_base64_media(
                content_base64=value,
                media_type=media_type,
                policy_id=policy_id,
                processing_location=processing_location,
                whisper_model_path=whisper_model_path,
                max_bytes=max_bytes,
            )
            token = f"<GI_PROTECTED_MEDIA_{secrets.token_urlsafe(18)}>"
            protected[token] = (
                f"data:{result.media_type};base64,{result.content_base64}"
            )
            return token
        if isinstance(value, list):
            return [
                walk(item, f"{path}[{index}]") for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            return {
                key: walk(item, f"{path}.{key}") for key, item in value.items()
            }
        return value

    return walk(data, "$"), protected


def restore_embedded_data_urls(data: Any, protected: dict[str, str]) -> Any:
    if isinstance(data, str):
        return protected.get(data, data)
    if isinstance(data, list):
        return [restore_embedded_data_urls(item, protected) for item in data]
    if isinstance(data, dict):
        return {
            key: restore_embedded_data_urls(value, protected)
            for key, value in data.items()
        }
    return data


def protect_base64_media(
    *,
    content_base64: str,
    media_type: str,
    policy_id: str,
    processing_location: Literal["client", "sidecar", "hosted"],
    whisper_model_path: str | Path | None = None,
    max_bytes: int = 50 * 1024 * 1024,
) -> EncodedMediaResult:
    if processing_location == "hosted":
        raise MediaPrivacyError(
            "Hosted multimedia redaction is disabled because the current local "
            "processor uses temporary files. The original media was not decoded or "
            "written. Use the zero-transfer sidecar until a memory-only hosted "
            "pipeline is available."
        )
    try:
        extension = _EXTENSIONS[media_type.lower()]
    except KeyError as exc:
        raise MediaPrivacyError(
            f"Unsupported media type '{media_type}'. Supported types: "
            + ", ".join(sorted(_EXTENSIONS))
        ) from exc

    encoded = content_base64
    if encoded.startswith("data:"):
        try:
            prefix, encoded = encoded.split(",", 1)
        except ValueError as exc:
            raise MediaPrivacyError("Malformed media data URL.") from exc
        expected_prefix = f"data:{media_type.lower()};base64"
        if prefix.lower() != expected_prefix:
            raise MediaPrivacyError(
                "The data URL media type does not match the declared media_type."
            )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MediaPrivacyError("content_base64 is not valid base64.") from exc
    if not raw:
        raise MediaPrivacyError("The media payload is empty.")
    if len(raw) > max_bytes:
        raise MediaPrivacyError(
            f"The decoded media exceeds the {max_bytes}-byte processing limit."
        )

    with tempfile.TemporaryDirectory(prefix="grandice-media-") as directory:
        source = Path(directory) / f"source{extension}"
        destination = Path(directory) / f"protected{extension}"
        source.write_bytes(raw)
        processor = LocalMultimodalProcessor(
            policy_id,
            whisper_model_path=whisper_model_path,
        )
        processing = processor.process(source, destination, media_type)
        protected = destination.read_bytes()

    detected = {
        "OCR_TEXT_REGION": processing.sensitive_text_regions,
        "FACE": processing.faces_redacted,
        "CODE": processing.codes_redacted,
        "AUDIO_INTERVAL": processing.audio_intervals_muted,
    }
    counts = {name: count for name, count in detected.items() if count}
    receipt = PrivacyReceipt(
        request_id=f"prv_media_{secrets.token_urlsafe(12)}",
        processing_location=processing_location,
        policy=policy_id,
        transformation="mask",
        entities_detected=counts,
        entities_transformed=counts.copy(),
        raw_content_transferred=processing_location == "hosted",
        warnings=[
            "Automated multimedia detection can miss sensitive content; review "
            "high-risk outputs before use."
        ],
    )
    return EncodedMediaResult(
        content_base64=base64.b64encode(protected).decode("ascii"),
        media_type=media_type.lower(),
        receipt=receipt,
        processing=processing,
    )

