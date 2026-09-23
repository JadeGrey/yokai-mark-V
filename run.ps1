# run.ps1 - Yokai Bot Windows Runner with Auto-Restart
# Purpose: Start Yokai, monitor the process, and automatically restart on crashes with a short cooldown.

$ErrorActionPreference = "Continue"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "      Yokai Mark V - Discord Bot          " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Activate virtual environment if present
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[INIT] Activating virtual environment (.venv)..." -ForegroundColor Green
    & ".\.venv\Scripts\Activate.ps1"
} elseif (Test-Path ".\venv\Scripts\Activate.ps1") {
    Write-Host "[INIT] Activating virtual environment (venv)..." -ForegroundColor Green
    & ".\venv\Scripts\Activate.ps1"
} else {
    Write-Host "[WARN] No virtual environment found in .\.venv. Using system Python." -ForegroundColor Yellow
}

# 2. Main runner loop
while ($true) {
    Write-Host "[START] Launching Yokai..." -ForegroundColor Cyan
    
    python -m yokai
    $exitCode = $LASTEXITCODE

    # Exit code 0 indicates intentional shutdown or KeyboardInterrupt
    if ($exitCode -eq 0) {
        Write-Host "`n[STOP] Yokai shut down cleanly (exit code 0)." -ForegroundColor Green
        break
    } else {
        Write-Host "`n[CRASH] Yokai terminated with exit code $exitCode." -ForegroundColor Red
        Write-Host "[COOLDOWN] Waiting 5 seconds before restarting (Press Ctrl+C to abort)..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}
