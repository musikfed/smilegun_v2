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
uv run python mobile_tls.py --print
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:SMILEGUN_HTTPS = '1'
$env:SMILEGUN_PORT = '5443'
Write-Host ''
Write-Host 'SmileGun MOBILE HTTPS is starting on port 5443.' -ForegroundColor Green
Write-Host 'Install and trust .smilegun-certs\smilegun-mobile-ca.crt on the phone once.' -ForegroundColor Yellow
Write-Host 'Then open the https://IP:5443 URL printed above and allow the front camera.' -ForegroundColor Yellow
uv run python app.py
