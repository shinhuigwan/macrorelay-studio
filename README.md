# MacroRelay Studio

MacroRelay Studio는 반복적인 클릭, 이미지 검색, 타이핑, 프로그램 실행/종료 같은 단계를 노드 플로우로 설계하고 AutoHotkey 스크립트로 출력합니다. 연결선은 빈 공간으로 드래그해 제거할 수 있고, 캔버스 바닥은 더블클릭 유지로 이동합니다. Quick Slots는 MacroRelay Runner가 백그라운드에서 전역 단축키로 실행합니다.

## 구조

- `macro_tool.py` : CLI를 통해 매크로를 만들고, 단계(step)를 추가하며, `.ahk`로 내보냅니다.
- `macros/` : 각 매크로 정의(JSON 파일)가 들어 있는 폴더.
- `assets/` : 이미지 검색에 쓸 PNG/JPG 파일을 저장하고 `index.json`에서 별칭(alias)으로 관리합니다.
- `exports/` : `export` 명령으로 만들어진 `.ahk` 파일이 위치합니다. 이 경로를 울란도 스트림덱 버튼에서 호출하면 됩니다.

## 기본 사용 흐름

### Studio 실행

`run_studio.bat`을 실행합니다. Studio에서 매크로 생성, 단계 추가, 이미지 캡처·편집, Quick Slots와 기능별 단축키 설정, `.ahk` 내보내기를 진행할 수 있습니다.

### CLI

1. 매크로 만들기
```bash
python macro_tool/macro_tool.py new "인벤토리 정리" --description "인벤 정리용 클릭 순서"
```

2. 단계 추가
   - 단순 클릭:
   ```bash
   python macro_tool/macro_tool.py add-step "인벤토리 정리" \
       --action mouse_click --param x=120 --param y=340 --param button=Left --param sleep_after=300
   ```
   - 이미지 검색 + 클릭:
   ```bash
   python macro_tool/macro_tool.py add-step "인벤토리 정리" \
       --action image_search \
       --json '{"asset":"purchase-button","click":{"offset":[0,5],"button":"Left","count":1},"timeout":4000}'
   ```

3. 이미지 에셋 관리
   - 등록: `macro_tool/assets` 하위로 복사하고 alias(예: `purchase-button`)를 기록합니다.
   ```bash
   python macro_tool/macro_tool.py asset add C:/Users/shin/Downloads/buy.png --alias purchase-button
   ```
   - 목록/삭제: `asset list`, `asset remove purchase-button`

4. `.ahk`로 내보내기
```bash
python macro_tool/macro_tool.py export "인벤토리 정리"
```
`exports/인벤토리-정리.ahk` 같은 파일이 생성됩니다.

## Ulando 스트림덱에 연결하기

1. `AutoHotkey.exe`를 설치한 위치를 확인합니다. 기본적으로 `"C:\Program Files\AutoHotkey\AutoHotkey.exe"`입니다.
2. 스트림덱의 버튼 액션에 다음과 같은 명령을 넣습니다.
   ```
   "C:\Program Files\AutoHotkey\AutoHotkey.exe" "<MacroRelay 경로>\exports\인벤토리-정리.ahk"
   ```
3. 버튼이 눌리면 해당 `.ahk`에서 정의한 단계가 순차적으로 실행되고, 필요한 경우 이미지가 `assets/` 폴더에서 참조됩니다.

## 주요 명령 요약

- `macro_tool.py list` : 저장된 매크로 이름과 설명 확인
- `macro_tool.py describe <name>` : JSON 출력
- `macro_tool.py add-step` : `--action`과 `--param`/`--json`으로 순서를 정의
- `macro_tool.py export <name>` : `.ahk` 파일 생성 (`exports/`에 저장)
- `macro_tool.py asset add/remove/list` : 이미지 검색용 자산 관리

## 이미지 검색 단계 예시

JSON에서 `click` 매개변수는 `{"offset":[dx,dy],"button":"Left","count":1}` 형태로, 검색 결과 좌표를 기준으로 오프셋을 덧셈합니다. `timeout`을 주면 지정한 밀리초까지만 검색하고 없으면 종료합니다. `abort_on_fail`을 `false`로 바꾸면 실패해도 다음 단계로 이어집니다.

## 모바일 원격 제어

Studio의 `설정 > 모바일 원격`에서 원격 제어를 켜면 Studio가 실행되는 동안 저전력 PC 에이전트가 인터넷 중계 서버와 연결을 유지합니다. 매크로가 실행 중이지 않아도 다른 Wi-Fi나 모바일 데이터의 휴대폰에서 연결할 수 있으며, 6자리 코드로 연결하면 매크로 조회·실행·정지 및 완료 알림을 사용할 수 있습니다. 공유기 설정이나 포트 포워딩은 필요하지 않습니다.

Android에서는 [최신 릴리스](https://github.com/shinhuigwan/macrorelay-studio/releases/latest)의 `MacroRelay-Remote-*.apk`를 내려받아 설치할 수 있습니다. 최신 APK에는 고정 HTTPS 주소가 포함되어 있어 주소 입력 없이 바로 열립니다. APK 소스는 `android/`에 있으며, 로컬에서는 `android/gradlew.bat assembleDebug`로 다시 빌드할 수 있습니다.

인터넷 외부 접속, 권한 모델, 설치형 PWA와 서버 운영 방법은 [REMOTE.md](REMOTE.md)를 참고하세요. `remote_config.json`에는 기기 비밀 키가 있으므로 Git에 포함하지 마세요.
