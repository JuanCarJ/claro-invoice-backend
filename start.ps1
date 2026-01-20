# Start Azure Functions with virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Cyan
& "$PSScriptRoot\.venv\Scripts\Activate.ps1"

Write-Host "Starting Azure Functions..." -ForegroundColor Green
func start
