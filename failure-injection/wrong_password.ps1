# wrong_password.ps1
# FOR FAILURE TESTING ONLY
# Temporarily overrides DB_PASSWORD for order-service with an incorrect
# value, forcing a real psycopg2 authentication error in the logs.
#
# Run this from the project root: d:\ai-rca
# After testing, run revert_password.ps1 to restore normal operation.

Write-Host "=== Wrong Password Failure Injection ===" -ForegroundColor Cyan
Write-Host "This will restart order-service with an INCORRECT database password."
Write-Host "This should produce a real authentication error in the logs."
Write-Host ""

$overrideFile = ".env.wrongpassword"

Write-Host "Step 1: Writing temporary override env file ($overrideFile)..."
@"
DB_USER=postgres
DB_PASSWORD=this_is_definitely_wrong
DB_NAME=postgres
"@ | Out-File -FilePath $overrideFile -Encoding utf8

Write-Host "Step 2: Restarting order-service with the wrong password..."
docker compose --env-file $overrideFile up -d --force-recreate order-service

Write-Host ""
Write-Host "Step 3: order-service is now running with a bad password." -ForegroundColor Yellow
Write-Host "Trigger a request to see the failure, e.g.:"
Write-Host '  Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get'
Write-Host ""
Write-Host "When done testing, run: .\failure-injection\revert_password.ps1" -ForegroundColor Green
