<#
.SYNOPSIS
    Issues an API key for one application.
.EXAMPLE
    .\scripts\new-key.ps1 -Name billing-bot -RpmLimit 60 -AllowedModels chat,embed
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Name,
    [int]$RpmLimit = 0,
    [string[]]$AllowedModels = @(),
    # Emit only the key on stdout so callers can capture it. Without this the
    # key goes to the host and is invisible to the pipeline.
    [switch]$Raw
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

$rpmArg = if ($RpmLimit -gt 0) { $RpmLimit } else { "None" }
$modelsArg = if ($AllowedModels.Count -gt 0) {
    "[" + (($AllowedModels | ForEach-Object { "'$_'" }) -join ",") + "]"
}
else { "None" }

Push-Location $root
try {
    $key = & $python -c @"
import asyncio
from gateway import auth, db
db.init_db()
raw, key_id = asyncio.run(auth.create_key('$Name', $rpmArg, $modelsArg))
print(raw)
"@
    if ($Raw) {
        Write-Output $key.Trim()
    }
    else {
        Write-Host "API key for '$Name':" -ForegroundColor Green
        Write-Host "  $key" -ForegroundColor Cyan
        Write-Host "  Shown once - store it now." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
