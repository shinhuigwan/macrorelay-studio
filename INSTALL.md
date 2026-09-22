# 설치/실행 (다른 PC)

## 가장 쉬운 자동 설치

1. GitHub의 Windows ZIP을 내려받아 압축을 풉니다.
2. `install.bat`을 더블클릭합니다.
3. Python 3.11, 필수 Python 패키지, OpenCV/OCR/Windows 자동화 엔진과 MacroRelay 호환 AutoHotkey v1이 자동 구성됩니다.
4. 완료 후 Studio가 자동 실행됩니다.

`run_studio.bat`을 먼저 실행해도 필수 환경이 없으면 자동 설치 화면으로 전환됩니다. 설치 진행은 `bootstrap-install.log`, 실패 원인은 `bootstrap-install-error.txt`에 기록됩니다.

Tesseract OCR도 winget 사용이 가능한 PC에서는 자동 설치하고 한국어·영어 데이터를 내려받습니다. 브라우저 자동화용 Chromium은 용량이 크므로 다음 명령으로 선택 설치합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -IncludeBrowser -Launch
```

## 자동 설치 항목

- Python 3.11 전용 `.venv`
- PySide6, Playwright Python 패키지, Pillow, pytesseract, openpyxl
- OpenCV, MSS, RapidOCR, ONNX Runtime
- pywin32, pywinauto, uiautomation
- AutoHotkey v1.1.37.02 포터블 실행기 및 Ahk2Exe 연결
- Tesseract OCR과 `eng+kor` 언어 데이터(설치 가능한 환경)

## 수동 설치

```bat
cd C:\path\to\macro_tool
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install --target runtime_packages -r requirements-runtime.txt
.venv\Scripts\python.exe run_studio.py
```

## 3) Playwright 브라우저 설치 (처음 1회, 브라우저 도우미 사용 시)

```bat
py -m playwright install
```

## 4) 창이 바로 꺼질 때

`bootstrap-install-error.txt` 또는 `studio-launch-error.txt` 내용을 확인하세요.
