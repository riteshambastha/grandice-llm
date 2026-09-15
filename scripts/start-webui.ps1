<#
.SYNOPSIS
    Runs Open WebUI pointed at the gateway (not at Ollama directly), so that
    browser traffic is authenticated and shows up in the usage log like any
    other application.
#>
[CmdletBinding()]
param(
    [int]$Port = 3000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$webuiHome = "C:\Users\rites\.grandice\open-webui"
# Open WebUI ships a console script; the package has no __main__ module, so
# "python -m open_webui" does not work.
$exe = Join-Path $webuiHome "venv\Scripts\open-webui.exe"

if (-not (Test-Path $exe)) { throw "Open WebUI is not installed at $webuiHome." }

$keyFile = Join-Path $webuiHome "gateway-key.txt"
if (-not (Test-Path $keyFile)) {
    Write-Host "Issuing a dedicated gateway key for Open WebUI..." -ForegroundColor Cyan
    $key = (& (Join-Path $PSScriptRoot "new-key.ps1") -Name "open-webui" -Raw | Select-Object -Last 1)
    if (-not $key -or $key -notmatch '^gll-') { throw "Could not issue an API key for Open WebUI." }
    Set-Content -Path $keyFile -Value $key.Trim() -Encoding ASCII
}
$apiKey = (Get-Content $keyFile -Raw).Trim()

# Keep application state on C: - the repo drive is small.
$env:DATA_DIR = Join-Path $webuiHome "data"
$env:OPENAI_API_BASE_URL = "http://127.0.0.1:8080/v1"
$env:OPENAI_API_KEY = $apiKey
# Blank disables Open WebUI's direct Ollama integration, forcing every call
# through the gateway so usage accounting stays complete.
$env:OLLAMA_BASE_URL = ""
$env:ENABLE_OLLAMA_API = "false"
$env:WEBUI_AUTH = "true"

# Route document embeddings through the gateway as well, instead of letting
# Open WebUI download and run its own sentence-transformers model.
$env:RAG_EMBEDDING_ENGINE = "openai"
$env:RAG_EMBEDDING_MODEL = "embed"
$env:RAG_OPENAI_API_BASE_URL = "http://127.0.0.1:8080/v1"
$env:RAG_OPENAI_API_KEY = $apiKey

New-Item -ItemType Directory -Force -Path $env:DATA_DIR | Out-Null

Write-Host "Open WebUI starting on http://127.0.0.1:$Port" -ForegroundColor Green
& $exe serve --port $Port
