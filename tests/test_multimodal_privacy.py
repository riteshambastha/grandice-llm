import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from grandice_privacy.engine import PrivacyEngine
from grandice_privacy.media import (
    MediaCapabilities,
    MediaPrivacyError,
    media_capabilities,
)
from grandice_privacy.multimodal import (
    FasterWhisperTranscriber,
    LocalMultimodalProcessor,
    TranscribedWord,
    sensitive_audio_intervals,
)
from grandice_privacy.policies import get_policy


def complete_capabilities() -> MediaCapabilities:
    return MediaCapabilities(
        image_metadata=True,
        image_ocr=True,
        face_detection=True,
        audio_transcription=True,
        video_processing=True,
        qr_detection=True,
        barcode_detection=True,
        ffmpeg=True,
        ffprobe=True,
    )


class FakeTranscriber:
    def transcribe(self, path: Path):
        return [
            TranscribedWord(0.0, 0.3, "contact"),
            TranscribedWord(0.3, 1.1, "alice@example.com"),
            TranscribedWord(1.1, 1.4, "today"),
        ]


class MultimodalPrivacyTests(unittest.TestCase):
    def test_capability_detection_is_local_and_includes_barcode(self) -> None:
        packages = {"PIL", "pytesseract", "cv2", "faster_whisper"}
        executables = {"tesseract", "ffmpeg", "ffprobe"}
        with (
            patch(
                "grandice_privacy.media.importlib.util.find_spec",
                side_effect=lambda name: object() if name in packages else None,
            ),
            patch(
                "grandice_privacy.media.shutil.which",
                side_effect=lambda name: name if name in executables else None,
            ),
        ):
            capabilities = media_capabilities()

        self.assertTrue(capabilities.qr_detection)
        self.assertTrue(capabilities.barcode_detection)
        self.assertTrue(capabilities.video_processing)

    def test_sensitive_transcript_spans_map_to_word_timestamps(self) -> None:
        intervals = sensitive_audio_intervals(
            FakeTranscriber().transcribe(Path("unused")),
            get_policy("strict-v1"),
        )

        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], 0.25)
        self.assertAlmostEqual(intervals[0][1], 1.15)

    def test_audio_processing_mutes_detected_intervals_and_strips_metadata(self) -> None:
        commands: list[list[str]] = []

        def run(command, **kwargs):
            commands.append(command)
            Path(command[-1]).write_bytes(b"locally-redacted")
            return subprocess.CompletedProcess(command, 0, stdout="")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            destination = Path(directory) / "redacted.wav"
            source.write_bytes(b"audio")
            processor = LocalMultimodalProcessor(
                transcriber=FakeTranscriber(),
                capabilities=complete_capabilities(),
                command_runner=run,
            )

            result = processor.process_audio(source, destination)

            self.assertEqual(destination.read_bytes(), b"locally-redacted")
            self.assertEqual(result.audio_intervals_muted, 1)
            self.assertEqual(commands[0][0], "ffmpeg")
            self.assertIn("-map_metadata", commands[0])
            self.assertIn("volume=enable=", commands[0][commands[0].index("-af") + 1])

    def test_audio_fails_closed_without_local_transcriber(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            destination = Path(directory) / "redacted.wav"
            source.write_bytes(b"audio")
            processor = LocalMultimodalProcessor(
                capabilities=complete_capabilities()
            )

            with self.assertRaises(MediaPrivacyError):
                processor.process_audio(source, destination)

            self.assertFalse(destination.exists())

    def test_image_fails_closed_when_qr_detection_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            destination = Path(directory) / "redacted.png"
            source.write_bytes(b"image")
            processor = LocalMultimodalProcessor(
                capabilities=replace(complete_capabilities(), qr_detection=False)
            )

            with self.assertRaisesRegex(MediaPrivacyError, "QR detection"):
                processor.process_image(source, destination)

            self.assertFalse(destination.exists())

    def test_video_fails_closed_when_stream_probe_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            destination = Path(directory) / "redacted.mp4"
            source.write_bytes(b"video")
            processor = LocalMultimodalProcessor(
                capabilities=replace(
                    complete_capabilities(),
                    video_processing=False,
                    ffprobe=False,
                )
            )

            with self.assertRaisesRegex(MediaPrivacyError, "FFprobe"):
                processor.process_video(source, destination)

            self.assertFalse(destination.exists())

    def test_unknown_media_type_is_refused(self) -> None:
        processor = LocalMultimodalProcessor(capabilities=complete_capabilities())
        with self.assertRaisesRegex(MediaPrivacyError, "Unsupported media type"):
            processor.process("source.bin", "output.bin", "application/octet-stream")

    def test_whisper_adapter_refuses_missing_model_without_import_or_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-model"
            with patch.dict("sys.modules", {"faster_whisper": None}):
                with self.assertRaisesRegex(MediaPrivacyError, "No download was attempted"):
                    FasterWhisperTranscriber(missing)

    def test_whisper_adapter_forces_local_files_only(self) -> None:
        options = {}

        class FakeWhisperModel:
            def __init__(self, model_path, **kwargs):
                options.update(kwargs)

        with tempfile.TemporaryDirectory() as directory:
            module = SimpleNamespace(WhisperModel=FakeWhisperModel)
            with patch.dict("sys.modules", {"faster_whisper": module}):
                FasterWhisperTranscriber(directory)

        self.assertIs(options["local_files_only"], True)

    def test_existing_embedded_media_refusal_remains_in_force(self) -> None:
        with self.assertRaises(MediaPrivacyError):
            PrivacyEngine("strict-v1").mask(
                "data:image/png;base64," + ("A" * 300)
            )


if __name__ == "__main__":
    unittest.main()
