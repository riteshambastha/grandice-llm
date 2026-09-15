<#
.SYNOPSIS
    One-glance health check of every component in the stack.
#>
[CmdletBinding()]
param()

$root = Split-Path -Parent $PSScriptRoot
$ollama = "C:\Users\rites\AppData\Local\Programs\Ollama\ollama.exe"

function Test-Endpoint {
    param([string]$Label, [string]$Uri)
    try {
        $null = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
        Write-Host ("  {0,-14} up    {1}" -f $Label, $Uri) -ForegroundColor Green
    }
    catch {
        Write-Host ("  {0,-14} down  {1}" -f $Label, $Uri) -ForegroundColor Red
    }
}

Write-Host "Services" -ForegroundColor Cyan
Test-Endpoint -Label "Ollama" -Uri "http://127.0.0.1:11434/api/version"
Test-Endpoint -Label "Gateway" -Uri "http://127.0.0.1:8080/health"
Test-Endpoint -Label "Open WebUI" -Uri "http://127.0.0.1:3000"

Write-Host ""
Write-Host "Models" -ForegroundColor Cyan
if (Test-Path $ollama) { & $ollama list | Select-Object -Skip 0 | ForEach-Object { "  $_" } }

Write-Host ""
Write-Host "GPU" -ForegroundColor Cyan
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu `
        --format=csv,noheader | ForEach-Object { "  $_" }
}

Write-Host ""
Write-Host "Loaded in VRAM" -ForegroundColor Cyan
if (Test-Path $ollama) {
    $ps = & $ollama ps
    if ($ps) { $ps | ForEach-Object { "  $_" } } else { "  (none)" }
}

$urlFile = Join-Path $root "logs\tunnel-url.txt"
Write-Host ""
Write-Host "Public tunnel" -ForegroundColor Cyan
if (Test-Path $urlFile) { "  " + (Get-Content $urlFile -Raw).Trim() }
else { "  not running - start with scripts\start-tunnel.ps1" }
