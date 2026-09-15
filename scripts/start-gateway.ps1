<#
.SYNOPSIS
    Runs the API gateway in the foreground. Use install-services.ps1 for an
    unattended setup that restarts with Windows.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment missing. Run scripts\bootstrap.ps1 first." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env missing. Run scripts\bootstrap.ps1 first." }

& $python -m gateway.main
