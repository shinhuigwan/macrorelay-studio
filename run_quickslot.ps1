$ErrorActionPreference = "SilentlyContinue"
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$quickslotScript = Join-Path $studioRoot "quickslot_app.py"
$runtimePackages = Join-Path $studioRoot "runtime_packages"
$legacyPackages = Join-Path $studioRoot ".venv\Lib\site-packages"
$bundledOpenCvPackages = Join-Path $studioRoot "runtime\opencv\cp312\packages"

$sysPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Path
$sysPythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Path
if (-not $sysPythonw -and $sysPython) {
    $sysPythonw = $sysPython
}

$runtimeCandidates = @()
if ($sysPython -and $sysPythonw) {
    $runtimeCandidates += ,@($sysPython, $sysPythonw, $false)
}
$runtimeCandidates += @(
    @((Join-Path $studioRoot ".venv\Scripts\python.exe"), (Join-Path $studioRoot ".venv\Scripts\pythonw.exe"), $true),
    @((Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"), (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"), $true),
    @((Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"), (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"), $false)
)

foreach ($candidate in $runtimeCandidates) {
    $python = $candidate[0]
    $pythonw = $candidate[1]
    $useLegacyPackages = $candidate[2]
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pythonw)) {
        continue
    }
    $packagePaths = @()
    if (Test-Path -LiteralPath $runtimePackages) {
        $packagePaths += $runtimePackages
    }
    if (Test-Path -LiteralPath $bundledOpenCvPackages) {
        $packagePaths += $bundledOpenCvPackages
    }
    if ($useLegacyPackages -and (Test-Path -LiteralPath $legacyPackages)) {
        $packagePaths += $legacyPackages
    }
    if ($packagePaths.Count -gt 0) {
        $env:PYTHONPATH = $packagePaths -join [IO.Path]::PathSeparator
    }
    else {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    & $python -c "from PySide6 import QtWidgets" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Remove-Item -LiteralPath (Join-Path $studioRoot "quickslot-launch-error.txt") -ErrorAction SilentlyContinue
        Start-Process -FilePath $pythonw -ArgumentList @($quickslotScript) -WorkingDirectory $studioRoot -WindowStyle Hidden
        exit 0
    }
}

Set-Content -LiteralPath (Join-Path $studioRoot "quickslot-launch-error.txt") `
    -Value "QuickSlot Deck을 실행할 Python/PySide6 환경을 찾지 못했습니다." -Encoding utf8
exit 1
