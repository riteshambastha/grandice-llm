from __future__ import annotations

import importlib.util
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MediaPrivacyError(RuntimeError):
    pass


_DATA_URL = re.compile(
    r"^data:(?P<kind>image|audio|video)/[^;,]+(?:;[^,]+)*;base64,",
    re.IGNORECASE,
)
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def embedded_media_type(value: str, path: str = "") -> str | None:
    match = _DATA_URL.match(value)
    if match:
        return match.group("kind").lower()

    # OpenAI-compatible payloads often put raw base64 in fields named image,
    # audio, data, or url. Require both a media-related path and a substantial
    # base64-like value to avoid classifying ordinary identifiers as media.
    media_path = any(
        marker in path.lower()
        for marker in ("image", "audio", "video", "attachment")
    )
    if media_path and re.match(r"^https?://", value, re.IGNORECASE):
        return "remote"
    compact = "".join(value.split())
    if media_path and len(compact) >= 256 and _BASE64.fullmatch(compact):
        return "embedded"
    return None


@dataclass(frozen=True)
class MediaCapabilities:
    image_metadata: bool
    image_ocr: bool
    face_detection: bool
    audio_transcription: bool
    video_processing: bool
    qr_detection: bool = False
    barcode_detection: bool = False
    ffmpeg: bool = False
    ffprobe: bool = False
    tesseract_path: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    face_model_path: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_metadata": self.image_metadata,
            "image_ocr": self.image_ocr,
            "face_detection": self.face_detection,
            "audio_transcription": self.audio_transcription,
            "video_processing": self.video_processing,
            "qr_detection": self.qr_detection,
            "barcode_detection": self.barcode_detection,
            "ffmpeg": self.ffmpeg,
            "ffprobe": self.ffprobe,
            "tesseract_path": self.tesseract_path,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "face_model_path": self.face_model_path,
            "warnings": list(self.warnings),
        }


def _local_executable(name: str) -> str | None:
    environment_name = f"GRANDICE_{name.upper()}_PATH"
    configured = os.getenv(environment_name)
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())

    discovered = shutil.which(name)
    if discovered:
        return discovered

    candidates: list[Path] = []
    if name == "tesseract":
        candidates.extend(
            [
                Path(os.getenv("ProgramFiles", r"C:\Program Files"))
                / "Tesseract-OCR"
                / "tesseract.exe",
                Path(os.getenv("LOCALAPPDATA", ""))
                / "Programs"
                / "Tesseract-OCR"
                / "tesseract.exe",
            ]
        )
    elif name in {"ffmpeg", "ffprobe"}:
        package_root = (
            Path(os.getenv("LOCALAPPDATA", ""))
            / "Microsoft"
            / "WinGet"
            / "Packages"
        )
        candidates.extend(
            package_root.glob(
                "Gyan.FFmpeg.Essentials_*/*/bin/" + name + ".exe"
            )
        )
    return next((str(path.resolve()) for path in candidates if path.is_file()), None)


def media_capabilities(
    whisper_model_path: str | Path | None = None,
) -> MediaCapabilities:
    pillow = importlib.util.find_spec("PIL") is not None
    pytesseract = importlib.util.find_spec("pytesseract") is not None
    opencv = importlib.util.find_spec("cv2") is not None
    whisper = importlib.util.find_spec("faster_whisper") is not None
    tesseract_path = _local_executable("tesseract")
    ffmpeg_path = _local_executable("ffmpeg")
    ffprobe_path = _local_executable("ffprobe")
    configured_model = (
        Path(whisper_model_path).expanduser()
        if whisper_model_path
        else None
    )
    whisper_model_ready = bool(configured_model and configured_model.is_dir())
    barcode = False
    face_model_path: str | None = None
    if opencv:
        try:
            import cv2

            bundled_face_model = (
                Path(cv2.data.haarcascades)
                / "haarcascade_frontalface_default.xml"
            )
            configured_face_model = Path(
                os.getenv(
                    "GRANDICE_FACE_MODEL_PATH",
                    str(
                        Path.home()
                        / ".grandice"
                        / "models"
                        / "opencv"
                        / "haarcascade_frontalface_default.xml"
                    ),
                )
            )
            face_model_path = next(
                (
                    str(path.resolve())
                    for path in (bundled_face_model, configured_face_model)
                    if path.is_file()
                ),
                None,
            )
            barcode = hasattr(cv2, "barcode_BarcodeDetector") or (
                hasattr(cv2, "barcode")
                and hasattr(cv2.barcode, "BarcodeDetector")
            )
        except ImportError:
            barcode = False

    warnings: list[str] = []
    if not pillow:
        warnings.append("Pillow is unavailable; image metadata cannot be removed.")
    if not (pytesseract and tesseract_path):
        warnings.append("Local Tesseract OCR is unavailable.")
    if not opencv:
        warnings.append("Local face and QR detection is unavailable.")
    elif not face_model_path:
        warnings.append("The local face detector model is unavailable.")
    if not barcode:
        warnings.append(
            "Local 1D barcode detection is unavailable; strict images are refused."
        )
    if not (whisper and ffmpeg_path and whisper_model_ready):
        warnings.append(
            "Local audio transcription is unavailable or has no configured model."
        )
    if not (ffmpeg_path and ffprobe_path):
        warnings.append("FFmpeg/FFprobe is unavailable; video cannot be processed.")

    return MediaCapabilities(
        image_metadata=pillow,
        image_ocr=pytesseract and bool(tesseract_path),
        face_detection=opencv and bool(face_model_path),
        audio_transcription=whisper and bool(ffmpeg_path) and whisper_model_ready,
        video_processing=bool(ffmpeg_path and ffprobe_path and opencv),
        qr_detection=opencv,
        barcode_detection=barcode,
        ffmpeg=bool(ffmpeg_path),
        ffprobe=bool(ffprobe_path),
        tesseract_path=tesseract_path,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        face_model_path=face_model_path,
        warnings=tuple(warnings),
    )


def require_media_capabilities(media_type: str, *, strict: bool = True) -> None:
    capabilities = media_capabilities()
    normalized = media_type.split("/", 1)[0].lower()

    required: dict[str, tuple[bool, str]] = {
        "image": (
            capabilities.image_metadata
            and capabilities.image_ocr
            and capabilities.face_detection
            and capabilities.qr_detection
            and capabilities.barcode_detection,
            "image metadata removal, OCR, face detection, QR detection, and barcode detection",
        ),
        "audio": (
            capabilities.audio_transcription,
            "audio decoding and local speech transcription",
        ),
        "video": (
            capabilities.video_processing and capabilities.audio_transcription,
            "frame, metadata, and audio-track processing",
        ),
    }
    if normalized not in required:
        raise MediaPrivacyError(f"Unsupported media type '{media_type}'.")

    available, description = required[normalized]
    if strict and not available:
        raise MediaPrivacyError(
            f"Strict privacy requires {description}, but this installation does not "
            "provide complete local coverage. The media was not processed or transferred."
        )

