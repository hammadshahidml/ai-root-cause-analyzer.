# memory_leak.ps1
# FOR FAILURE TESTING ONLY
# Repeatedly calls the /debug/leak endpoint on order-service, which
# allocates ~10MB per call and never releases it, simulating a memory
# leak until the container runs low on memory or becomes unresponsive.
#
# Run this from the project root: d:\ai-rca
# Press Ctrl+C to stop the simulation at any point.

Write-Host "=== Memory Leak Failure Injection ===" -ForegroundColor Cyan
Write-Host "This will repeatedly call POST /debug/leak on order-service."
Write-Host "Each call holds ~10MB of memory permanently."
Write-Host "Press Ctrl+C to stop the simulation."
Write-Host ""

$callCount = 0

try {
    while ($true) {
        $callCount++
        try {
            $result = Invoke-RestMethod -Uri "http://localhost:8000/debug/leak" -Method Post
            Write-Host "Call #$callCount - Approx MB held: $($result.approx_mb)"
        } catch {
            Write-Host "Call #$callCount FAILED - order-service may be unresponsive:" -ForegroundColor Red
            Write-Host $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Memory leak simulation stopped after $callCount calls." -ForegroundColor Yellow
    Write-Host "Restart order-service to release the leaked memory:"
    Write-Host "  docker compose restart order-service"
}
