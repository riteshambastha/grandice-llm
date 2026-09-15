<#
.SYNOPSIS
    Prepares the gateway for first run: creates .env with a fresh admin token,
    initialises the SQLite database, and issues a starter API key.
#>
[CmdletBinding()]
param(
    [string]$FirstAppName = "default-app"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function New-Secret {
    # RNGCryptoServiceProvider keeps this working on Windows PowerShell 5.1,
    # whose .NET Framework runtime lacks RandomNumberGenerator.Fill.
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($buffer) -replace '\+', '-' -replace '/', '_' -replace '=', '')
}

$envPath = Join-Path $root ".env"
if (Test-Path $envPath) {
    $envContent = Get-Content $envPath
    if (-not ($envContent | Where-Object { $_ -match '^BACKUP_ENCRYPTION_KEY=.+$' })) {
        $backupKey = New-Secret
        $foundEmptyKey = $false
        $envContent = $envContent | ForEach-Object {
            if ($_ -match '^BACKUP_ENCRYPTION_KEY=$') {
                $foundEmptyKey = $true
                "BACKUP_ENCRYPTION_KEY=$backupKey"
            }
            else { $_ }
        }
        if (-not $foundEmptyKey) {
            $envContent += "BACKUP_ENCRYPTION_KEY=$backupKey"
        }
        $envContent | Set-Content -Path $envPath -Encoding UTF8
        Write-Host "[ok] Added a database backup encryption key to .env." -ForegroundColor Green
    }
    else {
        Write-Host "[skip] .env already has an encryption key." -ForegroundColor Yellow
    }
}
else {
    $adminToken = New-Secret
    $backupKey = New-Secret
    (Get-Content (Join-Path $root ".env.example")) `
        -replace '^ADMIN_TOKEN=$', "ADMIN_TOKEN=$adminToken" `
        -replace '^BACKUP_ENCRYPTION_KEY=$', "BACKUP_ENCRYPTION_KEY=$backupKey" |
        Set-Content -Path $envPath -Encoding UTF8
    Write-Host "[ok] Created .env with new admin and backup encryption keys." -ForegroundColor Green
}

New-Item -ItemType Directory -Force -Path (Join-Path $root "data"), (Join-Path $root "logs"), (Join-Path $root "backups") | Out-Null

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment missing. Run: py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

& $python -c "from gateway import db; db.init_db(); print('[ok] Database schema ready.')"

$existing = & $python -c @"
from gateway import db
db.init_db()
import sqlite3, pathlib
from gateway.config import get_settings
conn = sqlite3.connect(get_settings().database_path)
print(conn.execute('SELECT COUNT(*) FROM api_keys').fetchone()[0])
"@

if ([int]$existing -eq 0) {
    $key = & $python -c @"
import asyncio
from gateway import auth, db
db.init_db()
raw, _ = asyncio.run(auth.create_key('$FirstAppName'))
print(raw)
"@
    Write-Host ""
    Write-Host "[ok] Starter API key for '$FirstAppName':" -ForegroundColor Green
    Write-Host "     $key" -ForegroundColor Cyan
    Write-Host "     Store it now - only a hash is kept." -ForegroundColor Yellow
}
else {
    Write-Host "[skip] $existing API key(s) already exist. Use scripts\new-key.ps1 to add more." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Next: .\scripts\start-gateway.ps1" -ForegroundColor Green
