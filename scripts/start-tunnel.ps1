<#
.SYNOPSIS
    Exposes the gateway through a permanent or quick Cloudflare tunnel.
.DESCRIPTION
    Uses .cloudflared\config.yml when present for the permanent
    llm.grand-ice.com hostname. Otherwise, it falls back to a quick tunnel whose
    random hostname changes on every restart.

    Only the gateway port is published, so every request still needs a valid
    API key. Ollama itself remains bound to localhost and is never exposed.
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$candidates = @(
    (Join-Path $root "tools\cloudflared.exe"),
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
)
$cloudflared = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cloudflared) {
    $onPath = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($onPath) { $cloudflared = $onPath.Source }
    else { throw "cloudflared not found. Run scripts\get-cloudflared.ps1 to download it." }
}

$logFile = Join-Path $logDir "cloudflared.log"
$urlFile = Join-Path $logDir "tunnel-url.txt"
$namedConfig = Join-Path $root ".cloudflared\config.yml"
Remove-Item $logFile, $urlFile -ErrorAction SilentlyContinue

if (Test-Path $namedConfig) {
    $url = "https://llm.grand-ice.com"
    Start-Process -FilePath $cloudflared `
        -ArgumentList "tunnel", "--config", "`"$namedConfig`"", "--no-autoupdate", "run" `
        -RedirectStandardError $logFile -RedirectStandardOutput "$logFile.out" `
        -WindowStyle Hidden

    Set-Content -Path $urlFile -Value $url -Encoding ASCII
    Write-Host ""
    Write-Host "Permanent public endpoint: $url" -ForegroundColor Green
    Write-Host "  Base URL for OpenAI clients: $url/v1" -ForegroundColor Cyan
    Write-Host "  Saved to: $urlFile" -ForegroundColor DarkGray
    exit 0
}

Start-Process -FilePath $cloudflared `
    -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port" `
    -RedirectStandardError $logFile -RedirectStandardOutput "$logFile.out" `
    -WindowStyle Hidden

Write-Host "Waiting for Cloudflare to assign a hostname..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$url = $null
while ((Get-Date) -lt $deadline -and -not $url) {
    Start-Sleep -Seconds 2
    if (Test-Path $logFile) {
        $match = Select-String -Path $logFile -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue
        if ($match) { $url = $match.Matches[0].Value }
    }
}

if (-not $url) { throw "Tunnel did not report a URL within $TimeoutSeconds seconds. See $logFile" }

Set-Content -Path $urlFile -Value $url -Encoding ASCII
Write-Host ""
Write-Host "Public endpoint: $url" -ForegroundColor Green
Write-Host "  Base URL for OpenAI clients: $url/v1" -ForegroundColor Cyan
Write-Host "  Saved to: $urlFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "This hostname changes each restart. For a fixed address, register a" -ForegroundColor Yellow
Write-Host "domain in Cloudflare and switch to a named tunnel." -ForegroundColor Yellow
