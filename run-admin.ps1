# run-admin.ps1 - Start the Yokai admin panel
# Purpose: Activate the virtual environment and launch the local admin panel.
#          The panel opens your browser through a single-use link. There is no
#          auto-restart on purpose: closing the tab or stopping the panel means
#          you want a fresh link, so just run this script again.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    & ".\.venv\Scripts\Activate.ps1"
} elseif (Test-Path ".\venv\Scripts\Activate.ps1") {
    & ".\venv\Scripts\Activate.ps1"
} else {
    Write-Host "No virtual environment found; using system Python." -ForegroundColor Yellow
}

Write-Host "Starting the Yokai admin panel (Ctrl+C to stop)..." -ForegroundColor Cyan
python -m yokai_admin
exit $LASTEXITCODE
