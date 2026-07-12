# run_eval_round.ps1
# Runs ONE round of all failure types in sequence, to help build up a
# larger, varied set of incidents for eval_harness.py testing.
#
# Run this from inside the failure-injection folder. Run it 2-3 times,
# spaced a few minutes apart, to get real variety in timing/context
# (some incidents will capture thin context, some rich, naturally).
#
# Requires: docker compose up --build (Terminal 1) and
# python log-collector/collector.py (Terminal 2) already running.

Write-Host "=== EVAL EXPANSION ROUND ===" -ForegroundColor Cyan

Write-Host "`n[1/4] Wrong password test..." -ForegroundColor Yellow
.\wrong_password.ps1
Start-Sleep -Seconds 2
try { Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 8
.\revert_password.ps1
Start-Sleep -Seconds 5

Write-Host "`n[2/4] Postgres stop/start test..." -ForegroundColor Yellow
docker compose stop postgres
Start-Sleep -Seconds 2
try { Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 6
docker compose start postgres
Start-Sleep -Seconds 10

Write-Host "`n[3/4] Latency test..." -ForegroundColor Yellow
try { Invoke-RestMethod -Uri "http://localhost:8000/debug/slow-query" -Method Post -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 2
try { Invoke-RestMethod -Uri "http://localhost:8000/debug/slow-query" -Method Post -ErrorAction SilentlyContinue } catch {}

Write-Host "`n[4/4] Memory leak test (will trigger an OOM kill)..." -ForegroundColor Yellow
for ($i = 1; $i -le 36; $i++) {
    try {
        Invoke-RestMethod -Uri "http://localhost:8000/debug/leak" -Method Post -ErrorAction Stop | Out-Null
    } catch {
        Write-Host "  Container likely OOM-killed around call $i" -ForegroundColor Red
        break
    }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 8
docker compose restart order-service
Start-Sleep -Seconds 5

Write-Host "`n=== ROUND COMPLETE ===" -ForegroundColor Green
Write-Host "Verifying health..."
Invoke-RestMethod -Uri "http://localhost:8000/health" -Method Get
