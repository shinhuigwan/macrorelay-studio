param(
    [switch]$Launch,
    [switch]$IncludeBrowser,
    [switch]$SkipExternalApps
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logPath = Join-Path $studioRoot "bootstrap-install.log"
$errorPath = Join-Path $studioRoot "bootstrap-install-error.txt"
$markerPath = Join-Path $studioRoot ".bootstrap-complete"
$runtimePackages = Join-Path $studioRoot "runtime_packages"
$venvRoot = Join-Path $studioRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvPythonw = Join-Path $venvRoot "Scripts\pythonw.exe"

function Write-Step([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $message
    Write-Host $line -ForegroundColor Cyan
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Invoke-Checked([string]$program, [string[]]$arguments) {
    & $program @arguments 2>&1 | Tee-Object -FilePath $logPath -Append
    if ($LASTEXITCODE -ne 0) {
        throw "실행 실패($LASTEXITCODE): $program $($arguments -join ' ')"
    }
}

function Test-Python([string]$program, [string[]]$prefix) {
    if (-not (Test-Path -LiteralPath $program)) { return $false }
    & $program @prefix -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Find-Python311 {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Python $candidate @()) { return @{ Program = $candidate; Prefix = @() } }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py -and (Test-Python $py.Source @("-3.11"))) {
        return @{ Program = $py.Source; Prefix = @("-3.11") }
    }
    return $null
}

function Find-Tesseract {
    foreach ($candidate in @(
        "C:\Program Files\Tesseract-OCR\tesseract.exe",
        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Write-PathFile([string]$name, [string]$value) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $studioRoot $name), $value + [Environment]::NewLine, $encoding)
}

function Install-AutoHotkeyV1 {
    $target = Join-Path $studioRoot "runtime\autohotkey-v1"
    $executable = Join-Path $target "AutoHotkeyU64.exe"
    if (-not (Test-Path -LiteralPath $executable)) {
        Write-Step "MacroRelay 호환 AutoHotkey v1 포터블 다운로드"
        $runtimeRoot = Split-Path -Parent $target
        New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
        $archive = Join-Path $runtimeRoot "AutoHotkey_1.1.37.02.zip"
        $url = "https://github.com/Lexikos/AutoHotkey_L/releases/download/v1.1.37.02/AutoHotkey_1.1.37.02.zip"
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
        $expected = "6F3663F7CDD25063C8C8728F5D9B07813CED8780522FD1F124BA539E2854215F"
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
        if ($actual -ne $expected) {
            throw "AutoHotkey 다운로드 검증 실패: $actual"
        }
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $target -Force
        Remove-Item -LiteralPath $archive -Force
    }
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "AutoHotkeyU64.exe를 구성하지 못했습니다."
    }
    Write-PathFile "ahk_path.txt" $executable
    $compiler = Join-Path $target "Compiler\Ahk2Exe.exe"
    if (Test-Path -LiteralPath $compiler) {
        Write-PathFile "ahk2exe_path.txt" $compiler
    }
}

function Install-TesseractSupport {
    $tesseract = Find-Tesseract
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $tesseract -and $winget) {
        Write-Step "Tesseract OCR 자동 설치"
        try {
            Invoke-Checked $winget.Source @(
                "install", "--id", "UB-Mannheim.TesseractOCR", "-e", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"
            )
        }
        catch {
            Write-Warning "Tesseract 설치를 건너뜁니다: $($_.Exception.Message)"
        }
        $tesseract = Find-Tesseract
    }
    if ($tesseract) {
        Write-PathFile "tesseract_path.txt" $tesseract
    }

    $tessdata = Join-Path $studioRoot "tessdata"
    New-Item -ItemType Directory -Path $tessdata -Force | Out-Null
    foreach ($language in @("eng", "kor")) {
        $target = Join-Path $tessdata "$language.traineddata"
        if (-not (Test-Path -LiteralPath $target)) {
            $download = "$target.download"
            try {
                Write-Step "Tesseract $language 언어 데이터 다운로드"
                Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata" -OutFile $download -UseBasicParsing
                if ((Get-Item -LiteralPath $download).Length -lt 1024) { throw "다운로드 파일이 너무 작습니다." }
                Move-Item -LiteralPath $download -Destination $target -Force
            }
            catch {
                Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue
                Write-Warning "$language 언어 데이터 다운로드를 건너뜁니다: $($_.Exception.Message)"
            }
        }
    }
}

try {
    Set-Content -LiteralPath $logPath -Value "MacroRelay Studio bootstrap $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8
    Remove-Item -LiteralPath $errorPath -Force -ErrorAction SilentlyContinue

    $python = Find-Python311
    if (-not $python) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw "Python 3.11이 없고 winget도 사용할 수 없습니다. Python 3.11을 먼저 설치해 주세요."
        }
        Write-Step "Python 3.11 자동 설치"
        Invoke-Checked $winget.Source @(
            "install", "--id", "Python.Python.3.11", "-e", "--scope", "user", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"
        )
        $python = Find-Python311
        if (-not $python) { throw "Python 3.11 설치 후 실행 파일을 찾지 못했습니다." }
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Step "MacroRelay 전용 Python 환경 생성"
        Invoke-Checked $python.Program @($python.Prefix + @("-m", "venv", $venvRoot))
    }

    Write-Step "기본 UI 및 파일 처리 패키지 설치"
    Invoke-Checked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip", "setuptools", "wheel")
    Invoke-Checked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "-r", (Join-Path $studioRoot "requirements.txt"))

    Write-Step "OpenCV·OCR·Windows 자동화 엔진 설치"
    New-Item -ItemType Directory -Path $runtimePackages -Force | Out-Null
    Invoke-Checked $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
        "--target", $runtimePackages, "-r", (Join-Path $studioRoot "requirements-runtime.txt")
    )

    Install-AutoHotkeyV1
    if (-not $SkipExternalApps) { Install-TesseractSupport }

    if ($IncludeBrowser) {
        Write-Step "Playwright Chromium 브라우저 설치"
        Invoke-Checked $venvPython @("-m", "playwright", "install", "chromium")
    }

    & $venvPython -c "import sys; sys.path.insert(0, r'$runtimePackages'); from PySide6 import QtWidgets; import cv2, mss; print('MacroRelay runtime OK')" 2>&1 | Tee-Object -FilePath $logPath -Append
    if ($LASTEXITCODE -ne 0) { throw "설치 후 필수 모듈 검증에 실패했습니다." }

    Set-Content -LiteralPath $markerPath -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding UTF8
    Write-Step "필수 구성요소 설치 완료"
    if ($Launch) {
        Start-Process -FilePath $venvPythonw -ArgumentList @((Join-Path $studioRoot "run_studio.py")) -WorkingDirectory $studioRoot -WindowStyle Hidden
    }
    exit 0
}
catch {
    $message = "MacroRelay Studio 자동 설치 실패`r`n$($_.Exception.Message)"
    Set-Content -LiteralPath $errorPath -Value $message -Encoding UTF8
    Write-Host $message -ForegroundColor Red
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($message + "`r`n`r`n" + $errorPath, "MacroRelay 설치 실패", "OK", "Error") | Out-Null
    }
    catch {}
    exit 1
}
