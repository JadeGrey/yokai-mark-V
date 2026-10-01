# run.ps1 - Yokai Bot Windows Runner with Auto-Restart
# Purpose: Start Yokai, monitor the process, and automatically restart on crashes
#          with exponential backoff. Every supervisor event is also logged to
#          logs\supervisor.log so crash history survives a closed terminal.

$ErrorActionPreference = "Continue"

$SupervisorLogDir = Join-Path $PSScriptRoot "logs"
$SupervisorLogPath = Join-Path $SupervisorLogDir "supervisor.log"
New-Item -ItemType Directory -Force -Path $SupervisorLogDir | Out-Null

function Write-SupervisorLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("INFO", "SUCCESS", "WARN", "ERROR")][string]$Level = "INFO"
    )
    $color = switch ($Level) {
        "SUCCESS" { "Green" }
        "WARN" { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [$Level] $Message"
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $SupervisorLogPath -Value $line -Encoding utf8
}

function Format-Duration {
    param([TimeSpan]$Span)
    if ($Span.TotalSeconds -lt 60) {
        return "{0}s" -f [int]$Span.TotalSeconds
    }
    return "{0}m {1}s" -f [int]$Span.TotalMinutes, $Span.Seconds
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "      Yokai Mark V - Discord Bot          " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-SupervisorLog -Message "Supervisor starting. Logging to $SupervisorLogPath"

# 1. Activate virtual environment if present
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-SupervisorLog -Level SUCCESS -Message "Activating virtual environment (.venv)..."
    & ".\.venv\Scripts\Activate.ps1"
} elseif (Test-Path ".\venv\Scripts\Activate.ps1") {
    Write-SupervisorLog -Level SUCCESS -Message "Activating virtual environment (venv)..."
    & ".\venv\Scripts\Activate.ps1"
} else {
    Write-SupervisorLog -Level WARN -Message "No virtual environment found in .\.venv. Using system Python."
}

# 2. Main runner loop with exponential backoff on repeated crashes
$BaseBackoffSeconds = 5
$MaxBackoffSeconds = 60
$StableRunThresholdSeconds = 60
$backoff = $BaseBackoffSeconds
$restartCount = 0

while ($true) {
    Write-SupervisorLog -Message "Launching Yokai (restart #$restartCount)..."
    $launchTime = Get-Date

    python -m yokai
    $exitCode = $LASTEXITCODE
    $uptime = (Get-Date) - $launchTime
    $uptimeStr = Format-Duration -Span $uptime

    # Exit code 0 indicates intentional shutdown or KeyboardInterrupt
    if ($exitCode -eq 0) {
        Write-SupervisorLog -Level SUCCESS -Message "Yokai shut down cleanly after $uptimeStr (exit code 0)."
        break
    }

    $restartCount++
    Write-SupervisorLog -Level ERROR -Message "Yokai terminated with exit code $exitCode after $uptimeStr."

    # Reset backoff if the last run was stable for a while; otherwise escalate it.
    if ($uptime.TotalSeconds -ge $StableRunThresholdSeconds) {
        $backoff = $BaseBackoffSeconds
    }

    Write-SupervisorLog -Level WARN -Message "Waiting $backoff seconds before restarting (Press Ctrl+C to abort)..."
    Start-Sleep -Seconds $backoff

    $backoff = [Math]::Min($backoff * 2, $MaxBackoffSeconds)
}
