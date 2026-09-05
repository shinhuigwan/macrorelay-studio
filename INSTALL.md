# 설치/실행 (다른 PC)

## 1) 필수 프로그램

- Python 3.11+ (권장: 3.11.9)
- (OCR 사용 시) Tesseract OCR 설치
- (브라우저 모드 사용 시) Chrome 설치
- (매크로 실행 시) AutoHotkey 설치

## 2) Python 패키지 설치 + 실행

가장 쉬운 방법: `macro_tool/run_gui.bat` 더블클릭

수동 실행:

```bat
cd C:\path\to\macro_tool
py -m pip install -r requirements.txt
py gui_pyside5.py
```

## 3) Playwright 브라우저 설치 (처음 1회, 브라우저 도우미 사용 시)

```bat
py -m playwright install
```

## 4) 창이 바로 꺼질 때

`startup_error.log` 파일이 `macro_tool` 폴더에 생성됩니다. 해당 로그 내용을 보내주면 원인(누락된 패키지/권한/경로)을 바로 잡을 수 있습니다.
