# revert_password.ps1
# Restores order-service to the correct DB_PASSWORD from the main .env
# file and restarts it cleanly.
#
# Run this from the project root: d:\ai-rca

Write-Host "=== Reverting Wrong Password ===" -ForegroundColor Cyan
Write-Host "Step 1: Restarting order-service using the normal .env file..."

docker compose --env-file ..\.env up -d --force-recreate order-service

Write-Host "Step 2: Cleaning up the temporary override file..."
if (Test-Path ".env.wrongpassword") {
    Remove-Item ".env.wrongpassword"
    Write-Host "Removed .env.wrongpassword"
}

Write-Host ""
Write-Host "order-service restored to normal operation." -ForegroundColor Green
Write-Host "Verify with: Invoke-RestMethod -Uri `"http://localhost:8000/health`" -Method Get"
