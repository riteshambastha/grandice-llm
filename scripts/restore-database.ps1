<#
.SYNOPSIS
    Decrypts and integrity-checks a Grandice database backup.

.DESCRIPTION
    By default, restores to data\gateway-restored.db for inspection. Use
    -ReplaceLive only while the gateway is stopped. Before replacing the live
    database, the script creates a fresh encrypted safety backup.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,

    [string]$TargetPath,

    [switch]$ReplaceLive
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$liveDatabase = Join-Path $root "data\gateway.db"

if (-not (Test-Path $python)) {
    throw "Virtual environment missing. Run scripts\bootstrap.ps1 first."
}

$resolvedBackup = (Resolve-Path $BackupPath).Path
if ($ReplaceLive) {
    $listener = netstat -ano | Select-String "127\.0\.0\.1:8080\s+.*LISTENING"
    if ($listener) {
        throw "The gateway is running. Stop it before using -ReplaceLive."
    }
    $TargetPath = $liveDatabase
}
elseif (-not $TargetPath) {
    $TargetPath = Join-Path $root "data\gateway-restored.db"
}

$targetFullPath = [System.IO.Path]::GetFullPath($TargetPath)
if ((Test-Path $targetFullPath) -and -not $ReplaceLive) {
    throw "Target already exists: $targetFullPath"
}

Push-Location $root
try {
    & $python -m gateway.backup verify $resolvedBackup
    if ($LASTEXITCODE -ne 0) {
        throw "Backup verification failed."
    }

    if ($ReplaceLive -and (Test-Path $liveDatabase)) {
        & $python -m gateway.backup create
        if ($LASTEXITCODE -ne 0) {
            throw "Safety backup failed; live database was not changed."
        }
    }

    $arguments = @("-m", "gateway.backup", "restore", $resolvedBackup, $targetFullPath)
    if ($ReplaceLive) {
        $arguments += "--overwrite"
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Database restore failed."
    }
}
finally {
    Pop-Location
}
