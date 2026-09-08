param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $studioRoot

function Find-CompatiblePython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @("3.12", "3.11")) {
            $resolved = @(& $launcher.Source "-$version" -c "import sys; print(sys.executable)" 2>$null)
            $candidatePath = if ($resolved.Count -gt 0) { [string]$resolved[-1] } else { "" }
            if ($LASTEXITCODE -eq 0 -and $candidatePath -and (Test-Path -LiteralPath $candidatePath)) {
                return $candidatePath
            }
        }
    }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Find-AutoHotkeyV1 {
    $configured = Join-Path $studioRoot "ahk_path.txt"
    if (Test-Path -LiteralPath $configured) {
        $saved = (Get-Content -LiteralPath $configured -Raw -ErrorAction SilentlyContinue).Trim()
        if ($saved -and (Test-Path -LiteralPath $saved)) { return $saved }
    }
    foreach ($candidate in @(
        "C:\Program Files\AutoHotkey\AutoHotkey.exe",
        "C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU64.exe",
        "C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

Write-Host "[1/5] Checking Python 3.11/3.12..." -ForegroundColor Cyan
$basePython = Find-CompatiblePython
if (-not $basePython) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11 or 3.12 is required. Install Python from python.org and run setup_windows.bat again."
    }
    Write-Host "Python is missing. Installing Python 3.12 for the current user..." -ForegroundColor Yellow
    & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Python installation failed with exit code $LASTEXITCODE."
    }
    $basePython = Find-CompatiblePython
    if (-not $basePython) {
        throw "Python was installed but could not be located. Sign out once or install Python 3.12 manually."
    }
}

$venvPython = Join-Path $studioRoot ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $studioRoot ".venv\Scripts\pythonw.exe"
$venvHealthy = $false
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -c "import sys; assert sys.version_info[:2] in ((3, 11), (3, 12))" 2>$null
    $venvHealthy = $LASTEXITCODE -eq 0
}
if (-not $venvHealthy) {
    Write-Host "[2/5] Creating a private Python environment..." -ForegroundColor Cyan
    & $basePython -m venv --clear (Join-Path $studioRoot ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "Could not create the .venv Python environment."
    }
} else {
    Write-Host "[2/5] Existing Python environment is healthy." -ForegroundColor Green
}

Write-Host "[3/5] Installing MacroRelay core components..." -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install --disable-pip-version-check --upgrade -r (Join-Path $studioRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Required Python component installation failed." }

Write-Host "[4/5] Checking AutoHotkey v1..." -ForegroundColor Cyan
$autoHotkey = Find-AutoHotkeyV1
if (-not $autoHotkey) {
    $installer = Join-Path $env:TEMP "AutoHotkey_1.1.37.02_setup.exe"
    $downloadUrl = "https://github.com/Lexikos/AutoHotkey_L/releases/download/v1.1.37.02/AutoHotkey_1.1.37.02_setup.exe"
    $expectedHash = "49A48E879F7480238D2FE17520AC19AFE83685AAC0B886719F9E1EAC818B75CC"
    try {
        Write-Host "AutoHotkey v1 is missing. Downloading the official signed installer..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $downloadUrl -OutFile $installer -UseBasicParsing
        $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        if ($actualHash -ne $expectedHash) {
            throw "AutoHotkey installer checksum mismatch."
        }
        $process = Start-Process -FilePath $installer -ArgumentList "/S" -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "AutoHotkey installer returned exit code $($process.ExitCode)."
        }
        $autoHotkey = Find-AutoHotkeyV1
    } catch {
        Write-Warning "AutoHotkey automatic installation failed: $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
if ($autoHotkey) {
    Set-Content -LiteralPath (Join-Path $studioRoot "ahk_path.txt") -Value $autoHotkey -Encoding utf8
    Write-Host "AutoHotkey connected: $autoHotkey" -ForegroundColor Green
} else {
    Write-Warning "Studio can start, but macro execution requires AutoHotkey v1. Connect it later in Settings > Components."
}

if (-not $SkipBrowser) {
    Write-Host "[5/5] Installing the browser automation component..." -ForegroundColor Cyan
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Chromium installation failed. Studio and image search still work; install it later from Settings for browser automation."
    }
} else {
    Write-Host "[5/5] Browser component skipped by option." -ForegroundColor DarkGray
}

& $venvPython -c "import cv2,mss,numpy; from PySide6 import QtWidgets; print('Core import check: OK')"
if ($LASTEXITCODE -ne 0) { throw "Installed component verification failed." }
if (-not (Test-Path -LiteralPath $venvPythonw)) {
    throw "pythonw.exe is missing from the private environment."
}

Write-Host "MacroRelay Studio setup is complete." -ForegroundColor Green
