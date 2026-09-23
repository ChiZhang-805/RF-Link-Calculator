param([Parameter(Mandatory=$true)][string]$Iscc)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = Join-Path $PWD '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath $Iscc)) { throw 'Python or Inno Setup compiler not found.' }
$env:PYTHONUTF8 = '1'
& $taskPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $taskPython scripts/collect_notices.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& $taskPython -m PyInstaller --noconfirm RF-Link.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$taskVersion = (& $taskPython -c 'from rf_link_calculator import __version__; print(__version__)').Trim()
$taskReport = Join-Path $PWD "artifacts\exe-self-test-$taskVersion.json"
$taskTestData = Join-Path $PWD 'artifacts\desktop-build-smoke'
$taskPreviousData = $env:RF_LINK_DATA_DIR
try {
    $env:RF_LINK_DATA_DIR = $taskTestData
    $taskRun = Start-Process -FilePath (Join-Path $PWD 'dist\RF-Link\RF-Link.exe') -ArgumentList ('--self-test "' + $taskReport + '"') -WindowStyle Hidden -PassThru
    if (!$taskRun.WaitForExit(120000)) { Stop-Process -Id $taskRun.Id; throw 'Frozen self-test timed out.' }
    if ($taskRun.ExitCode -ne 0 -or !(Test-Path -LiteralPath $taskReport)) { throw 'Frozen self-test failed.' }
} finally { $env:RF_LINK_DATA_DIR = $taskPreviousData }
& $Iscc "/DAppVersion=$taskVersion" installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$taskPortable = Join-Path $PWD "dist\RF-Link-$taskVersion-Windows-x64.zip"
Compress-Archive -LiteralPath (Join-Path $PWD 'dist\RF-Link') -DestinationPath $taskPortable -Force
$taskFiles = @($taskPortable, (Join-Path $PWD "dist\RF-Link-Setup-$taskVersion-x64.exe"))
$taskLines = $taskFiles | ForEach-Object { $taskHash = Get-FileHash -LiteralPath $_ -Algorithm SHA256; $taskHash.Hash.ToLower() + '  ' + (Split-Path $_ -Leaf) }
$taskLines | Set-Content -LiteralPath (Join-Path $PWD 'dist\SHA256SUMS.txt') -Encoding ascii
Write-Output "Built RF Link $taskVersion. Verify the installed application before publishing."
