# MacroRelay Remote

MacroRelay Remote는 휴대폰에서 Studio PC의 매크로를 확인하고 실행·정지하며 상태와 알림을 받는 기능입니다. PC가 외부 중계 서버로 연결하므로 공유기 포트 포워딩이나 PC 인바운드 공개가 필요하지 않습니다.

## 인터넷에서 바로 연결

1. Studio에서 `설정 > 모바일 원격`을 엽니다.
2. `Studio 실행 중 항상 모바일 연결 유지`와 필요한 실행·정지 권한을 켠 뒤 `설정 저장 및 적용`을 누릅니다.
3. Studio가 저전력 원격 에이전트를 자동으로 시작하고 연결이 끊기면 복구합니다.
4. [GitHub Releases](https://github.com/shinhuigwan/macrorelay-studio/releases/latest)에서 최신 APK를 설치하고 실행합니다.
5. APK에 기본 HTTPS 주소가 내장되어 있으므로 주소 입력 없이 연결 화면이 열립니다. Studio에 표시된 6자리 연결 코드를 입력합니다.

PC와 휴대폰은 같은 네트워크일 필요가 없습니다. 휴대폰은 Wi-Fi 또는 모바일 데이터를 사용할 수 있고 공유기 포트 포워딩이나 Windows 인바운드 방화벽 허용도 필요하지 않습니다. 매크로가 정지되어 있어도 Studio만 실행 중이면 상태 조회와 원격 실행 요청을 받을 수 있습니다.

## 자체 서버로 변경(고급)

기본 서버 대신 직접 운영하려면 `remote/relay_server.py`를 HTTPS 도메인이 있는 서버에 배포하고 Studio의 중계 서버 주소를 그 URL로 변경합니다. 서버는 Python 표준 라이브러리만 사용하며 다음과 같이 실행할 수 있습니다.

```powershell
python remote/relay_server.py --host 127.0.0.1 --port 8765 --database ./data/relay.db
```

기본 공개 릴레이는 `remote/cloudflare/`의 Cloudflare Worker와 SQLite 기반 Durable Object로 구성됩니다. 자체 서버 운영 시에는 Caddy, Nginx 또는 Cloudflare Tunnel 같은 HTTPS 역방향 프록시 뒤에 두고 데이터베이스를 정기 백업해야 합니다.

## 보안 모델

- PC 에이전트는 무작위 기기 비밀 키로 인증합니다.
- 모바일은 10분 유효 6자리 코드로 최초 연결 후 별도 토큰을 발급받습니다.
- 기기 비밀 키와 모바일 토큰은 원문으로 서버 DB에 저장되지 않고 SHA-256 해시로 저장됩니다.
- 원격 실행과 정지는 Studio에서 각각 끌 수 있습니다.
- `allowed_macros` 목록을 설정하면 지정한 매크로만 휴대폰에 노출하고 실행할 수 있습니다.
- `remote_config.json`, `runtime/remote_relay.db`는 절대로 GitHub에 업로드하지 않습니다.

## 매크로 완료 알림

빌더의 액션 목록에서 `모바일 알림` 노드를 추가합니다. 제목, 내용, 알림 종류를 설정할 수 있고 마지막 OCR 결과를 붙일 수도 있습니다. 원격으로 실행한 매크로는 시작·완료·실패·정지 상태도 자동으로 휴대폰 활동 내역에 전달합니다.

## 프로토콜

- 에이전트: 등록, 4초 장기 폴링, 5초 상태 갱신
- 명령: `status`, `list_macros`, `run_macro`, `stop_macro`
- 이벤트: 알림, 매크로 시작, 완료, 실패, 정지
- 클라이언트: 설치형 PWA. 동일한 HTTP JSON 프로토콜을 사용해 추후 Flutter/네이티브 앱으로 교체할 수 있습니다.
