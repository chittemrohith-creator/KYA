param([int]$Port = 5001)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Python = Join-Path (Split-Path $PSScriptRoot -Parent) 'demo-venv/Scripts/python.exe'
if (-not (Test-Path $Python)) { throw 'Sibling demo-venv is required. Create a Python venv and install requirements.txt.' }
& $Python -c "import flask, flask_sqlalchemy, bcrypt; from PIL import Image"
if ($LASTEXITCODE -ne 0) { throw 'Install requirements.txt into the sibling demo-venv before starting.' }
$env:CIVICSYNC_PORT = "$Port"
& $Python run.py
exit $LASTEXITCODE
