# latency.ps1
# FOR FAILURE TESTING ONLY
# Calls the /debug/slow-query endpoint repeatedly, which runs an
# artificially slow database query (pg_sleep) to simulate a slow or
# high-latency database connection.
#
# Run this from the project root: d:\ai-rca
# Press Ctrl+C to stop the simulation.

Write-Host "=== Latency / Slow Query Failure Injection ===" -ForegroundColor Cyan
Write-Host "This will repeatedly call POST /debug/slow-query on order-service."
Write-Host "Each call takes ~10 seconds to complete, simulating a slow DB."
Write-Host "Press Ctrl+C to stop the simulation."
Write-Host ""

$callCount = 0

try {
    while ($true) {
        $callCount++
        $start = Get-Date
        try {
            $result = Invoke-RestMethod -Uri "http://localhost:8000/debug/slow-query" -Method Post
            $elapsed = (Get-Date) - $start
            Write-Host "Call #$callCount completed in $([math]::Round($elapsed.TotalSeconds, 1))s"
        } catch {
            Write-Host "Call #$callCount FAILED:" -ForegroundColor Red
            Write-Host $_.Exception.Message
        }
    }
} finally {
    Write-Host ""
    Write-Host "Latency simulation stopped after $callCount calls." -ForegroundColor Yellow
}
