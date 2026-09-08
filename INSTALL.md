# Windows 설치 및 실행

## 가장 쉬운 설치

1. GitHub Releases에서 `MacroRelay-Studio-v*.zip`을 내려받아 압축을 풉니다.
2. `setup_windows.bat`을 더블클릭합니다.
3. 설치가 끝나면 Studio가 자동으로 실행됩니다.

설치 프로그램은 다음 작업을 자동으로 처리합니다.

- Python 3.11/3.12 검색 및 없을 경우 Windows Package Manager로 Python 3.12 설치
- 프로젝트 전용 `.venv` 생성 또는 손상된 가상환경 복구
- PySide6, OpenCV, MSS, NumPy 등 멀티 이미지 서치 필수 패키지 설치
- AutoHotkey v1.1 공식 설치 파일의 SHA-256 검증 후 자동 설치·연결
- Playwright Chromium 설치
- 설치 결과 모듈 검사

OCR 부가 엔진은 사용하는 기능과 용량이 다르므로 Studio의 `설정 > 구성요소 설치`에서 추가할 수 있습니다. Studio 편집, 이미지 검색과 기본 매크로 실행은 위 원클릭 설치로 구성됩니다.

## 이미 설치한 이후

`run_studio.bat`을 더블클릭하면 기존 환경을 재설치하지 않고 바로 실행합니다.

## 수동 설치

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## 문제 해결

- 설치 중 오류: 열린 창의 마지막 오류 문장을 확인합니다.
- 실행 창이 열리지 않음: `studio-launch-error.txt`를 확인합니다.
- OpenCV/OCR/AutoHotkey: Studio의 `설정 > 구성요소 설치`에서 상태를 확인합니다.
