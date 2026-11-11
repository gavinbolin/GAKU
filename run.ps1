param(
    [string]$Script = "..\app.py",
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$ScriptArgs
)
$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Create .venv with Python 3.12 and install requirements.txt first." }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $PSScriptRoot
try { & $python $Script @ScriptArgs; exit $LASTEXITCODE } finally { Pop-Location }
