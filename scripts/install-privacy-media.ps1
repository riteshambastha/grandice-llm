<#
.SYNOPSIS
    Installs the local-only multimedia runtime for Grandice Privacy Shield.
#>
[CmdletBinding()]
param(
    [string]$WhisperRepository = "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    [string]$WhisperModelPath = "$HOME\.grandice\models\faster-whisper-large-v3-turbo"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found at $python. Run bootstrap.ps1 first."
}

Write-Host "Installing FFmpeg and FFprobe..."
winget install --id Gyan.FFmpeg.Essentials --exact --source winget `
    --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
if ($LASTEXITCODE -ne 0) { throw "FFmpeg installation failed with exit code $LASTEXITCODE." }

Write-Host "Installing Tesseract..."
winget install --id tesseract-ocr.tesseract --exact --source winget `
    --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
if ($LASTEXITCODE -ne 0) { throw "Tesseract installation failed with exit code $LASTEXITCODE." }

Write-Host "Installing Python multimedia packages..."
& $python -m pip install --upgrade -r (Join-Path $root "requirements-multimodal.txt")
if ($LASTEXITCODE -ne 0) { throw "Python multimedia dependency installation failed." }

$opencvModelDir = Join-Path $HOME ".grandice\models\opencv"
New-Item -ItemType Directory -Force -Path $opencvModelDir | Out-Null
$faceModel = Join-Path $opencvModelDir "haarcascade_frontalface_default.xml"
Write-Host "Installing the local OpenCV face detector..."
Invoke-WebRequest -UseBasicParsing `
    -Uri "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_frontalface_default.xml" `
    -OutFile $faceModel

Write-Host "Downloading the local Whisper model..."
& $python -c @"
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id=r"$WhisperRepository",
    local_dir=r"$WhisperModelPath",
)
"@
if ($LASTEXITCODE -ne 0) { throw "Whisper model download failed." }

$env:GRANDICE_FACE_MODEL_PATH = $faceModel
$env:PRIVACY_WHISPER_MODEL_PATH = $WhisperModelPath

Write-Host "Verifying the installed privacy runtime..."
Set-Location $root
& $python -c @"
import json
from grandice_privacy.media import media_capabilities

capabilities = media_capabilities(r"$WhisperModelPath")
print(json.dumps(capabilities.as_dict(), indent=2))
required = (
    capabilities.image_metadata,
    capabilities.image_ocr,
    capabilities.face_detection,
    capabilities.qr_detection,
    capabilities.barcode_detection,
    capabilities.audio_transcription,
    capabilities.video_processing,
)
raise SystemExit(0 if all(required) else 1)
"@
if ($LASTEXITCODE -ne 0) {
    throw "The multimedia runtime installed, but one or more strict capabilities are unavailable."
}

Write-Host "Grandice Privacy Shield multimedia runtime is ready."

