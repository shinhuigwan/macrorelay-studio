$ErrorActionPreference = "SilentlyContinue"
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$playerScript = Join-Path $studioRoot "run_player.py"
$legacyPackages = Join-Path $studioRoot ".venv\Lib\site-packages"
$runtimeCandidates = @(
    @((Join-Path $studioRoot ".venv\Scripts\python.exe"), (Join-Path $studioRoot ".venv\Scripts\pythonw.exe"), $false),
    @((Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"), (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"), $false),
    @((Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"), (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"), $true)
)

foreach ($candidate in $runtimeCandidates) {
    $python = $candidate[0]
    $pythonw = $candidate[1]
    $useLegacyPackages = $candidate[2]
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pythonw)) {
        continue
    }
    if ($useLegacyPackages -and (Test-Path -LiteralPath $legacyPackages)) {
        $env:PYTHONPATH = $legacyPackages
    }
    else {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    & $python -c "from PySide6 import QtWidgets" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Remove-Item -LiteralPath (Join-Path $studioRoot "player-launch-error.txt") -ErrorAction SilentlyContinue
        Start-Process -FilePath $pythonw -ArgumentList @($playerScript) -WorkingDirectory $studioRoot -WindowStyle Hidden
        exit 0
    }
}

$sysPythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if ($sysPythonw) {
    Start-Process -FilePath $sysPythonw -ArgumentList @($playerScript) -WorkingDirectory $studioRoot -WindowStyle Hidden
    exit 0
}

Set-Content -LiteralPath (Join-Path $studioRoot "player-launch-error.txt") `
    -Value "MacroRelay Player를 실행할 Python/PySide6 환경을 찾지 못했습니다." -Encoding utf8
exit 1
