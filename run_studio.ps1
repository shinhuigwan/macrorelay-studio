$ErrorActionPreference = "SilentlyContinue"
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$studioScript = Join-Path $studioRoot "run_studio.py"
$installerScript = Join-Path $studioRoot "install.ps1"
$bootstrapMarker = Join-Path $studioRoot ".bootstrap-complete"
$runtimePackages = Join-Path $studioRoot "runtime_packages"
$legacyPackages = Join-Path $studioRoot ".venv\Lib\site-packages"
$runtimeCandidates = @(
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
    $pythonTag = (& $python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
    $candidateOpenCvPackages = if ($pythonTag) { Join-Path $studioRoot "runtime\opencv\$pythonTag\packages" } else { "" }
    $packagePaths = @()
    if (Test-Path -LiteralPath $runtimePackages) {
        $packagePaths += $runtimePackages
    }
    if ($candidateOpenCvPackages -and (Test-Path -LiteralPath $candidateOpenCvPackages)) {
        $packagePaths += $candidateOpenCvPackages
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
        if (-not (Test-Path -LiteralPath $bootstrapMarker) -and (Test-Path -LiteralPath $installerScript)) {
            Start-Process -FilePath "powershell.exe" -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installerScript, "-Launch"
            ) -WorkingDirectory $studioRoot
            exit 0
        }
        Remove-Item -LiteralPath (Join-Path $studioRoot "studio-launch-error.txt") -ErrorAction SilentlyContinue
        Start-Process -FilePath $pythonw -ArgumentList @($studioScript) -WorkingDirectory $studioRoot -WindowStyle Hidden
        exit 0
    }
}

if (Test-Path -LiteralPath $installerScript) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installerScript, "-Launch"
    ) -WorkingDirectory $studioRoot
    exit 0
}

Set-Content -LiteralPath (Join-Path $studioRoot "studio-launch-error.txt") `
    -Value "MacroRelay Studio를 실행할 Python/PySide6 환경을 찾지 못했습니다." -Encoding utf8
exit 1
