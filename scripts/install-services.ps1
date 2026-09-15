<#
.SYNOPSIS
    Installs supervised automatic startup for the complete Grandice stack.
.DESCRIPTION
    An elevated run creates a SYSTEM task triggered at Windows startup, before
    user logon. A non-elevated run creates a hidden Startup-folder launcher that
    begins at user logon. Both modes use one supervisor that checks Ollama, the
    gateway, Open WebUI and Cloudflare Tunnel every 15 seconds and restarts
    components that have stopped.
.EXAMPLE
    .\scripts\install-services.ps1
.EXAMPLE
    .\scripts\install-services.ps1 -IncludeWebUI -IncludeTunnel
#>
[CmdletBinding()]
param(
    [switch]$IncludeTunnel,
    [switch]$IncludeWebUI
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$taskName = "GrandiceLLM-Supervisor"
$supervisor = Join-Path $PSScriptRoot "supervise-stack.ps1"

if (-not (Test-Path $supervisor)) {
    throw "Supervisor script is missing: $supervisor"
}

# With no component switches, install the complete stack. Supplying one of the
# legacy switches preserves its prior selective behavior.
$includeEverything = -not $IncludeTunnel -and -not $IncludeWebUI
$supervisorSwitches = @()
if (-not ($includeEverything -or $IncludeWebUI)) { $supervisorSwitches += "-SkipWebUI" }
if (-not ($includeEverything -or $IncludeTunnel)) { $supervisorSwitches += "-SkipTunnel" }

$supervisorArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$supervisor`""
) + $supervisorSwitches
$argumentString = $supervisorArguments -join " "

# Remove the old independent launchers/tasks so only the supervisor owns
# component recovery.
@(
    "GrandiceLLM-Gateway",
    "GrandiceLLM-WebUI",
    "GrandiceLLM-Tunnel"
) | ForEach-Object {
    Unregister-ScheduledTask -TaskName $_ -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $startup "$_.vbs") -Force -ErrorAction SilentlyContinue
}

if ($isAdmin) {
    Remove-Item (Join-Path $startup "$taskName.vbs") -Force -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument $argumentString -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
        -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 999 -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Starts and supervises the Grandice local LLM stack at Windows boot." | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "[ok] Installed SYSTEM startup task '$taskName'." -ForegroundColor Green
    Write-Host "[ok] It starts at boot before user logon and restarts after failure." -ForegroundColor Green
}
else {
    $vbs = Join-Path $startup "$taskName.vbs"
    $command = '"powershell.exe" ' + $argumentString
    @(
        'Set sh = CreateObject("WScript.Shell")'
        'sh.CurrentDirectory = "' + $root + '"'
        'sh.Run "' + ($command -replace '"', '""') + '", 0, False'
    ) | Set-Content -Path $vbs -Encoding ASCII

    # Start supervision now; the named mutex prevents duplicates.
    Start-Process -FilePath "powershell.exe" -ArgumentList $supervisorArguments `
        -WorkingDirectory $root -WindowStyle Hidden
    Write-Host "[ok] Installed and started the supervised logon launcher." -ForegroundColor Green
    Write-Host "[note] Run this script once as Administrator for startup before login." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Supervisor checks every 15 seconds and starts stopped components." -ForegroundColor Cyan
Write-Host "Log: $root\logs\supervisor-YYYYMMDD.log" -ForegroundColor DarkGray
Write-Host "Permanent endpoint: https://llm.grand-ice.com/v1" -ForegroundColor DarkGray
