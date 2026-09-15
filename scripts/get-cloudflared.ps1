<#
.SYNOPSIS
    Downloads the cloudflared binary into tools\.
.DESCRIPTION
    Fetching the standalone executable avoids the MSI installer, which needs
    elevation and hangs when run non-interactively with --silent.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root "tools"
New-Item -ItemType Directory -Force -Path $tools | Out-Null

$target = Join-Path $tools "cloudflared.exe"
$url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host "Downloading cloudflared..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing

& $target --version
Write-Host "[ok] Saved to $target" -ForegroundColor Green
