param([int]$Port = 8501)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先按README安装虚拟环境与依赖。' }
if (-not $env:RF_LINK_DATA_DIR) { $env:RF_LINK_DATA_DIR = Join-Path $projectRoot 'outputs' }
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $projectRoot
& $pythonExe -m rf_link_calculator.presentation.server --port $Port
