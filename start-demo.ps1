param([ValidateRange(1, 65535)][int]$Port = 5001)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'Repository .venv not found. From this folder run: python -m venv .venv'
}
& $Python -c "import flask, flask_sqlalchemy, bcrypt; from PIL import Image"
if ($LASTEXITCODE -ne 0) {
    throw 'Missing dependencies. Run: .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}
$env:CIVICSYNC_PORT = "$Port"
& $Python (Join-Path $PSScriptRoot 'run.py')
exit $LASTEXITCODE
