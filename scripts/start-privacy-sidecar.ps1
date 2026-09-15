param(
    [string]$Upstream = "http://127.0.0.1:8080",
    [string]$Policy = "strict-v1",
    [int]$Port = 8090
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Python environment not found at $python. Run bootstrap.ps1 first."
}

$env:GRANDICE_SIDECAR_UPSTREAM = $Upstream
$env:GRANDICE_PRIVACY_POLICY = $Policy
$env:GRANDICE_SIDECAR_PORT = "$Port"

Set-Location $root
& $python -m privacy_sidecar.main

