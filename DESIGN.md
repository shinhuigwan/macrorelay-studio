# AutoHotkey 매크로 빌더 설계

## 목표

- 반복적인 클릭, 이미지 검색, 타이핑, 프로그램 실행/종료 등의 단계들을 순차적으로 기록/편집
- 각 단계는 JSON으로 저장되고, Ulando 스트림덱에서 실행할 AutoHotkey(.ahk) 파일로 변환
- 자주 쓰이는 이미지 검색용 이미지를 `assets/`에 보관하고 도구에서 관리

## 기본 데이터 구조

### macro.json

```jsonc
{
  "name": "macro-name",
  "description": "필요시 설명",
  "meta": {
    "coord_mode": "Screen" // 기본 좌표 기준
  },
  "steps": [
    {
      "action": "mouse_click",
      "button": "Left",
      "x": 200,
      "y": 300,
      "count": 1,
      "sleep_after": 300
    },
    {
      "action": "image_search",
      "asset": "target-button",
      "confidence": 90,
      "timeout": 2000,
      "click": {
        "type": "relative",
        "offset": [0, 5],
        "button": "Left"
      }
    }
  ]
}
```

### asset metadata

- `assets/`에 실제 PNG 파일 저장
- `assets/index.json`에는 별칭, 원본 경로, 크기 등의 참고 정보

## 지원 액션 후보

1. `mouse_click` – 화면 좌표로 클릭, 클릭 횟수/지연 포함
2. `inactive_click` – `ControlClick`으로 마우스를 사용하지 않는 클릭
3. `image_search` – 저장된 이미지로 좌표 추적, 결과에 따라 클릭/정지/다음으로
4. `type_text` – 텍스트 입력 또는 `SendInput`과 지연
5. `wait` – `Sleep`
6. `run_program` / `terminate_program` – `Run` 또는 `Process, Close`
7. `toggle_click_lock` – 클릭 감지/허용 상태 토글 (추후 확장)

## .ahk 생성 전략

- 스크립트 헤더에서 공통 설정 (`SendMode`, `CoordMode`, 이미지 경로)
- `ImageSearch` 결과를 `if ErrorLevel`로 검사하여 실패 시 로그
- 각 스텝은 주석으로 이름/파라미터 기록
- Ulando 스트림덱은 해당 `.ahk`를 실행하도록 버튼 명령 설정
