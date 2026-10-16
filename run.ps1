$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONUNBUFFERED = '1'

if (-not (Test-Path '.venv')) {
    uv venv --python 3.12
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

uv pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python generate_sound.py --ensure
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'SMILE·GUN: http://127.0.0.1:5000' -ForegroundColor Green
Write-Host 'For phone play over Wi-Fi use run_mobile.ps1 (RU: для смартфона).' -ForegroundColor Cyan
Write-Host 'Exit from the in-game CLOSE APPLICATION button or Ctrl+C.' -ForegroundColor Yellow
uv run python app.py
