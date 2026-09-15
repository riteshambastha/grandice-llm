<#
.SYNOPSIS
    Creates and verifies an encrypted Grandice database backup.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Virtual environment missing. Run scripts\bootstrap.ps1 first."
}

Push-Location $root
try {
    & $python -m gateway.backup create
    if ($LASTEXITCODE -ne 0) {
        throw "Database backup failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
