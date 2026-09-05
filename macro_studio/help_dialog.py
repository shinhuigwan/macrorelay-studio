from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class MacroHelpDialog(QtWidgets.QDialog):
    """MacroRelay Studio의 전체 기능에 대한 종합 인터랙티브 사용 가이드 및 도움말 창."""

    GUIDE_SECTIONS = [
        (
            "시작하기 & 노드 조작",
            "🚀",
            """
<h2>🚀 시작하기 & 노드 캔버스 조작법</h2>
<p>MacroRelay Studio는 블록 형태의 <b>노드(Node)</b>를 연결하여 복잡한 화면 자동화를 직관적으로 구성하는 비주얼 매크로 빌더입니다.</p>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #4ADE80; margin-top: 0;'>📌 캔버스 기본 조작</h3>
    <ul>
        <li><b>화면 이동 (팬)</b>: 캔버스 빈 곳에서 <b>우클릭 드래그</b> 또는 <b>스페이스바 + 좌클릭 드래그</b></li>
        <li><b>화면 확대 / 축소 (줌)</b>: <b>마우스 휠 스크롤</b> (또는 <code>Ctrl + 휠</code>)</li>
        <li><b>전체 보기</b>: 상단 <b>[ 전체 보기 ]</b> 버튼을 누르면 모든 노드가 화면에 쏙 들어오도록 자동 줌</li>
        <li><b>자동 정렬</b>: <b>[ 자동 정렬 ]</b> 버튼을 누르면 100px 최적 간격으로 노드가 일목요연하게 자동 정렬됩니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #38E7FF; margin-top: 0;'>🔌 노드 연결선 (성공 / 실패 흐름)</h3>
    <ul>
        <li><b>초록색 성공 포트 (Out)</b>: 이전 작업이 정상 완료되었을 때 다음으로 실행할 노드로 드래그하여 연결합니다.</li>
        <li><b>빨간색 실패 포트 (Out)</b>: 이미지 탐색 실패나 조건 불일치 시 실행할 노드로 드래그하여 연결합니다.</li>
        <li><b>연결선 끊기 (제거)</b>: 노드선을 마우스로 잡고 <b>빈 공간으로 드래그</b>하면 즉시 연결이 안전하게 해제됩니다.</li>
        <li><b>연결선 설정</b>: 노드선을 <b>더블클릭</b>하거나 <b>우클릭</b>하면 '실행 딜레이(ms)' 및 '횟수/변수 조건 분기'를 추가할 수 있습니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #FCD34D; margin-top: 0;'>📦 노드 보관함 & 관리</h3>
    <ul>
        <li><b>노드 보관 (삭제)</b>: 노드 우클릭 &gt; <code>[노드 보관]</code>을 누르면 영구 삭제 대신 안전하게 보관함에 저장됩니다.</li>
        <li><b>보관함 열기 & 복원</b>: 상단 툴바의 <b>[ 📦 보관함 ]</b> 버튼을 눌러 언제든 보관된 노드를 캔버스로 되살릴 수 있습니다.</li>
        <li><b>노드 복제</b>: 노드 우클릭 &gt; <code>[노드 복제]</code>를 누르면 설정값이 그대로 복사된 새 노드가 생성됩니다.</li>
    </ul>
</div>
            """,
        ),
        (
            "이미지 서치 & 엔진 프리셋",
            "🎯",
            """
<h2>🎯 이미지 서치 & 엔진별 프리셋 완벽 가이드</h2>
<p>화면에서 특정 버튼, 아이콘, 텍스트 이미지를 찾아내고 마우스 클릭을 수행하는 핵심 기능입니다.</p>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #38E7FF; margin-top: 0;'>⚖️ OpenCV vs AutoHotkey 엔진 비교</h3>
    <table border='1' cellpadding='8' cellspacing='0' style='border-collapse: collapse; border-color: #2E384D; width: 100%; color: #E2E8F0;'>
        <tr style='background: #1F2736;'>
            <th style='width: 25%;'>구분</th>
            <th style='width: 37.5%; color: #4ADE80;'>OpenCV 엔진 (권장)</th>
            <th style='width: 37.5%; color: #4D9FFF;'>AutoHotkey 엔진</th>
        </tr>
        <tr>
            <td><b>주요 강점</b></td>
            <td>70~150% 자동 배율 보정, 고해상도 지원, 정밀한 형태 판별</td>
            <td>극도로 가벼운 리소스, 즉각적인 반응속도(Zero Latency)</td>
        </tr>
        <tr>
            <td><b>추천 상황</b></td>
            <td>창 크기나 해상도가 달라질 수 있는 PC 게임, 웹 브라우저, UI</td>
            <td>위치와 크기가 고정된 앱플레이어(LDPlayer 등), 단순 아이콘</td>
        </tr>
        <tr>
            <td><b>엔진 전환</b></td>
            <td colspan='2'>노드 상세설정에서 엔진을 바꾸면 기존 설정이 새 엔진 규격에 맞게 <b>자동 변환</b> 및 <b>상호 복구</b>됩니다.</td>
        </tr>
    </table>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #C084FC; margin-top: 0;'>⚡ 엔진 프리셋 (속도 vs 정밀도)</h3>
    <ul>
        <li><b>⚡ 속도 우선 프리셋</b>: 탐색 주기를 35ms로 단축하고 불필요한 필터를 건너뛰어 초고속 연타 및 즉시 반응에 최적화됩니다.</li>
        <li><b>◐ 기본 프리셋</b>: 일치율 84%, 허용오차 16 등 대부분의 환경에서 안정적으로 동작하는 표준 설정입니다.</li>
        <li><b>🎯 정밀도 우선 프리셋</b>: 신뢰도 92%+, 허용오차 8 이하로 엄격하게 설정하여 배경 색상 변화나 유사 아이콘 오탐을 원천 차단합니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #F87171; margin-top: 0;'>🎯 검색 실패 시 대체 클릭 (오프셋 클릭)</h3>
    <p>화면에서 대상을 못 찾았을 때 멈추지 않고, <b>창 닫기 버튼(X)이나 빈 화면을 대신 클릭</b>하도록 설정할 수 있습니다.</p>
    <ul>
        <li><b>설정 방법</b>: 노드 상세설정 &gt; <code>[검색 실패 시 동작]</code> &gt; <code>[못 찾으면 실패 클릭 실행]</code> 체크</li>
        <li><b>좌표 지정</b>: <code>[화면 클릭으로 실패 위치 지정]</code> 버튼을 눌러 모니터에서 원하는 위치를 콕 찍으면 끝!</li>
        <li><b>듀얼 클릭 보장</b>: 윈도우 <code>WindowFromPoint</code> API와 <code>PostMessage + ControlClick</code> 듀얼 전송으로 비활성 상태에서도 100% 클릭이 입력됩니다.</li>
    </ul>
</div>
            """,
        ),
        (
            "순차 분기 & 스마트 녹화",
            "🔀",
            """
<h2>🔀 순차 분기 & 스마트 녹화 (Zero-Wire) 가이드</h2>
<p>여러 상황이나 작업(1번 작업 실패 시 2번 작업, 2번 실패 시 3번 작업...)을 자동으로 연속 시도하는 분기 시스템입니다.</p>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #FB923C; margin-top: 0;'>🔀 순차 분기 묶기 (스마트 분기)</h3>
    <ul>
        <li><b>개념</b>: 1번 분기 실행 ➔ 1번 실패 시 2번 분기 자동 실행 ➔ 2번 실패 시 3번 분기 실행</li>
        <li><b>묶는 방법</b>: 캔버스에서 분기로 만들 노드들을 <b>드래그 선택</b> 후 상단 <b>[ 🔀 순차 분기 묶기 ]</b> 버튼 클릭!</li>
        <li><b>선 없는 자동 분기 (Zero-Wire)</b>: 분기 레인으로 묶으면 분기끼리는 <b>복잡한 실패 빨간선을 잇지 않아도</b> 시스템이 컴파일 시점에 자동으로 다음 분기 1번 노드로 전환합니다!</li>
        <li><b>캔버스 시각화</b>: 각 분기는 스마트 녹화 스타일의 깔끔한 색상 영역 테두리(Workflow Lane)로 감싸져 한눈에 구분됩니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #38E7FF; margin-top: 0;'>🎥 스마트 녹화 (F7 다음 작업 분기)</h3>
    <ul>
        <li>상단 <b>[ 스마트 녹화 ]</b> 버튼을 누르면 미니 플로팅 녹화 바가 바탕화면에 나타납니다.</li>
        <li>마우스 클릭, 타이핑, 대기 시간이 실시간으로 감지되어 노드로 자동 변환됩니다.</li>
        <li><b>💡 F7 단축키</b>: 녹화 도중 <code>F7</code> 키를 누르면 현재 작업을 마무리하고 <b>'다음 작업 분기'</b>로 분리되어 녹화됩니다!</li>
    </ul>
</div>
            """,
        ),
        (
            "비활성 클릭 & 백그라운드",
            "🖱️",
            """
<h2>🖱️ 비활성 클릭 & 백그라운드 매크로 가이드</h2>
<p>창이 다른 창에 가려져 있거나 최소화되어 있어도, 실제 마우스 커서를 움직이지 않고 대상 프로그램에만 클릭 신호를 전송합니다.</p>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #4ADE80; margin-top: 0;'>💡 비활성 클릭의 작동 원리</h3>
    <ul>
        <li>일반 마우스 클릭(활성 클릭)은 마우스 커서가 직접 대상 위치로 이동하므로 매크로 동작 중 PC를 사용할 수 없습니다.</li>
        <li><b>비활성 클릭(Inactive Click)</b>은 윈도우 메시지(<code>WM_LBUTTONDOWN / UP</code>)를 창 내부 핸들(HWND)로 직접 전달하여, <b>화면을 보면서 다른 작업을 동시에 진행</b>할 수 있습니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #FCD34D; margin-top: 0;'>🛠️ 성공률을 높이는 설정 요령</h3>
    <ul>
        <li><b>대상 창 자동 선택</b>: 노드 상세설정에서 <code>[대상 창 선택]</code> 버튼을 눌러 대상 프로그램을 클릭하면 창 제목과 프로세스명(exe)이 자동 기입됩니다.</li>
        <li><b>클릭 방식 (Method)</b>: <code>자동 (Auto)</code>으로 두시면 시스템이 <code>PostMessage</code>와 <code>ControlClick</code>을 동시에 조합하여 LD플레이어, 게임, 웹 브라우저 가리지 않고 안정적으로 전송합니다.</li>
        <li><b>관리자 권한</b>: 게임이나 일부 보안 프로그램은 Studio를 <b>관리자 권한으로 실행</b>해야 비활성 신호를 정상 수신합니다.</li>
    </ul>
</div>
            """,
        ),
        (
            "픽셀 색상 & OCR 인식",
            "🔍",
            """
<h2>🔍 픽셀 색상 서치 & OCR 텍스트 인식</h2>
<p>이미지 외에도 화면의 고유 색상(RGB)이나 글자/숫자를 읽어 스마트하게 판단합니다.</p>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #FF6B9D; margin-top: 0;'>🎨 픽셀 색상 서치 (Pixel Search)</h3>
    <ul>
        <li>화면의 특정 픽셀 또는 지정 범위 안에서 원하는 색상(#RRGGBB)이 나타나는지 초고속으로 검사합니다.</li>
        <li><b>스포이트 기능</b>: 화면에서 원하는 지점을 클릭하면 해당 픽셀의 HEX 색상 코드가 자동 복사됩니다.</li>
        <li><b>허용 오차 (Tolerance)</b>: 그라데이션이나 조명 변화가 있는 경우 허용 오차를 10~25 정도로 설정하세요.</li>
        <li><b>발견 시 동작</b>: 발견 위치 즉시 클릭, 좌표를 변수에 저장, 또는 성공/실패 조건 분기로 활용 가능합니다.</li>
    </ul>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #4D9FFF; margin-top: 0;'>📝 OCR 텍스트 인식 & OCR 추적</h3>
    <ul>
        <li><b>텍스트 추출</b>: 지정 영역 안의 한글, 영문, 숫자를 읽어 변수에 저장합니다. (예: 골드 수량, 대화창 텍스트)</li>
        <li><b>단어 찾기 & 클릭</b>: 화면에 특정 단어가 나타나면 그 글자가 위치한 곳을 직접 클릭합니다.</li>
        <li><b>OCR 추적</b>: '골드 아이콘' 이미지를 먼저 화면에서 찾은 후, 그 바로 옆(오프셋) 영역의 숫자만 정밀하게 읽어냅니다.</li>
    </ul>
</div>
            """,
        ),
        (
            "단축키 & 꿀팁 FAQ",
            "⌨️",
            """
<h2>⌨️ 단축키 & 실전 꿀팁 FAQ</h2>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px; margin-bottom: 14px;'>
    <h3 style='color: #38E7FF; margin-top: 0;'>⚡ 필수 단축키 일람</h3>
    <table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; border-color: #2E384D; width: 100%; color: #E2E8F0;'>
        <tr style='background: #1F2736;'>
            <th style='width: 30%;'>단축키</th>
            <th style='width: 70%;'>기능 설명</th>
        </tr>
        <tr><td><b>Ctrl + R</b></td><td>매크로 전체 실행 시작</td></tr>
        <tr><td><b>F12</b></td><td>실행 중인 모든 매크로 및 서치 프로세스 <b>즉시 강제 중지</b></td></tr>
        <tr><td><b>Ctrl + Shift + T</b></td><td>선택한 노드 1개만 단독 <b>단계별 테스트</b> 실행</td></tr>
        <tr><td><b>F1</b></td><td>지금 보고 계신 <b>도움말 & 가이드</b> 열기</td></tr>
        <tr><td><b>Ctrl + N</b></td><td>새 매크로 생성</td></tr>
        <tr><td><b>Ctrl + Z</b></td><td>삭제/보관한 노드나 매크로 즉시 실행 취소 복구</td></tr>
        <tr><td><b>F7</b></td><td>스마트 녹화 도중 새 분기(다음 작업)로 분리 생성</td></tr>
    </table>
</div>

<div style='background: #171D28; border: 1px solid #2B384E; border-radius: 8px; padding: 14px;'>
    <h3 style='color: #4ADE80; margin-top: 0;'>❓ 자주 묻는 질문 (FAQ)</h3>
    <p><b>Q. 노드가 실패했는데 왜 실패했는지 원인을 어떻게 아나요?</b><br>
    ➔ 상단 <b>[ 📋 성공·실패 로그 ]</b> 버튼을 누르시면, 실패 원인(창 가려짐, 신뢰도 미달 등)과 구체적인 해결 방법이 친절한 한글로 기록되어 있습니다.</p>

    <p><b>Q. 노드 간격이 너무 붙어있거나 멀어요.</b><br>
    ➔ 상단 <b>[ 자동 정렬 ]</b> 버튼을 누르면 100px 최적 간격으로 노드가 겹치지 않고 보기 좋게 정돈됩니다.</p>

    <p><b>Q. 중간 노드부터 테스트하고 싶어요.</b><br>
    ➔ 원하는 노드를 클릭하고 상단 <b>[ ▶ 선택 노드부터 실행 ]</b>을 누르면 앞 단계는 건너뛰고 해당 노드부터 끝까지 실행됩니다.</p>
</div>
            """,
        ),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("사용 가이드 & 전체 도움말 · MacroRelay Studio")
        self.resize(1000, 720)
        self.setStyleSheet("""
            QDialog {
                background: #0F131C;
                color: #E2E8F0;
            }
            QListWidget {
                background: #151A26;
                border: 1px solid #232A3B;
                border-radius: 8px;
                padding: 6px;
                color: #CBD5E1;
                font-size: 10pt;
            }
            QListWidget::item {
                padding: 10px 14px;
                border-radius: 6px;
                margin-bottom: 4px;
            }
            QListWidget::item:hover {
                background: #1E2536;
                color: #FFFFFF;
            }
            QListWidget::item:selected {
                background: #2563EB;
                color: #FFFFFF;
                font-weight: 700;
            }
            QTextBrowser {
                background: #121722;
                border: 1px solid #232A3B;
                border-radius: 8px;
                padding: 18px;
                color: #E2E8F0;
                font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;
                font-size: 10pt;
                line-height: 1.65;
            }
            QLineEdit {
                background: #151A26;
                border: 1px solid #2B384E;
                border-radius: 6px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-size: 9.5pt;
            }
            QLineEdit:focus {
                border-color: #38E7FF;
            }
            QPushButton {
                background: #232A3B;
                color: #FFFFFF;
                border: 1px solid #333F56;
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2F3950;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        header_layout = QtWidgets.QHBoxLayout()
        icon_label = QtWidgets.QLabel("📖")
        icon_label.setStyleSheet("font-size: 20pt;")
        title_box = QtWidgets.QVBoxLayout()
        main_title = QtWidgets.QLabel("MacroRelay Studio 완벽 가이드 & 도움말")
        main_title.setStyleSheet("font-size: 14pt; font-weight: 800; color: #FFFFFF;")
        sub_title = QtWidgets.QLabel("노드 조작, 이미지 서치, 분기, 비활성 클릭, 단축키 등 모든 기능의 상세 설명서입니다.")
        sub_title.setStyleSheet("color: #8A98B0; font-size: 9pt;")
        title_box.addWidget(main_title)
        title_box.addWidget(sub_title)
        header_layout.addWidget(icon_label)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 도움말 검색 (예: 분기, 프리셋, 오프셋, 비활성 클릭, F7, 단축키...)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(340)
        self.search_input.textChanged.connect(self._on_search)
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)

        body_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        body_splitter.setHandleWidth(8)

        self.cat_list = QtWidgets.QListWidget()
        self.cat_list.setFixedWidth(240)
        for title, emoji, _html in self.GUIDE_SECTIONS:
            item = QtWidgets.QListWidgetItem(f"{emoji}  {title}")
            self.cat_list.addItem(item)
        self.cat_list.currentRowChanged.connect(self._on_category_changed)

        self.content_browser = QtWidgets.QTextBrowser()
        self.content_browser.setOpenExternalLinks(True)

        body_splitter.addWidget(self.cat_list)
        body_splitter.addWidget(self.content_browser)
        body_splitter.setStretchFactor(1, 1)

        layout.addWidget(body_splitter, 1)

        footer_layout = QtWidgets.QHBoxLayout()
        tip_footer = QtWidgets.QLabel("💡 언제든 <b>F1</b> 키를 누르면 이 도움말 창을 다시 열 수 있습니다.")
        tip_footer.setStyleSheet("color: #4ADE80; font-size: 9pt;")
        close_btn = QtWidgets.QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        footer_layout.addWidget(tip_footer)
        footer_layout.addStretch(1)
        footer_layout.addWidget(close_btn)

        layout.addLayout(footer_layout)

        self.cat_list.setCurrentRow(0)

    def _on_category_changed(self, row: int) -> None:
        if 0 <= row < len(self.GUIDE_SECTIONS):
            _title, _emoji, html_content = self.GUIDE_SECTIONS[row]
            self.content_browser.setHtml(html_content)

    def _on_search(self, query: str) -> None:
        query = query.strip().lower()
        if not query:
            self._on_category_changed(self.cat_list.currentRow())
            return

        results_html: list[str] = [f"<h2>🔍 '{query}' 검색 결과</h2>"]
        matches_found = 0
        for title, emoji, content in self.GUIDE_SECTIONS:
            if query in title.lower() or query in content.lower():
                matches_found += 1
                results_html.append(f"<div style='border: 1px solid #38E7FF; border-radius: 8px; padding: 14px; margin-bottom: 16px; background: #161D2B;'>")
                results_html.append(f"<h3 style='color: #38E7FF; margin-top: 0;'>{emoji} {title}</h3>")
                results_html.append(content)
                results_html.append("</div>")

        if matches_found == 0:
            results_html.append(f"<p style='color: #8A98B0; padding: 20px;'>검색어 '<b>{query}</b>'와 일치하는 도움말 항목이 없습니다.<br>다른 키워드(예: <b>분기, 이미지, 클릭, 단축키, 오프셋</b>)로 검색해보세요.</p>")

        self.content_browser.setHtml("\n".join(results_html))
