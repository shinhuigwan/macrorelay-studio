# MacroRelay Studio

MacroRelay Studio는 반복적인 클릭, 이미지 검색, 타이핑, 프로그램 실행/종료 같은 단계를 노드 플로우로 설계하고 AutoHotkey 스크립트로 출력합니다. 연결선은 빈 공간으로 드래그해 제거할 수 있고, 캔버스 바닥은 더블클릭 유지로 이동합니다. Quick Slots는 MacroRelay Runner가 백그라운드에서 전역 단축키로 실행합니다.

## 2.34.1 AI 시작 화면 캡처 안정화

- AI 시작 화면은 드래그를 놓는 즉시 캡처를 완료해 정지 화면에 갇히는 현상을 방지합니다.
- 캡처 창이 닫히거나 오류가 나도 Studio와 실행 조건 창을 항상 복원합니다.
- 시작 화면 캡처 직후 `누끼·상세 편집`에서 자동 누끼, 투명화 붓, 색상 제거와 자르기를 사용할 수 있습니다.

## 2.34 AI 실행 조건 단순화

- AI 녹화가 끝난 뒤 `내가 직접 실행` 또는 `특정 화면이 나타나면 자동 실행`만 선택합니다.
- 시작 화면은 기존 다중 모니터 PNG 캡처와 이미지 자산 시스템으로 저장하고 대상 프로그램 클라이언트 영역에서 확인합니다.
- 자동 실행은 화면이 `없음 → 있음`으로 바뀐 뒤 500ms 동안 안정적으로 유지될 때 한 번만 실행합니다.
- 시작 화면이 계속 보이면 반복하지 않으며, 사라진 뒤 다시 나타날 때 재실행됩니다.
- 실행 조건과 기본 실패 정책은 AI 분석 패키지와 JSON 가져오기 흐름에 보존됩니다.
- 설정 화면에서는 Trigger, Polling, Edge, Cooldown 같은 내부 용어를 표시하지 않습니다.

## 2.33 AI 자동 매크로 제작

OpenAI API나 API 키를 연결하지 않고도 한 번의 일반 조작 녹화에서 ChatGPT 분석 패키지를 만들고, ChatGPT가 작성한 MacroRelay JSON을 안전한 노드 초안으로 가져올 수 있습니다.

1. 매크로 빌더에서 `AI 매크로 녹화`를 누르고 평소처럼 작업한 뒤 `F10`으로 종료합니다.
2. `exports/ai-recordings/`에 생성된 ZIP과 `ChatGPT용 프롬프트 복사`의 내용을 ChatGPT에 첨부합니다.
3. ChatGPT는 즉시 JSON을 만들지 않고 미확정 질문을 한 번에 먼저 묻습니다. 답변 후 내려받은 JSON을 `AI JSON 불러오기`로 엽니다.
4. 통합 설정 마법사에서 실패한 이미지, 대상 프로그램, 비활성 클릭, OCR, 변수·보안 값만 확인하고 `설정 완료`를 누릅니다.

분석 ZIP에는 동작 영상, 클릭·더블클릭·드래그·휠·단축키 타임라인, 대상 프로그램/DPI/클라이언트 좌표, 클릭 전후 무손실 PNG, 작은·버튼·넓은·흑백·윤곽·배경 제거 후보, 접촉 시트와 엄격한 `macrorelay-ai-1.0` 스키마가 포함됩니다. 영상 프레임은 이미지 서치 자산으로 사용하지 않습니다.

문자 입력은 녹화 시점부터 `[REDACTED]`로 저장되고 입력 구간 영상과 PNG의 입력 컨트롤은 가려집니다. 민감 값은 `설정 > 보안 보관함`의 DPAPI 항목 이름으로만 참조합니다. AI JSON은 허용 액션만 가져오며 Python·AHK·PowerShell·셸 코드는 거부합니다. 모든 가져오기는 새 `AI 초안`으로 저장되고, 주황색 설정 필요 노드는 정식 실행·Quick Slots·원격 실행이 차단됩니다. 드라이런과 단일 단계 테스트만 허용되며 `미완성 설정 계속하기`에서 언제든 이어서 설정할 수 있습니다.

## 2.32 안정성·자동 실행 기능

- 노드별 재시도와 간격, 실패선 전환, 연속 실패 자동 정지, 체크포인트 자동 재개
- 서브플로우 입력·출력 변수와 성공 결과 변수
- 실제 클릭·입력 없이 경로와 예상 동작을 확인하는 드라이런
- 화면 캡처 재사용, 템플릿 사전 로딩, 이전 탐지 위치 우선 검색을 포함한 이미지 검색 캐시
- 프로그램 시작·종료, 창/이미지 출현, OCR 숫자 조건, 시간·요일 기반 자동 실행
- 이미지/OCR/변수 입력과 기대 경로·클릭 횟수를 저장하는 매크로 회귀 테스트
- Windows 사용자 DPAPI로 암호화되는 보안 보관함과 실행 중 변수 불러오기 노드
- 노드 평균·최대 시간, 실패율, 이미지 검색 시간, OCR 성공률, 캡처 재사용, CPU·메모리 통계

이벤트 조건과 테스트 케이스는 매크로 빌더의 `더보기` 메뉴에서 설정합니다. 보안 값은 `설정 > 보안 보관함`에 저장한 뒤 `보안 보관함 값 불러오기` 노드에서 이름만 참조합니다. 포터블 내보내기에는 참조된 값의 DPAPI 암호문만 포함되며 평문은 포함되지 않습니다. 기존 모바일 연결 키도 첫 실행 때 DPAPI 보관함으로 자동 이전되어 `remote_config.json`에서 제거됩니다.

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
