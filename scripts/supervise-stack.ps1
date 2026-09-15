<#
.SYNOPSIS
    Keeps the complete Grandice stack running and restarts crashed components.

.DESCRIPTION
    Intended for a hidden Startup-folder launcher or a SYSTEM scheduled task.
    A named mutex prevents duplicate supervisors. Components are restarted only
    when both their health probe and process check indicate they are stopped.
#>
[CmdletBinding()]
param(
    [int]$CheckIntervalSeconds = 15,
    [switch]$SkipWebUI,
    [switch]$SkipTunnel
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$mutex = New-Object System.Threading.Mutex($false, "Global\GrandiceLLM-Supervisor-v1")
$ownsMutex = $false
try {
    $ownsMutex = $mutex.WaitOne(0, $false)
}
catch [System.Threading.AbandonedMutexException] {
    $ownsMutex = $true
}
if (-not $ownsMutex) {
    exit 0
}

function Write-SupervisorLog {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Add-Content -Path (Join-Path $logDir ("supervisor-{0}.log" -f (Get-Date -Format "yyyyMMdd"))) -Value $line -Encoding UTF8
}

function Test-Endpoint {
    param([string]$Uri, [int]$TimeoutSeconds = 4)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec $TimeoutSeconds -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Find-StackProcess {
    param([string]$CommandPattern)
    return Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $CommandPattern } |
        Select-Object -First 1
}

function Start-HiddenProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logDir "$Name-$stamp.out.log"
    $stderr = Join-Path $logDir "$Name-$stamp.err.log"
    try {
        $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        Write-SupervisorLog "Started $Name (PID $($process.Id))."
    }
    catch {
        Write-SupervisorLog "Could not start ${Name}: $($_.Exception.Message)" "ERROR"
    }
}

function Ensure-Ollama {
    if (Test-Endpoint "http://127.0.0.1:11434/api/version") { return }
    if (Find-StackProcess '(?i)ollama\.exe"?\s+serve') { return }

    $ollama = "C:\Users\rites\AppData\Local\Programs\Ollama\ollama.exe"
    if (-not (Test-Path $ollama)) {
        Write-SupervisorLog "Ollama executable is missing: $ollama" "ERROR"
        return
    }

    # Explicit values make startup reliable under both the user account and
    # the SYSTEM account used by an elevated boot task.
    $env:OLLAMA_HOST = "127.0.0.1:11434"
    $env:OLLAMA_MODELS = "C:\Users\rites\.ollama\models"
    $env:OLLAMA_KEEP_ALIVE = "30m"
    $env:OLLAMA_MAX_LOADED_MODELS = "2"
    $env:OLLAMA_FLASH_ATTENTION = "1"
    $env:OLLAMA_KV_CACHE_TYPE = "q8_0"
    $env:OLLAMA_CONTEXT_LENGTH = "8192"
    Start-HiddenProcess -Name "ollama" -Executable $ollama -Arguments @("serve") -WorkingDirectory $root
}

function Ensure-Gateway {
    if (Test-Endpoint "http://127.0.0.1:8080/health") { return }
    if (Find-StackProcess '(?i)-m\s+gateway\.main') { return }

    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        Write-SupervisorLog "Gateway virtual environment is missing." "ERROR"
        return
    }
    # The boot supervisor runs as SYSTEM, so it cannot discover user-scoped
    # Winget packages or models through that account's HOME/LOCALAPPDATA.
    $ffmpeg = Resolve-Path "C:\Users\rites\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    $ffprobe = Resolve-Path "C:\Users\rites\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_*\*\bin\ffprobe.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($ffmpeg) { $env:GRANDICE_FFMPEG_PATH = $ffmpeg.Path }
    if ($ffprobe) { $env:GRANDICE_FFPROBE_PATH = $ffprobe.Path }
    $env:GRANDICE_TESSERACT_PATH = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    $env:GRANDICE_FACE_MODEL_PATH = "C:\Users\rites\.grandice\models\opencv\haarcascade_frontalface_default.xml"
    $env:PRIVACY_WHISPER_MODEL_PATH = "C:\Users\rites\.grandice\models\faster-whisper-large-v3-turbo"
    Start-HiddenProcess -Name "gateway" -Executable $python -Arguments @("-m", "gateway.main") -WorkingDirectory $root
}

function Ensure-WebUI {
    if ($SkipWebUI) { return }
    if (Test-Endpoint "http://127.0.0.1:3000") { return }
    if (Find-StackProcess '(?i)(open-webui\.exe.*serve|start-webui\.ps1)') { return }

    Start-HiddenProcess -Name "webui-launcher" -Executable "powershell.exe" `
        -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start-webui.ps1")) `
        -WorkingDirectory $root
}

function Ensure-Tunnel {
    if ($SkipTunnel) { return }
    if (Test-Endpoint "https://grand-ice.com/health" 8) { return }
    if (Find-StackProcess '(?i)cloudflared\.exe.*tunnel.*run') { return }

    Start-HiddenProcess -Name "tunnel-launcher" -Executable "powershell.exe" `
        -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start-tunnel.ps1")) `
        -WorkingDirectory $root
}

Write-SupervisorLog "Supervisor started as $([Security.Principal.WindowsIdentity]::GetCurrent().Name)."
try {
    while ($true) {
        Ensure-Ollama
        Ensure-Gateway
        Ensure-WebUI
        Ensure-Tunnel
        Start-Sleep -Seconds ([Math]::Max(5, $CheckIntervalSeconds))
    }
}
finally {
    Write-SupervisorLog "Supervisor stopped."
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
