$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$runtimeDir = Join-Path $repoRoot ".runtime\cpython-3.11.14-windows-x86_64-none"
$runtimePython = Join-Path $runtimeDir "python.exe"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$pyvenvConfig = Join-Path $repoRoot ".venv\pyvenv.cfg"

if (-not (Test-Path $runtimePython)) {
    throw "Workspace Python runtime is missing: $runtimePython"
}
if (-not (Test-Path $venvPython)) {
    throw "Project virtualenv Python is missing: $venvPython"
}
if (-not (Test-Path $pyvenvConfig)) {
    throw "Project virtualenv config is missing: $pyvenvConfig"
}

$homeLine = "home = $runtimeDir"
$configLines = Get-Content -LiteralPath $pyvenvConfig
$updatedLines = @()
$sawHome = $false
foreach ($line in $configLines) {
    if ($line -match "^home\s*=") {
        $updatedLines += $homeLine
        $sawHome = $true
    } else {
        $updatedLines += $line
    }
}
if (-not $sawHome) {
    $updatedLines = @($homeLine) + $updatedLines
}
Set-Content -LiteralPath $pyvenvConfig -Value $updatedLines -Encoding utf8

& $venvPython -c "import sys, pydantic; print(sys.executable); print(sys.version); print(pydantic.__version__)"
& $venvPython scripts\run_paper_eval.py --account-id paper-live-1000 --all --report-dir data\report
