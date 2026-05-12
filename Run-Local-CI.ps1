Param(
  [switch]$SkipE2E,
  [switch]$Quick
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step($name) {
  Write-Host ""
  Write-Host "==> $name" -ForegroundColor Cyan
}

Step "Python compile checks"
& ".\.venv\Scripts\python.exe" -m py_compile site_server.py scoring.py alerts.py atomic_io.py product_intelligence.py

Step "Pytest suite"
& ".\.venv\Scripts\python.exe" -m pytest tests/ -q

Step "JavaScript syntax checks"
& node --check "pitch_site/product_session.js"
& node --check "pitch_site/browser_cv.js"
& node --check "pitch_site/app.js"
& node --check "pitch_site/overlay.js"
& node --check "pitch_site/evidence.js"
& node --check "pitch_site/landing.js"
& node --check "pitch_site/cv_config.js"
& node --check "pitch_site/share_proof.js"

if (-not $SkipE2E) {
  Step "Playwright smoke"
  if ($Quick) {
    & npm run test:e2e -- --grep "renders /play|demo lock flow marks proof export"
  } else {
    & npm run test:e2e
  }
} else {
  Step "Playwright smoke skipped"
}

Write-Host ""
Write-Host "Local CI passed." -ForegroundColor Green
