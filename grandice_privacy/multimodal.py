from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

from .detectors import detect_text
from .media import MediaCapabilities, MediaPrivacyError, media_capabilities
from .models import PrivacyPolicy
from .policies import get_policy


@dataclass(frozen=True)
class TranscribedWord:
    start: float
    end: float
    text: str


class LocalTranscriber(Protocol):
    def transcribe(self, path: Path) -> Sequence[TranscribedWord]: ...


@dataclass(frozen=True)
class MediaProcessingResult:
    output_path: Path
    sensitive_text_regions: int = 0
    faces_redacted: int = 0
    codes_redacted: int = 0
    audio_intervals_muted: int = 0
    frames_processed: int = 0


class FasterWhisperTranscriber:
    """A faster-whisper adapter which only accepts an existing local model."""

    def __init__(self, model_path: str | Path, **model_options: Any) -> None:
        path = Path(model_path).expanduser().resolve()
        if not path.exists():
            raise MediaPrivacyError(
                f"Local Whisper model does not exist: {path}. No download was attempted."
            )
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise MediaPrivacyError("faster-whisper is not installed.") from exc
        device = model_options.setdefault(
            "device", os.getenv("GRANDICE_WHISPER_DEVICE", "cpu")
        )
        model_options.setdefault(
            "compute_type",
            os.getenv(
                "GRANDICE_WHISPER_COMPUTE_TYPE",
                "int8" if device == "cpu" else "float16",
            ),
        )
        self._model = WhisperModel(
            str(path), local_files_only=True, **model_options
        )

    def transcribe(self, path: Path) -> Sequence[TranscribedWord]:
        segments, _ = self._model.transcribe(str(path), word_timestamps=True)
        words: list[TranscribedWord] = []
        for segment in segments:
            for word in segment.words or ():
                if word.start is None or word.end is None:
                    raise MediaPrivacyError(
                        "The local transcription omitted required word timestamps."
                    )
                words.append(
                    TranscribedWord(float(word.start), float(word.end), word.word.strip())
                )
        return words


def sensitive_audio_intervals(
    words: Sequence[TranscribedWord], policy: PrivacyPolicy
) -> list[tuple[float, float]]:
    """Map sensitive spans in a transcript back to conservative word intervals."""
    if not words:
        return []
    transcript_parts: list[str] = []
    spans: list[tuple[int, int, TranscribedWord]] = []
    cursor = 0
    for word in words:
        if word.end < word.start or word.start < 0:
            raise MediaPrivacyError("The local transcript contains invalid timestamps.")
        if transcript_parts:
            transcript_parts.append(" ")
            cursor += 1
        start = cursor
        transcript_parts.append(word.text)
        cursor += len(word.text)
        spans.append((start, cursor, word))

    findings = detect_text("".join(transcript_parts), policy, "$.audio.transcript")
    intervals = [
        (word.start, word.end)
        for finding in findings
        for start, end, word in spans
        if start < finding.end and finding.start < end
    ]
    return _merge_intervals(intervals)


def _merge_intervals(
    intervals: Iterable[tuple[float, float]], padding: float = 0.05
) -> list[tuple[float, float]]:
    expanded = sorted(
        (max(0.0, start - padding), end + padding) for start, end in intervals
    )
    merged: list[tuple[float, float]] = []
    for start, end in expanded:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _mute_filter(intervals: Sequence[tuple[float, float]]) -> str | None:
    if not intervals:
        return None
    expressions = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in intervals)
    return f"volume=enable='{expressions}':volume=0"


class LocalMultimodalProcessor:
    """Fail-closed, filesystem-based processing using local executables only."""

    def __init__(
        self,
        policy_id: str = "strict-v1",
        *,
        custom_literals: dict[str, list[str]] | None = None,
        transcriber: LocalTranscriber | None = None,
        whisper_model_path: str | Path | None = None,
        capabilities: MediaCapabilities | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.policy = get_policy(policy_id, custom_literals=custom_literals)
        self.capabilities = capabilities or media_capabilities(
            whisper_model_path
        )
        if transcriber is not None and whisper_model_path is not None:
            raise ValueError("Provide transcriber or whisper_model_path, not both.")
        self.transcriber = transcriber
        if whisper_model_path is not None:
            self.transcriber = FasterWhisperTranscriber(whisper_model_path)
        self._run_command = command_runner

    def process(
        self,
        source: str | Path,
        destination: str | Path,
        media_type: str,
    ) -> MediaProcessingResult:
        normalized = media_type.split("/", 1)[0].lower()
        processors = {
            "image": self.process_image,
            "audio": self.process_audio,
            "video": self.process_video,
        }
        try:
            processor = processors[normalized]
        except KeyError as exc:
            raise MediaPrivacyError(f"Unsupported media type '{media_type}'.") from exc
        return processor(source, destination)

    def process_image(self, source: str | Path, destination: str | Path) -> MediaProcessingResult:
        self._require_image()
        source_path, destination_path = self._paths(source, destination)
        try:
            from PIL import Image

            with Image.open(source_path) as opened:
                if getattr(opened, "n_frames", 1) != 1:
                    raise MediaPrivacyError(
                        "Animated/multi-frame images are refused; every frame must be processed."
                    )
                image = opened.convert("RGB")
            image, text_count, face_count, code_count = self._redact_image(image)
            with self._staged_path(destination_path) as staged:
                output = Image.new("RGB", image.size)
                output.paste(image)
                output.save(staged, format=self._image_format(destination_path))
                self._commit(staged, destination_path)
        except MediaPrivacyError:
            raise
        except Exception as exc:
            raise MediaPrivacyError(
                "Image processing failed; no output was committed."
            ) from exc
        return MediaProcessingResult(
            destination_path, text_count, face_count, code_count
        )

    def process_audio(self, source: str | Path, destination: str | Path) -> MediaProcessingResult:
        self._require_audio()
        source_path, destination_path = self._paths(source, destination)
        try:
            assert self.transcriber is not None
            words = self.transcriber.transcribe(source_path)
            intervals = sensitive_audio_intervals(words, self.policy)
            command = [
                self.capabilities.ffmpeg_path or "ffmpeg",
                "-nostdin",
                "-y",
                "-i",
                str(source_path),
            ]
            mute_filter = _mute_filter(intervals)
            if mute_filter:
                command.extend(["-af", mute_filter])
            command.extend(["-map_metadata", "-1"])
            with self._staged_path(destination_path) as staged:
                command.append(str(staged))
                self._command(command)
                self._require_nonempty(staged, "audio")
                self._commit(staged, destination_path)
        except MediaPrivacyError:
            raise
        except Exception as exc:
            raise MediaPrivacyError(
                "Audio processing failed; no output was committed."
            ) from exc
        return MediaProcessingResult(
            destination_path, audio_intervals_muted=len(intervals)
        )

    def process_video(self, source: str | Path, destination: str | Path) -> MediaProcessingResult:
        self._require_video()
        source_path, destination_path = self._paths(source, destination)
        try:
            import cv2
            from PIL import Image

            with tempfile.TemporaryDirectory(prefix="grandice-video-") as directory:
                temporary = Path(directory)
                visual_path = temporary / "visual.mp4"
                capture = cv2.VideoCapture(str(source_path))
                if not capture.isOpened():
                    raise MediaPrivacyError("The video stream could not be decoded.")
                fps = float(capture.get(cv2.CAP_PROP_FPS))
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                if fps <= 0 or width <= 0 or height <= 0:
                    capture.release()
                    raise MediaPrivacyError("The video stream has invalid dimensions or timing.")
                writer = cv2.VideoWriter(
                    str(visual_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (width, height),
                )
                if not writer.isOpened():
                    capture.release()
                    raise MediaPrivacyError("A local video encoder is unavailable.")

                frames = text_count = face_count = code_count = 0
                try:
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        redacted, texts, faces, codes = self._redact_image(
                            Image.fromarray(rgb)
                        )
                        writer.write(
                            cv2.cvtColor(
                                __import__("numpy").asarray(redacted), cv2.COLOR_RGB2BGR
                            )
                        )
                        frames += 1
                        text_count += texts
                        face_count += faces
                        code_count += codes
                finally:
                    capture.release()
                    writer.release()
                if frames == 0:
                    raise MediaPrivacyError("The video contained no decodable frames.")
                if expected_frames > 0 and frames < expected_frames:
                    raise MediaPrivacyError(
                        "The video ended before every declared frame was processed."
                    )

                has_audio = self._video_has_audio(source_path)
                with self._staged_path(destination_path) as staged:
                    if has_audio:
                        extracted = temporary / "audio.wav"
                        redacted_audio = temporary / "audio-redacted.wav"
                        self._command(
                            [
                                self.capabilities.ffmpeg_path or "ffmpeg",
                                "-nostdin", "-y", "-i", str(source_path),
                                "-vn", "-map_metadata", "-1", str(extracted),
                            ]
                        )
                        audio_result = self.process_audio(extracted, redacted_audio)
                        self._command(
                            [
                                self.capabilities.ffmpeg_path or "ffmpeg",
                                "-nostdin", "-y", "-i", str(visual_path),
                                "-i", str(redacted_audio), "-map", "0:v:0", "-map", "1:a:0",
                                "-c:v", "copy", "-c:a", "aac", "-map_metadata", "-1",
                                str(staged),
                            ]
                        )
                        muted = audio_result.audio_intervals_muted
                    else:
                        self._command(
                            [
                                self.capabilities.ffmpeg_path or "ffmpeg",
                                "-nostdin", "-y", "-i", str(visual_path),
                                "-map", "0:v:0", "-c:v", "copy", "-map_metadata", "-1",
                                str(staged),
                            ]
                        )
                        muted = 0
                    self._require_nonempty(staged, "video")
                    self._commit(staged, destination_path)
        except MediaPrivacyError:
            raise
        except Exception as exc:
            raise MediaPrivacyError(
                "Video processing failed; no output was committed."
            ) from exc
        return MediaProcessingResult(
            destination_path, text_count, face_count, code_count, muted, frames
        )

    def _redact_image(self, image: Any) -> tuple[Any, int, int, int]:
        import cv2
        import numpy as np
        import pytesseract
        from PIL import ImageDraw
        from pytesseract import Output

        array = np.asarray(image)
        if self.capabilities.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = (
                self.capabilities.tesseract_path
            )
        draw = ImageDraw.Draw(image)
        ocr = pytesseract.image_to_data(image, output_type=Output.DICT)
        words: list[tuple[int, int, str, tuple[int, int, int, int]]] = []
        parts: list[str] = []
        cursor = 0
        for index, raw_text in enumerate(ocr.get("text", [])):
            text = str(raw_text).strip()
            if not text:
                continue
            if parts:
                parts.append(" ")
                cursor += 1
            start = cursor
            parts.append(text)
            cursor += len(text)
            box = (
                int(ocr["left"][index]), int(ocr["top"][index]),
                int(ocr["left"][index]) + int(ocr["width"][index]),
                int(ocr["top"][index]) + int(ocr["height"][index]),
            )
            words.append((start, cursor, text, box))
        findings = detect_text("".join(parts), self.policy, "$.image.ocr")
        text_boxes = {
            box
            for finding in findings
            for start, end, _, box in words
            if start < finding.end and finding.start < end
        }
        for box in text_boxes:
            draw.rectangle(box, fill="black")

        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        cascade_path = self.capabilities.face_model_path or str(
            Path(cv2.data.haarcascades)
            / "haarcascade_frontalface_default.xml"
        )
        classifier = cv2.CascadeClassifier(str(cascade_path))
        if classifier.empty():
            raise MediaPrivacyError("The bundled local face detector could not be loaded.")
        faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
        for x, y, width, height in faces:
            draw.rectangle((int(x), int(y), int(x + width), int(y + height)), fill="black")

        code_polygons = self._detect_codes(array, cv2)
        for polygon in code_polygons:
            draw.polygon(polygon, fill="black")
        return image, len(text_boxes), len(faces), len(code_polygons)

    def _detect_codes(self, array: Any, cv2: Any) -> list[list[tuple[int, int]]]:
        polygons: list[list[tuple[int, int]]] = []
        detector = cv2.QRCodeDetector()
        try:
            detected = detector.detectMulti(array)
            points = detected[1] if detected[0] else None
            if points is not None:
                polygons.extend(self._polygons(points))
        except (AttributeError, cv2.error):
            ok, points = detector.detect(array)
            if ok and points is not None:
                polygons.extend(self._polygons(points))
        if self.capabilities.barcode_detection:
            try:
                detector_type = getattr(cv2, "barcode_BarcodeDetector", None)
                if detector_type is None:
                    detector_type = cv2.barcode.BarcodeDetector
                barcode_detector = detector_type()
                ok, points = barcode_detector.detect(array)
                if ok and points is not None:
                    polygons.extend(self._polygons(points))
            except Exception as exc:
                raise MediaPrivacyError(
                    "Configured local barcode detection failed."
                ) from exc
        return polygons

    @staticmethod
    def _polygons(points: Any) -> list[list[tuple[int, int]]]:
        import numpy as np

        values = np.asarray(points).reshape((-1, 4, 2))
        return [[(int(x), int(y)) for x, y in polygon] for polygon in values]

    def _video_has_audio(self, source: Path) -> bool:
        result = self._command(
            [
                self.capabilities.ffprobe_path or "ffprobe",
                "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "json", str(source),
            ],
            capture_output=True,
        )
        try:
            return bool(json.loads(result.stdout or "{}").get("streams"))
        except (json.JSONDecodeError, AttributeError) as exc:
            raise MediaPrivacyError("FFprobe returned an invalid stream description.") from exc

    def _require_image(self) -> None:
        capabilities = self.capabilities
        if not (
            capabilities.image_metadata
            and capabilities.image_ocr
            and capabilities.face_detection
            and capabilities.qr_detection
            and capabilities.barcode_detection
        ):
            raise MediaPrivacyError(
                "Image processing requires local Pillow, Tesseract OCR, OpenCV face "
                "and QR detection, and OpenCV barcode detection. No output was created."
            )

    def _require_audio(self) -> None:
        if not self.capabilities.ffmpeg or self.transcriber is None:
            raise MediaPrivacyError(
                "Audio processing requires FFmpeg and a configured local transcriber "
                "with word timestamps. No output was created."
            )

    def _require_video(self) -> None:
        self._require_image()
        if not (
            self.capabilities.video_processing
            and self.capabilities.ffmpeg
            and self.capabilities.ffprobe
        ):
            raise MediaPrivacyError(
                "Video processing requires local OpenCV, FFmpeg, and FFprobe. "
                "No output was created."
            )

    def _command(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        try:
            return self._run_command(
                command, check=True, stdin=subprocess.DEVNULL, **kwargs
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MediaPrivacyError(f"Local media command failed: {command[0]}.") from exc

    @staticmethod
    def _paths(source: str | Path, destination: str | Path) -> tuple[Path, Path]:
        source_path = Path(source).expanduser().resolve()
        destination_path = Path(destination).expanduser().resolve()
        if not source_path.is_file():
            raise MediaPrivacyError(f"Media input does not exist: {source_path}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        return source_path, destination_path

    @staticmethod
    def _image_format(path: Path) -> str:
        formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
        try:
            return formats[path.suffix.lower()]
        except KeyError as exc:
            raise MediaPrivacyError(
                "Image output must be JPEG, PNG, or WebP."
            ) from exc

    @staticmethod
    def _require_nonempty(path: Path, media_type: str) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise MediaPrivacyError(f"Local {media_type} encoder produced no output.")

    @staticmethod
    def _commit(staged: Path, destination: Path) -> None:
        os.replace(staged, destination)

    class _StagedPath:
        def __init__(self, destination: Path) -> None:
            self.destination = destination
            self.path: Path | None = None

        def __enter__(self) -> Path:
            descriptor, name = tempfile.mkstemp(
                prefix=".grandice-", suffix=self.destination.suffix,
                dir=self.destination.parent,
            )
            os.close(descriptor)
            os.unlink(name)
            self.path = Path(name)
            return self.path

        def __exit__(self, *_: Any) -> None:
            if self.path is not None:
                self.path.unlink(missing_ok=True)

    @classmethod
    def _staged_path(cls, destination: Path) -> _StagedPath:
        return cls._StagedPath(destination)
