from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop, virtual_desktop_geometry
from .repository import MacroRepository
from .widgets import WheelSafeSpinBox


KOREAN_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def korean_initial_text(value: str) -> str:
    result: list[str] = []
    for character in value.casefold():
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            result.append(KOREAN_INITIALS[(code - 0xAC00) // 588])
        else:
            result.append(character)
    return "".join(result)


def korean_contains(query: str, value: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return True
    haystack = value.casefold()
    return needle in haystack or korean_initial_text(needle) in korean_initial_text(haystack)


class KoreanContainsProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.query = ""

    def set_query(self, query: str) -> None:
        self.query = query
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QtCore.QModelIndex) -> bool:
        index = self.sourceModel().index(source_row, 0, source_parent)
        return korean_contains(self.query, str(self.sourceModel().data(index) or ""))


class SearchableAssetCombo(QtWidgets.QComboBox):
    """Editable asset picker with contains and Korean-initial completion."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.setIconSize(QtCore.QSize(46, 32))
        self.view().setIconSize(QtCore.QSize(46, 32))
        self.view().setUniformItemSizes(True)
        self.proxy = KoreanContainsProxyModel(self)
        self.proxy.setSourceModel(self.model())
        self.asset_completer = QtWidgets.QCompleter(self.proxy, self)
        self.asset_completer.setCompletionMode(QtWidgets.QCompleter.UnfilteredPopupCompletion)
        self.asset_completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.setCompleter(self.asset_completer)
        self.lineEdit().setPlaceholderText("이름 또는 초성으로 검색")
        self.lineEdit().textEdited.connect(self._search)
        self.asset_completer.activated[str].connect(self._select_completion)

    def _search(self, text: str) -> None:
        self.proxy.set_query(text)
        if text:
            self.asset_completer.complete()

    def _select_completion(self, text: str) -> None:
        index = self.findText(text, QtCore.Qt.MatchFixedString)
        if index >= 0:
            self.setCurrentIndex(index)

    def selected_value(self) -> str:
        index = self.findText(self.currentText(), QtCore.Qt.MatchFixedString)
        if index >= 0:
            # '선택 안 함' 항목의 data는 빈 문자열입니다. 표시 문구를 자산
            # 이름으로 되돌리면 실제로 존재하지 않는 이미지가 저장됩니다.
            data = self.itemData(index)
            return "" if data is None else str(data)
        return self.currentText().strip()

    def set_asset_options(self, values: list[str], preview_paths: dict[str, Path | None]) -> None:
        previous = self.selected_value()
        self.clear()
        self.addItem("선택 안 함", "")
        for value in values:
            path = preview_paths.get(value)
            icon = QtGui.QIcon()
            if path is not None and Path(path).is_file():
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    icon = QtGui.QIcon(
                        pixmap.scaled(92, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                    )
            self.addItem(icon, value, value)
            index = self.count() - 1
            self.setItemData(index, value, QtCore.Qt.ToolTipRole)
            self.setItemData(index, QtCore.QSize(0, 40), QtCore.Qt.SizeHintRole)
        index = self.findData(previous)
        if index < 0:
            index = self.findText(previous)
        self.setCurrentIndex(max(index, 0))


class MultiAssetPicker(QtWidgets.QWidget):
    """Compact searchable checklist used by one-node multi image search."""

    offset_edited = QtCore.Signal()
    selection_changed = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("여러 이미지 검색 · 이름 또는 초성")
        self.search.textChanged.connect(self._filter)
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumHeight(145)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.list.setIconSize(QtCore.QSize(54, 38))
        self.list.setUniformItemSizes(True)
        self.list.itemChanged.connect(lambda _item: self._update_count())
        controls = QtWidgets.QHBoxLayout()
        select_visible = QtWidgets.QPushButton("검색 결과 모두 선택")
        clear = QtWidgets.QPushButton("선택 해제")
        select_visible.clicked.connect(self._select_visible)
        clear.clicked.connect(lambda: self.set_value([]))
        self.count_label = QtWidgets.QLabel("0개 선택")
        self.count_label.setObjectName("Muted")
        controls.addWidget(select_visible)
        controls.addWidget(clear)
        controls.addStretch(1)
        controls.addWidget(self.count_label)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addLayout(controls)
        self.preview_scroll = QtWidgets.QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.preview_scroll.setFixedHeight(112)
        self.preview_body = QtWidgets.QWidget()
        self.preview_layout = QtWidgets.QHBoxLayout(self.preview_body)
        self.preview_layout.setContentsMargins(4, 4, 4, 4)
        self.preview_layout.setSpacing(7)
        self.preview_layout.addStretch(1)
        self.preview_scroll.setWidget(self.preview_body)
        self.preview_scroll.setVisible(False)
        layout.addWidget(self.preview_scroll)
        self._preview_paths: dict[str, Path] = {}
        self._offsets: dict[str, list[int]] = {}
        self.preview_aliases: list[str] = []

    def set_options(self, values: list[str], preview_paths: dict[str, Path | None] | None = None) -> None:
        selected = set(self.value())
        self._preview_paths = {
            str(alias): Path(path)
            for alias, path in (preview_paths or {}).items()
            if path is not None and Path(path).is_file()
        }
        self.list.clear()
        for value in values:
            item = QtWidgets.QListWidgetItem(str(value))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if value in selected else QtCore.Qt.Unchecked)
            item.setSizeHint(QtCore.QSize(0, 44))
            path = self._preview_paths.get(str(value))
            if path is not None:
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    item.setIcon(
                        QtGui.QIcon(
                            pixmap.scaled(108, 76, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                        )
                    )
            item.setToolTip(str(value))
            self.list.addItem(item)
        self._update_count()

    def set_value(self, values: Any) -> None:
        selected = {str(value) for value in values if str(value).strip()} if isinstance(values, list) else set()
        known = {self.list.item(index).text() for index in range(self.list.count())}
        for missing in sorted(selected - known):
            item = QtWidgets.QListWidgetItem(missing)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            self.list.addItem(item)
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setCheckState(QtCore.Qt.Checked if item.text() in selected else QtCore.Qt.Unchecked)
        self._update_count()

    def value(self) -> list[str]:
        return [
            self.list.item(index).text()
            for index in range(self.list.count())
            if self.list.item(index).checkState() == QtCore.Qt.Checked
        ]

    def set_offsets(self, values: Any) -> None:
        self._offsets = {}
        if isinstance(values, dict):
            for alias, offset in values.items():
                if isinstance(offset, (list, tuple)) and len(offset) >= 2:
                    self._offsets[str(alias)] = [int(offset[0] or 0), int(offset[1] or 0)]
        self._refresh_previews()

    def offsets(self) -> dict[str, list[int]]:
        return {alias: list(self._offsets.get(alias, [0, 0])) for alias in self.value()}

    def _set_offset_axis(self, alias: str, axis: int, value: int) -> None:
        current = list(self._offsets.get(alias, [0, 0]))
        current[axis] = int(value)
        self._offsets[alias] = current
        self.offset_edited.emit()

    def _filter(self, query: str) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(not korean_contains(query, item.text()))

    def _select_visible(self) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if not item.isHidden():
                item.setCheckState(QtCore.Qt.Checked)
        self._update_count()

    def _update_count(self) -> None:
        self.count_label.setText(f"{len(self.value())}개 선택")
        self._refresh_previews()
        self.selection_changed.emit()

    def _refresh_previews(self) -> None:
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.preview_aliases = []
        for alias in self.value():
            path = self._preview_paths.get(alias)
            if path is None:
                continue
            pixmap = QtGui.QPixmap(str(path))
            if pixmap.isNull():
                continue
            card = QtWidgets.QWidget()
            card.setFixedWidth(150)
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(2, 2, 2, 2)
            card_layout.setSpacing(2)
            image = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
            image.setFixedSize(144, 68)
            image.setPixmap(pixmap.scaled(image.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            shown_name = QtGui.QFontMetrics(card.font()).elidedText(alias, QtCore.Qt.ElideMiddle, 142)
            name = QtWidgets.QLabel(shown_name, alignment=QtCore.Qt.AlignCenter)
            name.setToolTip(alias)
            card_layout.addWidget(image)
            card_layout.addWidget(name)
            self.preview_layout.addWidget(card)
            self.preview_aliases.append(alias)
        self.preview_layout.addStretch(1)
        self.preview_scroll.setVisible(bool(self.preview_aliases))


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "text"
    default: Any = ""
    minimum: int = -999_999
    maximum: int = 999_999
    options: tuple[tuple[str, Any], ...] = ()
    section: str = "기본 설정"
    tooltip: str = ""
    placeholder: str = ""


def choice(*items: tuple[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(items)


ACTION_LABELS = {
    "mouse_click": "마우스 클릭",
    "inactive_click": "비활성 클릭",
    "image_search": "이미지 서치",
    "type_text": "텍스트 입력",
    "wait": "대기",
    "browser_action": "브라우저 요소",
    "ocr": "OCR 텍스트 인식",
    "table_store": "테이블 값 저장",
    "table_copy": "테이블 복사",
    "table_paste": "테이블 붙여넣기",
    "table_excel_read": "Excel 읽기",
    "table_excel_write": "Excel 쓰기",
    "set_var": "변수 설정",
    "calc_var": "변수 계산",
    "coord_mode": "좌표 기준 변경",
    "call_submacro": "서브매크로 호출",
    "flow_control": "반복 이동",
    "text_condition": "텍스트 조건",
    "run_program": "프로그램 실행",
    "terminate_program": "프로그램 종료",
    "remote_notify": "모바일 알림",
}


ACTION_FIELDS: dict[str, list[FieldSpec]] = {
    "mouse_click": [
        FieldSpec("x", "X 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("y", "Y 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("button", "마우스 버튼", "choice", "Left", options=choice(("왼쪽", "Left"), ("오른쪽", "Right"), ("가운데", "Middle"), ("휠 위", "WheelUp"), ("휠 아래", "WheelDown"))),
        FieldSpec("count", "클릭 횟수", "int", 1, 1, 50),
        FieldSpec("sleep_after", "클릭 후 대기", "duration", 0, 0, 600_000),
    ],
    "inactive_click": [
        FieldSpec("window", "대상 창", "text", "A", placeholder="ahk_id 0x... 또는 창 제목"),
        FieldSpec("window_exe", "대상 프로그램", "text", "", placeholder="예: whale.exe"),
        FieldSpec("control", "컨트롤", "text", "", placeholder="선택 사항"),
        FieldSpec("x", "X 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("y", "Y 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("button", "버튼", "choice", "Left", options=choice(("왼쪽", "Left"), ("오른쪽", "Right"), ("가운데", "Middle"), ("휠 위", "WheelUp"), ("휠 아래", "WheelDown"))),
        FieldSpec("clicks", "클릭 횟수", "int", 1, 1, 50),
        FieldSpec("method", "전송 방식", "choice", "controlclick", options=choice(("자동", "auto"), ("최상위 창 직접 메시지", "direct_postmessage"), ("ControlClick", "controlclick"), ("PostMessage", "postmessage"))),
        FieldSpec("options", "클릭 옵션", "text", "NA", placeholder="예: NA"),
        FieldSpec("action_type", "동작", "choice", "click", options=choice(("클릭", "click"), ("드래그", "drag"))),
        FieldSpec("drag_to.0", "드래그 끝 X", "int", 0, -100_000, 100_000, section="드래그·재시도"),
        FieldSpec("drag_to.1", "드래그 끝 Y", "int", 0, -100_000, 100_000, section="드래그·재시도"),
        FieldSpec("drag_click_after", "드래그 후 클릭", "bool", False, section="드래그·재시도"),
        FieldSpec("retry_count", "재시도 횟수", "int", 0, 0, 100, section="드래그·재시도"),
        FieldSpec("retry_delay", "재시도 간격", "duration", 80, 10, 60_000, section="드래그·재시도"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="드래그·재시도"),
    ],
    "image_search": [
        FieldSpec("asset", "검색 이미지", "asset", ""),
        FieldSpec(
            "assets",
            "멀티 검색 이미지",
            "assets",
            [],
            tooltip="2개 이상 선택하면 한 화면 캡처에서 모두 비교하고 정확도가 가장 높은 이미지를 선택합니다. 단일 검색 이미지는 우선 후보로 함께 포함됩니다.",
        ),
        FieldSpec("engine", "검색 엔진", "choice", "ahk", options=choice(("AutoHotkey · 가볍고 빠름", "ahk"), ("OpenCV · 정밀(선택 설치)", "opencv"))),
        FieldSpec("search_profile", "검색 품질", "choice", "fast", options=choice(("빠름 · 권장", "fast"), ("균형 · 색상 보강", "balanced"), ("정밀 · 70~150% 자동 배율", "precise"))),
        FieldSpec("variation", "색상 허용 오차", "int", 16, 0, 255, tooltip="낮을수록 더 정확하고 엄격하게 일치합니다."),
        FieldSpec("confidence", "일치 신뢰도", "int", 86, 50, 99, tooltip="OpenCV에서 사용하는 최소 일치율입니다."),
        FieldSpec("timeout", "검색 제한 시간", "duration", 1200, 0, 600_000),
        FieldSpec("poll_delay", "검색 반복 간격", "duration", 35, 10, 60_000),
        FieldSpec("trans", "투명색", "text", "", placeholder="예: FFFFFF"),
        FieldSpec("region_mode", "범위 기준", "choice", "screen", options=choice(("전체 화면 · 모든 모니터", "screen"), ("창", "window"), ("클라이언트", "client")), section="검색 범위"),
        FieldSpec("region_coords", "좌표 해석", "choice", "screen", options=choice(("화면 절대 좌표", "screen"), ("대상 기준 상대 좌표", "relative")), section="검색 범위"),
        FieldSpec("region_window", "검색 대상 창", "text", "", section="검색 범위"),
        FieldSpec("region_window_exe", "검색 대상 프로그램", "text", "", section="검색 범위"),
        FieldSpec("region.0", "왼쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.1", "위", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.2", "오른쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.3", "아래", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region2.0", "추가 범위 왼쪽", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.1", "추가 범위 위", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.2", "추가 범위 오른쪽", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.3", "추가 범위 아래", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("click_enabled", "찾으면 클릭", "bool", False, section="검색 성공 후 동작"),
        FieldSpec("click.mode", "클릭 모드", "choice", "active", options=choice(("활성 클릭", "active"), ("비활성 클릭", "inactive")), section="검색 성공 후 동작"),
        FieldSpec("click.method", "비활성 방식", "choice", "auto", options=choice(("자동 · 앱에 맞춤", "auto"), ("최상위 창 직접 메시지", "direct_postmessage"), ("ControlClick", "controlclick"), ("PostMessage", "postmessage")), section="검색 성공 후 동작"),
        FieldSpec("click.click_image", "찾은 이미지 중심 클릭", "bool", True, section="검색 성공 후 동작", tooltip="오프셋 클릭도 함께 켜면 이미지 중심을 먼저 클릭하고 오프셋 위치를 이어서 클릭합니다."),
        FieldSpec("click.click_offset", "오프셋 사용", "bool", False, section="검색 성공 후 동작", tooltip="체크하면 아래 위치 패드에서 지정한 만큼 이미지 중심에서 이동해 클릭합니다."),
        FieldSpec("click.offset", "오프셋 위치", "offset", [0, 0], section="검색 성공 후 동작"),
        FieldSpec("click.between_click_delay", "중심→오프셋 간격", "duration", 80, 0, 10_000, section="검색 성공 후 동작", tooltip="두 위치를 모두 클릭할 때 첫 클릭과 두 번째 클릭 사이의 대기 시간입니다."),
        FieldSpec("click.count", "각 위치 클릭 횟수", "int", 1, 1, 20, section="검색 성공 후 동작"),
        FieldSpec("click.window", "대상 창", "text", "", section="검색 성공 후 동작"),
        FieldSpec("click.window_exe", "대상 프로그램", "text", "", section="검색 성공 후 동작"),
        FieldSpec("click.keys", "클릭 후 키 입력", "text", "", section="검색 성공 후 동작"),
        FieldSpec("click.key_mode", "키 입력 방식", "choice", "inactive", options=choice(("비활성", "inactive"), ("활성", "active")), section="검색 성공 후 동작"),
        FieldSpec("abort_on_fail", "검색 실패 시 중단", "bool", False, section="완료 처리"),
        FieldSpec(
            "repeat_on_success",
            "성공하면 같은 노드 재검색",
            "bool",
            False,
            tooltip="이미지가 계속 발견되는 동안 현재 노드를 반복하고, 처음 미탐지되면 실패선으로 이동합니다.",
            section="완료 처리",
        ),
        FieldSpec("repeat_on_success_delay", "재검색 간격", "duration", 50, 0, 60_000, section="완료 처리"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="완료 처리"),
    ],
    "type_text": [
        FieldSpec("text", "입력 내용", "multiline", ""),
        FieldSpec("send_mode", "전송 방식", "choice", "input", options=choice(("빠른 입력", "input"), ("이벤트 입력", "event"), ("원문 입력", "raw"))),
        FieldSpec("mode", "대상 방식", "choice", "active", options=choice(("현재 활성 창", "active"), ("비활성 창", "inactive"))),
        FieldSpec("window", "비활성 대상 창", "text", ""),
        FieldSpec("delay", "입력 후 대기", "duration", 0, 0, 600_000),
    ],
    "wait": [FieldSpec("duration", "대기 시간", "duration", 500, 0, 3_600_000)],
    "browser_action": [
        FieldSpec("selector", "CSS 선택자", "text", "", placeholder="#button 또는 div.item"),
        FieldSpec("browser_action", "브라우저 동작", "choice", "click", options=choice(("클릭", "click"), ("더블 클릭", "double_click"), ("텍스트 입력", "type_text"), ("텍스트 추출", "extract_text"), ("마우스 올리기", "hover"))),
        FieldSpec("value", "입력 값", "text", ""),
        FieldSpec("title", "브라우저 창 제목", "text", ""),
        FieldSpec("prefer_active", "활성 탭 우선", "bool", True),
        FieldSpec("timeout", "제한 시간", "duration", 2000, 0, 600_000, section="연결 설정"),
        FieldSpec("poll_delay", "반복 확인 간격", "duration", 50, 0, 60_000, section="연결 설정"),
        FieldSpec("port", "Chrome 디버그 포트", "int", 9222, 1000, 65535, section="연결 설정"),
        FieldSpec("server_port", "로컬 서버 포트", "int", 9233, 1000, 65535, section="연결 설정"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="연결 설정"),
    ],
    "ocr": [
        FieldSpec("mode", "인식 대상", "choice", "region", options=choice(("화면 범위", "region"), ("브라우저 요소", "browser"))),
        FieldSpec("lang", "인식 언어", "text", "eng+kor"),
        FieldSpec("region.0", "왼쪽", "int", 0, -100_000, 100_000, section="화면 범위"),
        FieldSpec("region.1", "위", "int", 0, -100_000, 100_000, section="화면 범위"),
        FieldSpec("region.2", "오른쪽", "int", 0, -100_000, 100_000, section="화면 범위"),
        FieldSpec("region.3", "아래", "int", 0, -100_000, 100_000, section="화면 범위"),
        FieldSpec("selector", "CSS 선택자", "text", "", section="브라우저 요소"),
        FieldSpec("title", "창 제목", "text", "", section="브라우저 요소"),
        FieldSpec("port", "디버그 포트", "int", 9222, 1000, 65535, section="브라우저 요소"),
        FieldSpec("server_port", "서버 포트", "int", 9233, 1000, 65535, section="브라우저 요소"),
        FieldSpec("prefer_active", "활성 탭 우선", "bool", True, section="브라우저 요소"),
        FieldSpec("timeout", "제한 시간", "duration", 2000, 0, 600_000, section="브라우저 요소"),
        FieldSpec("poll_delay", "확인 간격", "duration", 50, 0, 60_000, section="브라우저 요소"),
        FieldSpec("ocr_action", "OCR 동작", "choice", "extract", options=choice(
            ("텍스트 추출", "extract"),
            ("텍스트 찾기", "find_text"),
            ("텍스트 클릭", "find_click"),
            ("오프셋 클릭", "find_click_offset"),
            ("숫자 추출", "extract_number"),
            ("숫자 조건", "number_condition"),
        ), section="OCR 엔진"),
        FieldSpec("profile", "인식 프로필", "choice", "auto", options=choice(
            ("균형 · 권장", "auto"),
            ("빠른 인식", "fast"),
            ("최고 정확도 · 두 엔진 교차", "precise"),
            ("숫자", "number"),
            ("게임 UI", "game_ui"),
        ), section="OCR 엔진"),
        FieldSpec("engine_preference", "OCR 엔진", "choice", "auto", options=choice(
            ("자동 · 한국어 모델 우선", "auto"),
            ("PaddleOCR · 한국어 PP-OCRv5", "paddle"),
            ("Tesseract · 문장 보강", "tesseract"),
        ), section="OCR 엔진"),
        FieldSpec("capture_mode", "캡처 대상", "choice", "screen", options=choice(
            ("화면 범위", "screen"),
            ("대상 창", "window"),
            ("클라이언트 영역", "client"),
        ), section="OCR 엔진"),
        FieldSpec("window_title", "대상 창 제목", "text", "", section="OCR 엔진"),
        FieldSpec("coord_base", "좌표 기준", "choice", "screen", options=choice(
            ("화면", "screen"),
            ("창", "window"),
            ("클라이언트", "client"),
        ), section="OCR 엔진"),
        FieldSpec("find_text", "찾을 텍스트", "text", "", section="텍스트 찾기"),
        FieldSpec("match_mode", "매칭 모드", "choice", "contains", options=choice(
            ("포함", "contains"),
            ("완전 일치", "exact"),
            ("정규식", "regex"),
            ("시작 문자열", "starts_with"),
            ("끝 문자열", "ends_with"),
        ), section="텍스트 찾기"),
        FieldSpec("expect_text", "예상 문자열", "text", "", section="텍스트 찾기"),
        FieldSpec("regex", "정규식", "text", "", section="텍스트 찾기"),
        FieldSpec("whitelist", "허용 문자", "text", "", placeholder="예: 0123456789.%", section="텍스트 찾기"),
        FieldSpec("minimum_confidence", "최소 신뢰도", "int", 35, 0, 100, tooltip="이 값보다 신뢰도가 낮은 인식 결과는 실패로 처리합니다.", section="텍스트 찾기"),
        FieldSpec("position_priority", "같은 글자 위치 선택", "choice", "top_left", options=choice(
            ("왼쪽 위부터", "top_left"),
            ("신뢰도 높은 순", "confidence"),
            ("오른쪽 위부터", "top_right"),
            ("왼쪽 아래부터", "bottom_left"),
            ("가장 큰 영역", "largest"),
        ), section="텍스트 찾기"),
        FieldSpec("click_offset_x", "클릭 오프셋 X", "int", 0, -10000, 10000, section="텍스트 클릭"),
        FieldSpec("click_offset_y", "클릭 오프셋 Y", "int", 0, -10000, 10000, section="텍스트 클릭"),
        FieldSpec("number_condition", "숫자 조건", "choice", "gte", options=choice(
            ("이상", "gte"),
            ("이하", "lte"),
            ("초과", "gt"),
            ("미만", "lt"),
            ("같음", "eq"),
            ("다름", "neq"),
        ), section="숫자 조건"),
        FieldSpec("number_value", "비교 값", "float", 0, -999999999, 999999999, section="숫자 조건"),
        FieldSpec(
            "value_regex",
            "값 추출 정규식",
            "text",
            "",
            placeholder=r"예: 횟수\s*[:=]?\s*(\d+)",
            tooltip="괄호로 묶은 값만 변수에 저장합니다. 비워두면 전체 OCR 결과에서 첫 숫자를 추출합니다.",
            section="변수 저장",
        ),
        FieldSpec("value_group", "저장할 그룹 번호", "int", 1, 0, 20, section="변수 저장"),
        FieldSpec("store_var", "결과 변수명", "text", "", section="변수 저장"),
        FieldSpec("output_path", "추가 저장 경로", "path", "", section="결과 저장"),
        FieldSpec("output_format", "저장 형식", "choice", "csv", options=choice(("CSV", "csv"), ("JSON", "json"), ("텍스트", "txt")), section="결과 저장"),
        FieldSpec("output_append", "기존 파일에 이어쓰기", "bool", True, section="결과 저장"),
        FieldSpec("excel_mode", "Excel 연동", "choice", "none", options=choice(("사용 안 함", "none"), ("파일", "file"), ("실행 중 Excel", "running")), section="Excel 연동"),
        FieldSpec("excel_path", "Excel 파일", "path", "", section="Excel 연동"),
        FieldSpec("excel_sheet", "시트", "text", "", section="Excel 연동"),
        FieldSpec("excel_cell", "셀", "text", "", placeholder="예: A1", section="Excel 연동"),
        FieldSpec("table", "데이터 테이블", "table", "", section="데이터 테이블 저장"),
        FieldSpec("table_row", "행", "int", 1, 1, 999_999, section="데이터 테이블 저장"),
        FieldSpec("table_col", "열", "text", "A", section="데이터 테이블 저장"),
        FieldSpec("table_row_step", "저장 후 행 이동", "int", 0, -999_999, 999_999, section="데이터 테이블 저장"),
        FieldSpec("table_col_step", "저장 후 열 이동", "int", 0, -999_999, 999_999, section="데이터 테이블 저장"),
    ],
    "table_store": [
        FieldSpec("table", "테이블", "table", "default"),
        FieldSpec("source", "값 출처", "choice", "manual", options=choice(("직접 입력", "manual"), ("마지막 OCR 값", "ocr_last"))),
        FieldSpec("value", "저장 값", "text", ""),
        FieldSpec("row", "행", "int", 1, 1, 999_999),
        FieldSpec("col", "열", "text", "A"),
    ],
    "table_copy": [
        FieldSpec("table", "테이블", "table", "default"),
        FieldSpec("row_start", "시작 행", "text", "1", placeholder="숫자 또는 $변수"),
        FieldSpec("row_end", "끝 행", "text", "1", placeholder="숫자 또는 $변수"),
        FieldSpec("col_start", "시작 열", "text", "A"),
        FieldSpec("col_end", "끝 열", "text", "A"),
        FieldSpec("use_selected_row", "선택된 행 사용", "bool", False, section="자동 이동"),
        FieldSpec("use_selected_col", "선택된 열 사용", "bool", False, section="자동 이동"),
        FieldSpec("row_step", "복사 후 행 이동", "int", 0, -999_999, 999_999, section="자동 이동"),
        FieldSpec("col_step", "복사 후 열 이동", "int", 0, -999_999, 999_999, section="자동 이동"),
        FieldSpec("cursor_persist", "다음 실행에도 위치 유지", "bool", True, section="자동 이동"),
    ],
    "table_paste": [
        FieldSpec("table", "테이블", "table", "default"),
        FieldSpec("row_start", "시작 행", "text", "1"),
        FieldSpec("row_end", "끝 행", "text", "1"),
        FieldSpec("col_start", "시작 열", "text", "A"),
        FieldSpec("col_end", "끝 열", "text", "A"),
        FieldSpec("mode", "붙여넣기 방식", "choice", "active", options=choice(("활성 창", "active"), ("비활성 창", "inactive"))),
        FieldSpec("window", "대상 창", "text", ""),
        FieldSpec("window_exe", "대상 프로그램", "text", ""),
        FieldSpec("use_selected_row", "선택된 행 사용", "bool", False, section="자동 이동"),
        FieldSpec("use_selected_col", "선택된 열 사용", "bool", False, section="자동 이동"),
        FieldSpec("row_step", "붙여넣기 후 행 이동", "int", 0, -999_999, 999_999, section="자동 이동"),
        FieldSpec("col_step", "붙여넣기 후 열 이동", "int", 0, -999_999, 999_999, section="자동 이동"),
        FieldSpec("cursor_persist", "다음 실행에도 위치 유지", "bool", True, section="자동 이동"),
    ],
    "table_excel_read": [],
    "table_excel_write": [],
    "set_var": [FieldSpec("name", "변수 이름", "text", "value"), FieldSpec("value", "값", "text", "")],
    "calc_var": [
        FieldSpec("name", "결과 변수", "text", "value"),
        FieldSpec("expr", "수식", "text", "", placeholder="예: price * 1.1"),
        FieldSpec("op", "간단 연산", "choice", "", options=choice(("사용 안 함", ""), ("더하기", "+"), ("빼기", "-"), ("곱하기", "*"), ("나누기", "/"))),
        FieldSpec("value", "연산 값", "text", ""),
    ],
    "coord_mode": [FieldSpec("mode", "좌표 기준", "choice", "Screen", options=choice(("전체 화면", "Screen"), ("창", "Window"), ("클라이언트", "Client")))],
    "call_submacro": [FieldSpec("macro", "호출할 매크로", "macro", "")],
    "flow_control": [
        FieldSpec("repeat_count", "이동 반복 횟수", "int", 0, 0, 999_999, tooltip="0이면 항상 이동합니다."),
        FieldSpec("jump_to", "이동할 노드", "int", 1, 1, 999_999),
        FieldSpec("counter_key", "공유 카운터 이름", "text", ""),
    ],
    "text_condition": [
        FieldSpec("source", "비교 대상", "choice", "ocr", options=choice(("마지막 OCR 텍스트", "ocr"), ("클립보드", "clipboard"))),
        FieldSpec("mode", "비교 방식", "choice", "contains", options=choice(("포함", "contains"), ("완전히 같음", "equals"))),
        FieldSpec("needle", "단일 조건값", "text", ""),
        FieldSpec("needles_text", "복수 조건값", "text", "", placeholder="쉼표로 구분"),
        FieldSpec("on_match", "일치 시 노드", "int", 0, 0, 999_999),
        FieldSpec("on_no_match", "불일치 시 노드", "int", 0, 0, 999_999),
        FieldSpec("normalize", "공백과 줄바꿈 무시", "bool", True),
        FieldSpec("case_sensitive", "대소문자 구분", "bool", False),
    ],
    "run_program": [FieldSpec("command", "실행 명령 또는 파일", "path", "")],
    "terminate_program": [FieldSpec("process", "프로세스 이름", "text", "", placeholder="예: notepad.exe")],
    "remote_notify": [
        FieldSpec("title", "알림 제목", "text", "MacroRelay"),
        FieldSpec("message", "알림 내용", "multiline", "동작이 완료되었습니다."),
        FieldSpec("level", "알림 종류", "choice", "success", options=choice(("완료", "success"), ("안내", "info"), ("주의", "warning"), ("오류", "error"))),
        FieldSpec("include_last_ocr", "마지막 OCR 결과 포함", "bool", False),
        FieldSpec("wait_delivery", "전송 결과를 기다림", "bool", False, tooltip="끄면 매크로는 알림 전송을 기다리지 않고 바로 다음 단계로 이동합니다."),
    ],
}

EXCEL_FIELDS = [
    FieldSpec("table", "테이블", "table", "default"),
    FieldSpec("row", "행", "int", 1, 1, 999_999),
    FieldSpec("col", "열", "text", "A"),
    FieldSpec("excel_mode", "Excel 연결", "choice", "file", options=choice(("파일", "file"), ("실행 중 Excel", "running"))),
    FieldSpec("excel_path", "Excel 파일", "path", ""),
    FieldSpec("excel_sheet", "시트", "text", ""),
    FieldSpec("excel_cell", "셀", "text", "", placeholder="예: A1"),
]
ACTION_FIELDS["table_excel_read"] = EXCEL_FIELDS
ACTION_FIELDS["table_excel_write"] = EXCEL_FIELDS
COMMON_FIELD_KEYS = {"sleep_after"}


class WindowPickerDialog(QtWidgets.QDialog):
    """클릭한 위치 아래의 최상위 창을 찾는 가벼운 Windows 선택기입니다."""

    def __init__(self, parent=None, ignored_hwnds: set[int] | None = None) -> None:
        super().__init__(parent)
        self.window_token = ""
        self.exe_name = ""
        self._ignored_hwnds = set(ignored_hwnds or ())
        self._point = QtCore.QPoint()
        self._highlight_hwnd = 0
        self._highlight_rect = QtCore.QRect()
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setMouseTracking(True)
        geometry = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QtWidgets.QLabel("대상 창을 클릭하세요  ·  Esc 취소", self)
        hint.setAlignment(QtCore.Qt.AlignCenter)
        hint.setStyleSheet("background:rgba(12,14,20,225); color:white; padding:14px; font-size:13pt; font-weight:700;")
        layout.addWidget(hint, 0, QtCore.Qt.AlignTop)
        layout.addStretch(1)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(31, 36, 48, 52))
        if self._highlight_rect.isValid():
            local_rect = self._highlight_rect.translated(-self.geometry().topLeft())
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor("#2997FF"), 5))
            painter.drawRect(local_rect.adjusted(2, 2, -2, -2))
        super().paintEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._highlight_hwnd, self._highlight_rect = self._window_under_point(event.globalPosition().toPoint())
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._point = event.globalPosition().toPoint()
            self._highlight_hwnd, self._highlight_rect = self._window_under_point(self._point)
            QtCore.QTimer.singleShot(100, self._resolve_window)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _resolve_window(self) -> None:
        try:
            user32 = ctypes.windll.user32
            hwnd = self._highlight_hwnd or self._window_under_point(self._point)[0]
            if hwnd:
                root = int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
                self.window_token = f"ahk_id 0x{root:X}"
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            self.exe_name = Path(buffer.value).name
                    finally:
                        ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            self.window_token = ""
            self.exe_name = ""
        if self.window_token:
            self.accept()
        else:
            self.reject()

    def _window_under_point(self, position: QtCore.QPoint) -> tuple[int, QtCore.QRect]:
        try:
            user32 = ctypes.windll.user32
            own_root = int(user32.GetAncestor(int(self.winId()), 2) or int(self.winId()))
            candidate = int(user32.GetWindow(own_root, 2) or 0)  # GW_HWNDNEXT
            while candidate:
                candidate_root = int(user32.GetAncestor(candidate, 2) or candidate)
                rect = wintypes.RECT()
                if (
                    candidate_root != own_root
                    and candidate_root not in self._ignored_hwnds
                    and user32.IsWindowVisible(candidate_root)
                    and user32.GetWindowRect(candidate_root, ctypes.byref(rect))
                    and rect.left <= position.x() < rect.right
                    and rect.top <= position.y() < rect.bottom
                ):
                    return candidate_root, QtCore.QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                candidate = int(user32.GetWindow(candidate, 2) or 0)
        except Exception:
            pass
        return 0, QtCore.QRect()


class CoordinatePickerDialog(QtWidgets.QDialog):
    """원하는 위치를 클릭하거나 F4를 눌러 좌표를 확정합니다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.point = QtGui.QCursor.pos()
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        geometry = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)
        self._f4_shortcut = QtGui.QShortcut(QtGui.QKeySequence("F4"), self)
        self._f4_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self._f4_shortcut.activated.connect(self._accept_cursor)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(QtCore.Qt.ActiveWindowFocusReason)
        QtCore.QTimer.singleShot(0, self._ensure_keyboard_focus)

    def _ensure_keyboard_focus(self) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.ActiveWindowFocusReason)

    def _accept_cursor(self) -> None:
        if not self.isVisible():
            return
        self.point = QtGui.QCursor.pos()
        self.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.point = event.globalPosition().toPoint()
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.point = event.globalPosition().toPoint()
        if event.button() == QtCore.Qt.LeftButton:
            event.accept()
            self.accept()
            return
        if event.button() == QtCore.Qt.RightButton:
            event.accept()
            self.reject()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_F4, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._accept_cursor()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(10, 14, 24, 24))
        local = self.point - self.geometry().topLeft()
        painter.setPen(QtGui.QPen(QtGui.QColor("#2997FF"), 2))
        painter.drawLine(local.x() - 22, local.y(), local.x() + 22, local.y())
        painter.drawLine(local.x(), local.y() - 22, local.x(), local.y() + 22)
        text = f"X {self.point.x()}   Y {self.point.y()}   ·   클릭 또는 F4 저장   ·   Esc 취소"
        font = QtGui.QFont("Malgun Gothic", 11)
        font.setBold(True)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        text_rect = metrics.boundingRect(text).adjusted(-14, -9, 14, 9)
        text_rect.moveTopLeft(QtCore.QPoint(local.x() + 18, local.y() + 18))
        if text_rect.right() > self.width() - 10:
            text_rect.moveRight(self.width() - 10)
        if text_rect.bottom() > self.height() - 10:
            text_rect.moveBottom(local.y() - 18)
        painter.fillRect(text_rect, QtGui.QColor(12, 18, 30, 225))
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(text_rect, QtCore.Qt.AlignCenter, text)
        super().paintEvent(event)


class OffsetPointPickerDialog(QtWidgets.QDialog):
    """Pick an image reference point and its desired click point on the real screen."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.reference_point: QtCore.QPoint | None = None
        self.click_point: QtCore.QPoint | None = None
        self.cursor_point = QtGui.QCursor.pos()
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        geometry = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def offset(self) -> list[int]:
        if self.reference_point is None or self.click_point is None:
            return [0, 0]
        delta = self.click_point - self.reference_point
        return [delta.x(), delta.y()]

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(QtCore.Qt.ActiveWindowFocusReason)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self.cursor_point = event.globalPosition().toPoint()
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            self.reject()
            event.accept()
            return
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        point = event.globalPosition().toPoint()
        if self.reference_point is None:
            self.reference_point = point
            self.cursor_point = point
            self.update()
        else:
            self.click_point = point
            self.accept()
        event.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _draw_marker(painter: QtGui.QPainter, point: QtCore.QPoint, color: QtGui.QColor, label: str) -> None:
        painter.setPen(QtGui.QPen(color, 3))
        painter.setBrush(QtGui.QColor(color.red(), color.green(), color.blue(), 55))
        painter.drawEllipse(point, 12, 12)
        painter.drawLine(point.x() - 20, point.y(), point.x() + 20, point.y())
        painter.drawLine(point.x(), point.y() - 20, point.x(), point.y() + 20)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(point + QtCore.QPoint(16, -16), label)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(10, 14, 24, 38))
        origin = self.geometry().topLeft()
        cursor = self.cursor_point - origin
        reference = self.reference_point - origin if self.reference_point is not None else None
        if reference is not None:
            painter.setPen(QtGui.QPen(QtGui.QColor("#46C2C7"), 2, QtCore.Qt.DashLine))
            painter.drawLine(reference, cursor)
            self._draw_marker(painter, reference, QtGui.QColor("#2997FF"), "1 · 이미지 기준점")
            self._draw_marker(painter, cursor, QtGui.QColor("#46C2C7"), "2 · 실제 클릭점")
            delta = self.cursor_point - self.reference_point
            instruction = f"2/2  오프셋 클릭점을 클릭하세요   ·   X {delta.x():+d}px  Y {delta.y():+d}px   ·   Esc 취소"
        else:
            self._draw_marker(painter, cursor, QtGui.QColor("#2997FF"), "1 · 이미지 기준점")
            instruction = "1/2  검색 이미지에서 기준이 될 위치를 클릭하세요   ·   Esc 취소"
        font = QtGui.QFont("Malgun Gothic", 12)
        font.setBold(True)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        box = metrics.boundingRect(instruction).adjusted(-18, -12, 18, 12)
        box.moveCenter(QtCore.QPoint(self.width() // 2, 38))
        painter.fillRect(box, QtGui.QColor(12, 18, 30, 235))
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(box, QtCore.Qt.AlignCenter, instruction)
        super().paintEvent(event)


class OffsetCanvas(QtWidgets.QWidget):
    offset_changed = QtCore.Signal(int, int)
    MAX_OFFSET = 5000
    VIEW_RANGES = (120, 300, 600, 1200, 2500, 5000)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._offset = QtCore.QPoint(0, 0)
        self._preview = QtGui.QPixmap()
        self._view_range = 300
        self._preview_zoom = 200
        self.setMinimumSize(320, 210)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setToolTip("이미지 중심을 기준으로 클릭할 위치를 지정합니다. 드래그하거나 아래 X/Y 값으로 정밀 입력하세요.")

    def set_offset(self, x: int, y: int) -> None:
        point = QtCore.QPoint(
            max(-self.MAX_OFFSET, min(self.MAX_OFFSET, int(x))),
            max(-self.MAX_OFFSET, min(self.MAX_OFFSET, int(y))),
        )
        required = max(abs(point.x()), abs(point.y()))
        if required > self._view_range:
            self._view_range = next((value for value in self.VIEW_RANGES if value >= required), self.MAX_OFFSET)
        if point == self._offset:
            self.update()
            return
        self._offset = point
        self.update()
        self.offset_changed.emit(point.x(), point.y())

    def offset(self) -> list[int]:
        return [self._offset.x(), self._offset.y()]

    def set_preview(self, path: Path | None) -> None:
        self._preview = QtGui.QPixmap(str(path)) if path is not None else QtGui.QPixmap()
        self.update()

    def set_view_range(self, value: int) -> None:
        requested = max(1, min(self.MAX_OFFSET, int(value or 300)))
        required = max(abs(self._offset.x()), abs(self._offset.y()))
        self._view_range = max(requested, required)
        self.update()

    def view_range(self) -> int:
        return self._view_range

    def set_preview_zoom(self, percent: int) -> None:
        self._preview_zoom = max(25, min(800, int(percent or 100)))
        self.update()

    def _set_from_position(self, position: QtCore.QPointF) -> None:
        area = self.rect().adjusted(18, 18, -18, -18)
        if area.width() <= 0 or area.height() <= 0:
            return
        x = round((position.x() - area.center().x()) / (area.width() / 2) * self._view_range)
        y = round((position.y() - area.center().y()) / (area.height() / 2) * self._view_range)
        self.set_offset(x, y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._set_from_position(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.LeftButton:
            self._set_from_position(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        area = self.rect().adjusted(18, 18, -18, -18)
        painter.setPen(QtGui.QPen(QtGui.QColor("#364052"), 1))
        painter.setBrush(QtGui.QColor("#10151D"))
        painter.drawRoundedRect(area, 10, 10)
        if not self._preview.isNull():
            desired = QtCore.QSize(
                max(1, round(self._preview.width() * self._preview_zoom / 100)),
                max(1, round(self._preview.height() * self._preview_zoom / 100)),
            )
            max_size = QtCore.QSize(max(1, round(area.width() * 0.72)), max(1, round(area.height() * 0.72)))
            if desired.width() > max_size.width() or desired.height() > max_size.height():
                desired.scale(max_size, QtCore.Qt.KeepAspectRatio)
            shown = self._preview.scaled(desired, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            target = QtCore.QRect(QtCore.QPoint(), shown.size())
            target.moveCenter(area.center())
            painter.save()
            painter.setOpacity(0.82)
            painter.drawPixmap(target, shown)
            painter.restore()
        center = area.center()
        painter.setPen(QtGui.QPen(QtGui.QColor("#536073"), 1, QtCore.Qt.DashLine))
        painter.drawLine(center.x(), area.top() + 8, center.x(), area.bottom() - 8)
        painter.drawLine(area.left() + 8, center.y(), area.right() - 8, center.y())
        painter.setPen(QtGui.QPen(QtGui.QColor("#263142"), 1, QtCore.Qt.DotLine))
        for fraction in (-0.5, 0.5):
            grid_x = center.x() + round(fraction * area.width() / 2)
            grid_y = center.y() + round(fraction * area.height() / 2)
            painter.drawLine(grid_x, area.top() + 8, grid_x, area.bottom() - 8)
            painter.drawLine(area.left() + 8, grid_y, area.right() - 8, grid_y)
        marker_x = center.x() + round(self._offset.x() / self._view_range * area.width() / 2)
        marker_y = center.y() + round(self._offset.y() / self._view_range * area.height() / 2)
        marker = QtCore.QPoint(marker_x, marker_y)
        painter.setPen(QtGui.QPen(QtGui.QColor("#46C2C7"), 2))
        painter.setBrush(QtGui.QColor("#163A40"))
        painter.drawEllipse(marker, 9, 9)
        painter.drawLine(marker.x() - 14, marker.y(), marker.x() + 14, marker.y())
        painter.drawLine(marker.x(), marker.y() - 14, marker.x(), marker.y() + 14)
        painter.setPen(QtGui.QColor("#8490A3"))
        painter.drawText(area.adjusted(8, 5, -8, -5), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, "↖")
        painter.drawText(area.adjusted(8, 5, -8, -5), QtCore.Qt.AlignTop | QtCore.Qt.AlignRight, "↗")
        painter.drawText(area.adjusted(8, 5, -8, -5), QtCore.Qt.AlignBottom | QtCore.Qt.AlignLeft, "↙")
        painter.drawText(area.adjusted(8, 5, -8, -5), QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight, "↘")
        painter.drawText(
            area.adjusted(8, 5, -8, -5),
            QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter,
            f"표시 범위 ±{self._view_range}px",
        )


class HorizontalWheelScrollArea(QtWidgets.QScrollArea):
    """Use the mouse wheel to move a compact horizontal thumbnail strip."""

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        bar = self.horizontalScrollBar()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar.setValue(bar.value() - round(delta / 120) * 170)
            event.accept()
            return
        super().wheelEvent(event)


class OffsetEditor(QtWidgets.QWidget):
    offset_picked = QtCore.Signal()
    multi_offset_edited = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.canvas = OffsetCanvas()
        self._multi_paths: dict[str, Path] = {}
        self._multi_offsets: dict[str, list[int]] = {}
        self._active_alias = ""
        self.asset_buttons: dict[str, QtWidgets.QToolButton] = {}
        self._switching_asset = False
        self.asset_row = QtWidgets.QWidget()
        asset_layout = QtWidgets.QVBoxLayout(self.asset_row)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.setSpacing(5)
        asset_header = QtWidgets.QHBoxLayout()
        asset_title = QtWidgets.QLabel("오프셋 설정 이미지")
        asset_title.setStyleSheet("font-weight: 700;")
        asset_hint = QtWidgets.QLabel("이미지를 눌러 각각의 클릭점을 설정하세요 · Alt+←/→")
        asset_hint.setObjectName("Muted")
        asset_header.addWidget(asset_title)
        asset_header.addSpacing(8)
        asset_header.addWidget(asset_hint)
        asset_header.addStretch(1)
        asset_layout.addLayout(asset_header)
        asset_strip = QtWidgets.QHBoxLayout()
        asset_strip.setContentsMargins(0, 0, 0, 0)
        asset_strip.setSpacing(5)
        self.asset_previous = QtWidgets.QToolButton()
        self.asset_previous.setText("‹")
        self.asset_previous.setToolTip("이전 이미지 (Alt+←)")
        self.asset_previous.setFixedSize(30, 112)
        self.asset_previous.clicked.connect(lambda: self._select_relative_asset(-1))
        self.asset_next = QtWidgets.QToolButton()
        self.asset_next.setText("›")
        self.asset_next.setToolTip("다음 이미지 (Alt+→)")
        self.asset_next.setFixedSize(30, 112)
        self.asset_next.clicked.connect(lambda: self._select_relative_asset(1))
        self.asset_scroll = HorizontalWheelScrollArea()
        self.asset_scroll.setWidgetResizable(False)
        self.asset_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.asset_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.asset_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.asset_scroll.setFixedHeight(112)
        self.asset_body = QtWidgets.QWidget()
        self.asset_cards_layout = QtWidgets.QHBoxLayout(self.asset_body)
        self.asset_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.asset_cards_layout.setSpacing(7)
        self.asset_scroll.setWidget(self.asset_body)
        asset_strip.addWidget(self.asset_previous)
        asset_strip.addWidget(self.asset_scroll, 1)
        asset_strip.addWidget(self.asset_next)
        asset_layout.addLayout(asset_strip)
        self.asset_row.setVisible(False)
        self.label = QtWidgets.QLabel("이미지 중심")
        self.label.setObjectName("Muted")
        self.range_combo = QtWidgets.QComboBox()
        for value in OffsetCanvas.VIEW_RANGES:
            self.range_combo.addItem(f"±{value}px", value)
        self.range_combo.setCurrentIndex(self.range_combo.findData(300))
        self.zoom_combo = QtWidgets.QComboBox()
        for value in (25, 50, 100, 200, 400, 800):
            self.zoom_combo.addItem(f"{value}%", value)
        self.zoom_combo.setCurrentIndex(self.zoom_combo.findData(200))
        self.x_spin = WheelSafeSpinBox()
        self.y_spin = WheelSafeSpinBox()
        for spin in (self.x_spin, self.y_spin):
            spin.setRange(-OffsetCanvas.MAX_OFFSET, OffsetCanvas.MAX_OFFSET)
            spin.setSuffix(" px")
        reset = QtWidgets.QPushButton("중앙으로 초기화")
        reset.clicked.connect(lambda: self.canvas.set_offset(0, 0))
        pick_points = QtWidgets.QPushButton("⌖ 화면에서 2점 지정")
        pick_points.setToolTip("1번: 검색 이미지의 기준점 클릭 · 2번: 실제로 클릭할 위치 클릭")
        pick_points.clicked.connect(self._pick_from_screen)
        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(QtWidgets.QLabel("작업 범위"))
        view_row.addWidget(self.range_combo)
        view_row.addSpacing(10)
        view_row.addWidget(QtWidgets.QLabel("이미지 배율"))
        view_row.addWidget(self.zoom_combo)
        view_row.addStretch(1)
        value_row = QtWidgets.QHBoxLayout()
        value_row.addWidget(self.label, 1)
        value_row.addWidget(QtWidgets.QLabel("X"))
        value_row.addWidget(self.x_spin)
        value_row.addWidget(QtWidgets.QLabel("Y"))
        value_row.addWidget(self.y_spin)
        value_row.addWidget(pick_points)
        value_row.addWidget(reset)
        layout.addWidget(self.asset_row)
        layout.addLayout(view_row)
        layout.addWidget(self.canvas)
        layout.addLayout(value_row)
        self.canvas.offset_changed.connect(self._sync_from_canvas)
        self.x_spin.valueChanged.connect(self._sync_from_inputs)
        self.y_spin.valueChanged.connect(self._sync_from_inputs)
        self.range_combo.currentIndexChanged.connect(self._change_view_range)
        self.zoom_combo.currentIndexChanged.connect(self._change_preview_zoom)
        self.canvas.offset_changed.connect(self._store_active_multi_offset)
        self.previous_asset_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Alt+Left"), self)
        self.next_asset_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Alt+Right"), self)
        self.previous_asset_shortcut.activated.connect(lambda: self._select_relative_asset(-1))
        self.next_asset_shortcut.activated.connect(lambda: self._select_relative_asset(1))

    def set_value(self, value: Any) -> None:
        values = value if isinstance(value, (list, tuple)) else [0, 0]
        x, y = (list(values) + [0, 0])[:2]
        self.canvas.set_offset(int(x or 0), int(y or 0))
        self._sync_from_canvas(int(x or 0), int(y or 0))

    def value(self) -> list[int]:
        return self.canvas.offset()

    def set_preview(self, path: Path | None) -> None:
        self.canvas.set_preview(path)

    def set_multi_assets(self, entries: list[tuple[str, Path]], offsets: dict[str, list[int]]) -> None:
        previous = self._active_alias
        self._multi_paths = {alias: Path(path) for alias, path in entries}
        self._multi_offsets = {
            alias: list(offsets.get(alias, [0, 0]))[:2]
            for alias, _path in entries
        }
        self._rebuild_asset_cards()
        self.asset_row.setVisible(len(entries) > 1)
        aliases = list(self._multi_paths)
        self._select_multi_asset(previous if previous in self._multi_paths else (aliases[0] if aliases else ""))

    def clear_multi_assets(self) -> None:
        self._multi_paths = {}
        self._multi_offsets = {}
        self._active_alias = ""
        self._clear_asset_cards()
        self.asset_row.setVisible(False)

    def multi_offsets(self) -> dict[str, list[int]]:
        return {alias: list(value) for alias, value in self._multi_offsets.items()}

    def _clear_asset_cards(self) -> None:
        while self.asset_cards_layout.count():
            item = self.asset_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.asset_buttons = {}

    def _rebuild_asset_cards(self) -> None:
        self._clear_asset_cards()
        for alias, path in self._multi_paths.items():
            button = QtWidgets.QToolButton()
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            button.setFixedSize(154, 106)
            button.setIconSize(QtCore.QSize(136, 64))
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setStyleSheet(
                "QToolButton { color:#DCE6F3; background:#111722; border:1px solid #334158;"
                " border-radius:8px; padding:4px; }"
                "QToolButton:hover { background:#172130; border-color:#5D718C; }"
                "QToolButton:checked { background:#142B35; border:3px solid #36DCE8; padding:2px; }"
            )
            pixmap = QtGui.QPixmap(str(path))
            if not pixmap.isNull():
                shown = pixmap.scaled(136, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                button.setIcon(QtGui.QIcon(shown))
            button.clicked.connect(lambda _checked=False, name=alias: self._select_multi_asset(name))
            self.asset_cards_layout.addWidget(button)
            self.asset_buttons[alias] = button
            self._refresh_asset_card(alias)
        self.asset_cards_layout.addStretch(1)
        width = max(1, len(self.asset_buttons) * 161)
        self.asset_body.setMinimumWidth(width)
        self.asset_body.resize(width, 106)
        enabled = len(self.asset_buttons) > 1
        self.asset_previous.setEnabled(enabled)
        self.asset_next.setEnabled(enabled)

    @staticmethod
    def _offset_summary(offset: list[int]) -> str:
        x, y = (list(offset) + [0, 0])[:2]
        if not x and not y:
            return "중앙"
        return f"X {int(x):+d} · Y {int(y):+d}"

    def _refresh_asset_card(self, alias: str) -> None:
        button = self.asset_buttons.get(alias)
        if button is None:
            return
        offset = self._multi_offsets.get(alias, [0, 0])
        configured = bool(int(offset[0] or 0) or int(offset[1] or 0))
        name = QtGui.QFontMetrics(button.font()).elidedText(alias, QtCore.Qt.ElideRight, 138)
        summary = self._offset_summary(offset)
        button.setText(f"{name}\n{'✓ ' if configured else ''}{summary}")
        button.setToolTip(f"{alias}\n클릭 위치: {summary}")

    def _select_multi_asset(self, alias: str) -> None:
        if not alias or alias not in self._multi_paths:
            return
        self._active_alias = alias
        self._switching_asset = True
        self.canvas.set_preview(self._multi_paths[alias])
        self.set_value(self._multi_offsets.get(alias, [0, 0]))
        self._switching_asset = False
        for name, button in self.asset_buttons.items():
            blocker = QtCore.QSignalBlocker(button)
            button.setChecked(name == alias)
            del blocker
        button = self.asset_buttons.get(alias)
        if button is not None:
            self.asset_scroll.ensureWidgetVisible(button, 8, 0)

    def _select_relative_asset(self, delta: int) -> None:
        aliases = list(self._multi_paths)
        if len(aliases) < 2:
            return
        try:
            current = aliases.index(self._active_alias)
        except ValueError:
            current = 0
        self._select_multi_asset(aliases[(current + delta) % len(aliases)])

    def _store_active_multi_offset(self, x: int, y: int) -> None:
        if self._switching_asset:
            return
        alias = self._active_alias
        if alias and alias in self._multi_paths:
            self._multi_offsets[alias] = [int(x), int(y)]
            self._refresh_asset_card(alias)
            self.multi_offset_edited.emit()

    def _pick_from_screen(self) -> None:
        picker = OffsetPointPickerDialog(self.window())
        if picker.exec() != QtWidgets.QDialog.Accepted:
            return
        x, y = picker.offset()
        self.canvas.set_offset(x, y)
        self._sync_from_canvas(x, y)
        self.offset_picked.emit()

    def _sync_from_canvas(self, x: int, y: int) -> None:
        for spin, value in ((self.x_spin, x), (self.y_spin, y)):
            blocker = QtCore.QSignalBlocker(spin)
            spin.setValue(value)
            del blocker
        current_range = self.canvas.view_range()
        if int(self.range_combo.currentData() or 0) != current_range:
            index = self.range_combo.findData(current_range)
            if index >= 0:
                blocker = QtCore.QSignalBlocker(self.range_combo)
                self.range_combo.setCurrentIndex(index)
                del blocker
        self._update_label(x, y)

    def _sync_from_inputs(self, _value: int) -> None:
        self.canvas.set_offset(self.x_spin.value(), self.y_spin.value())

    def _change_view_range(self, _index: int) -> None:
        self.canvas.set_view_range(int(self.range_combo.currentData() or 300))
        self._sync_from_canvas(*self.canvas.offset())

    def _change_preview_zoom(self, _index: int) -> None:
        self.canvas.set_preview_zoom(int(self.zoom_combo.currentData() or 200))

    def _update_label(self, x: int, y: int) -> None:
        if not x and not y:
            text = "이미지 중심"
        else:
            horizontal = "오른쪽" if x > 0 else "왼쪽" if x < 0 else "가운데"
            vertical = "아래" if y > 0 else "위" if y < 0 else "가운데"
            text = f"{horizontal} {abs(x)}px · {vertical} {abs(y)}px"
        self.label.setText(text)


def action_template(action: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action}
    for spec in ACTION_FIELDS.get(action, []):
        if spec.key in {"click_enabled", "region2.0", "region2.1", "region2.2", "region2.3", "needles_text"}:
            continue
        if spec.default not in ("", False, None):
            set_path(payload, spec.key, deepcopy(spec.default))
    return payload


def get_path(payload: dict[str, Any], key: str, default: Any = None) -> Any:
    current: Any = payload
    for part in key.split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return default
    return current


def set_path(payload: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    current: Any = payload
    for index, part in enumerate(parts[:-1]):
        next_is_index = parts[index + 1].isdigit()
        if isinstance(current, dict):
            if part not in current or not isinstance(current[part], (dict, list)):
                current[part] = [] if next_is_index else {}
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            position = int(part)
            while len(current) <= position:
                current.append([] if next_is_index else {})
            current = current[position]
    final = parts[-1]
    if isinstance(current, dict):
        current[final] = value
    elif isinstance(current, list) and final.isdigit():
        position = int(final)
        while len(current) <= position:
            current.append(None)
        current[position] = value


def remove_path(payload: dict[str, Any], key: str) -> None:
    parts = key.split(".")
    current: Any = payload
    for part in parts[:-1]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return
        if current is None:
            return
    final = parts[-1]
    if isinstance(current, dict):
        current.pop(final, None)


class ActionEditor(QtWidgets.QWidget):
    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.original: dict[str, Any] = {}
        self.current_action = "mouse_click"
        self._last_capture_rect = QtCore.QRect()
        self._last_capture_target: dict[str, Any] | None = None
        self.widgets: dict[str, dict[str, QtWidgets.QWidget]] = {}
        self.pages: dict[str, QtWidgets.QWidget] = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)
        for action in ACTION_LABELS:
            page = self._build_page(action)
            self.pages[action] = page
            self.stack.addWidget(page)

    def _build_page(self, action: str) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 4, 4, 4)
        body_layout.setSpacing(8)
        self.widgets[action] = {}
        helpers = self._build_helpers(action)
        if helpers is not None:
            body_layout.addWidget(helpers)
        sections: dict[str, QtWidgets.QFormLayout] = {}
        for spec in ACTION_FIELDS.get(action, []):
            if spec.key in COMMON_FIELD_KEYS:
                continue
            if spec.section not in sections:
                group = QtWidgets.QGroupBox(spec.section)
                form = QtWidgets.QFormLayout(group)
                form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
                sections[spec.section] = form
                body_layout.addWidget(group)
            widget = self._make_widget(spec)
            sections[spec.section].addRow(spec.label, widget)
            self.widgets[action][spec.key] = widget
        if action == "image_search":
            region_mode = self.widgets[action].get("region_mode")
            if isinstance(region_mode, QtWidgets.QComboBox):
                region_mode.currentIndexChanged.connect(self._sync_image_region_coordinates)
            profile = self.widgets[action].get("search_profile")
            if isinstance(profile, QtWidgets.QComboBox):
                profile.currentIndexChanged.connect(lambda _index: self._apply_search_profile())
            offset_toggle = self.widgets[action].get("click.click_offset")
            offset_editor = self.widgets[action].get("click.offset")
            if isinstance(offset_toggle, QtWidgets.QCheckBox) and isinstance(offset_editor, OffsetEditor):
                offset_editor.offset_picked.connect(lambda toggle=offset_toggle: toggle.setChecked(True))
            multi_picker = self.widgets[action].get("assets")
            if isinstance(offset_toggle, QtWidgets.QCheckBox) and isinstance(multi_picker, MultiAssetPicker):
                click_enabled = self.widgets[action].get("click_enabled")
                click_image = self.widgets[action].get("click.click_image")
                def enable_multi_offset_click() -> None:
                    offset_toggle.setChecked(True)
                    if isinstance(click_enabled, QtWidgets.QCheckBox):
                        click_enabled.setChecked(True)
                    if isinstance(click_image, QtWidgets.QCheckBox):
                        click_image.setChecked(False)
                multi_picker.offset_edited.connect(enable_multi_offset_click)
                if isinstance(offset_editor, OffsetEditor):
                    offset_editor.multi_offset_edited.connect(enable_multi_offset_click)
                multi_picker.selection_changed.connect(self._update_offset_preview)
            asset = self.widgets[action].get("asset")
            if isinstance(asset, QtWidgets.QComboBox):
                asset.currentIndexChanged.connect(self._update_offset_preview)
                if asset.isEditable():
                    asset.lineEdit().textChanged.connect(self._update_offset_preview)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _apply_search_profile(self) -> None:
        widgets = self.widgets.get("image_search", {})
        profile = widgets.get("search_profile")
        if not isinstance(profile, QtWidgets.QComboBox):
            return
        presets = {
            "fast": (30, 78, 30),
            "balanced": (16, 86, 60),
            "precise": (6, 92, 90),
        }
        variation, confidence, poll = presets.get(str(profile.currentData()), presets["balanced"])
        for key, value in (("variation", variation), ("confidence", confidence), ("poll_delay", poll)):
            widget = widgets.get(key)
            if isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(value)

    def _sync_image_region_coordinates(self) -> None:
        widgets = self.widgets.get("image_search", {})
        mode_widget = widgets.get("region_mode")
        coords_widget = widgets.get("region_coords")
        if not isinstance(mode_widget, QtWidgets.QComboBox) or not isinstance(coords_widget, QtWidgets.QComboBox):
            return
        mode = str(mode_widget.currentData() or "screen")
        desired = "screen" if mode == "screen" else "relative"
        index = coords_widget.findData(desired)
        if index >= 0:
            coords_widget.setCurrentIndex(index)

    def _build_helpers(self, action: str) -> QtWidgets.QWidget | None:
        buttons: list[tuple[str, Any]] = []
        if action in {"mouse_click", "inactive_click"}:
            buttons.append(("⌖ 좌표 선택 · 클릭/F4 저장", lambda: self._capture_cursor(action)))
        if action == "image_search":
            buttons.extend(
                [
                    ("⚡ 자동 설정", self._auto_configure_image_search),
                    ("▣ 전체 화면 · 모든 모니터", self._use_full_virtual_screen),
                    ("⌖ 캡처 등록·편집", self._capture_register_asset),
                    ("▣ 주 검색 범위 잡기", lambda: self._pick_region(action, "region")),
                    ("▣ 추가 검색 범위", lambda: self._pick_region(action, "region2")),
                    ("◎ 대상 창", lambda: self._pick_window(action, "window")),
                    ("▣ 대상 프로그램", lambda: self._pick_window(action, "program")),
                    ("설정 검사", self._diagnose_image_search),
                ]
            )
        elif action == "ocr":
            buttons.append(("▣ OCR 인식 범위 잡기", lambda: self._pick_region(action, "region")))
            buttons.append(("▶ OCR 테스트", lambda: self._test_ocr(action)))
        elif action in {"inactive_click", "type_text", "table_paste"}:
            buttons.append(("◎ 대상 창 찾기", lambda: self._pick_window(action)))
        if not buttons:
            return None
        group = QtWidgets.QGroupBox("빠른 입력")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(10, 8, 10, 8)
        for index, (label, callback) in enumerate(buttons):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button, index // 3, index % 3)
        return group

    def _set_field_value(self, action: str, key: str, value: Any) -> None:
        widget = self.widgets.get(action, {}).get(key)
        spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == key), None)
        if widget is not None and spec is not None:
            self._set_widget_value(widget, spec, value)

    def _use_full_virtual_screen(self) -> None:
        self._set_field_value("image_search", "region_mode", "screen")
        self._set_field_value("image_search", "region_coords", "screen")
        self._set_field_value("image_search", "region_window", "")
        self._set_field_value("image_search", "region_window_exe", "")
        for key in ("region", "region2"):
            for offset in range(4):
                self._set_field_value("image_search", f"{key}.{offset}", 0)
        QtWidgets.QToolTip.showText(
            QtGui.QCursor.pos(),
            "모든 모니터를 포함한 전체 가상 화면 검색으로 설정했습니다.",
            self,
        )

    def _capture_cursor(self, action: str) -> None:
        if action not in {"mouse_click", "inactive_click"}:
            return
        hosts = self._hide_host_windows()
        picker = CoordinatePickerDialog()
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        self._restore_host_windows(hosts)
        if accepted:
            self._set_field_value(action, "x", picker.point.x())
            self._set_field_value(action, "y", picker.point.y())

    def capture_current_coordinates(self) -> bool:
        if self.current_action not in {"mouse_click", "inactive_click"}:
            return False
        self._capture_cursor(self.current_action)
        return True

    def _hide_host_windows(self) -> list[tuple[QtWidgets.QWidget, bool, float, bool]]:
        hosts: list[QtWidgets.QWidget] = []
        current = self.window()
        while isinstance(current, QtWidgets.QWidget) and current not in hosts:
            hosts.append(current)
            parent = current.parentWidget()
            if parent is None:
                break
            current = parent.window()
        states: list[tuple[QtWidgets.QWidget, bool, float, bool]] = []
        for index, host in enumerate(hosts):
            was_visible = host.isVisible()
            opacity = host.windowOpacity()
            keep_modal_alive = index == 0 and isinstance(host, QtWidgets.QDialog)
            states.append((host, was_visible, opacity, keep_modal_alive))
            if keep_modal_alive:
                # Hiding a dialog opened with exec() terminates its modal loop as
                # Rejected.  Make it invisible without ending that loop instead.
                host.setWindowOpacity(0.0)
            else:
                host.hide()
        wait_loop = QtCore.QEventLoop(self)
        QtCore.QTimer.singleShot(180, wait_loop.quit)
        wait_loop.exec()
        return states

    @staticmethod
    def _restore_host_windows(states: list[tuple[QtWidgets.QWidget, bool, float, bool]]) -> None:
        for host, was_visible, opacity, keep_modal_alive in reversed(states):
            if was_visible and not keep_modal_alive:
                host.show()
            host.setWindowOpacity(opacity)
        if states:
            states[0][0].raise_()
            states[0][0].activateWindow()

    @staticmethod
    def _host_hwnds(states: list[tuple[QtWidgets.QWidget, bool, float, bool]]) -> set[int]:
        return {int(host.winId()) for host, *_ in states}

    def _test_ocr(self, action: str) -> None:
        """Run a quick OCR test with current settings."""
        from PySide6.QtWidgets import QMessageBox
        import json
        import socket
        import subprocess
        import sys
        import time

        step = self.build_step()
        if not step:
            QMessageBox.warning(self, "OCR 테스트", "OCR 설정을 먼저 입력하세요.")
            return

        region_val = step.get("region")
        if isinstance(region_val, list) and len(region_val) >= 4:
            region = [int(x or 0) for x in region_val[:4]]
        else:
            region = [0, 0, 0, 0]

        payload_dict = {
            "cmd": "ocr",
            "region": region,
            "capture_mode": str(step.get("capture_mode", "screen")),
            "window_title": str(step.get("window_title", "")),
            "lang": str(step.get("lang", "eng+kor")),
            "profile": str(step.get("profile", "auto")),
            "expect_text": str(step.get("expect_text", "")),
            "regex": str(step.get("regex", "")),
            "whitelist": str(step.get("whitelist", "")),
            "find_text": str(step.get("find_text", "")),
            "match_mode": str(step.get("match_mode", "contains")),
            "engine_preference": str(step.get("engine_preference", "auto")),
            "ocr_action": str(step.get("ocr_action", "extract")),
            "value_regex": str(step.get("value_regex", "")),
            "value_group": int(step.get("value_group", 1) or 0),
            "minimum_confidence": float(step.get("minimum_confidence", 35) or 0) / 100.0,
            "position_priority": str(step.get("position_priority", "top_left")),
            "debug": True,
        }
        payload = json.dumps(payload_dict, ensure_ascii=False)

        def _try_connect_and_send() -> dict | None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect(("127.0.0.1", 9234))
                sock.sendall(payload.encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
                resp = b""
                while True:
                    chunk = sock.recv(16384)
                    if not chunk:
                        break
                    resp += chunk
                sock.close()
                if resp:
                    return json.loads(resp.decode("utf-8"))
            except Exception:
                return None

        start = time.perf_counter()
        result = _try_connect_and_send()

        # If server is not running, attempt to start it automatically
        if result is None:
            try:
                ocr_engine_script = Path(__file__).resolve().parent.parent / "ocr_engine.py"
                if ocr_engine_script.is_file():
                    subprocess.Popen(
                        [sys.executable, str(ocr_engine_script), "--server", "--port", "9234"],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    time.sleep(1.2)
                    result = _try_connect_and_send()
            except Exception:
                pass

        elapsed = (time.perf_counter() - start) * 1000

        if result is None:
            QMessageBox.warning(
                self,
                "OCR 테스트",
                "OCR 엔진 서버 연결에 실패했습니다.\nocr_engine.py 실행 상태를 확인하세요."
            )
            return

        text = result.get("text", "")
        conf = result.get("confidence", 0.0)
        engine = result.get("engine", "")
        error = result.get("error", "")

        if error:
            msg = f"OCR 오류: {error}\n\n소요 시간: {elapsed:.0f}ms"
            QMessageBox.warning(self, "OCR 테스트 결과", msg)
        else:
            self._show_ocr_test_result(result, elapsed)

    def _show_ocr_test_result(self, result: dict[str, Any], elapsed: float) -> None:
        """Show OCR text and every detected coordinate without hiding detail."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("OCR 테스트 결과")
        dialog.setModal(True)
        dialog.resize(760, 590)
        layout = QtWidgets.QVBoxLayout(dialog)

        success = bool(result.get("success"))
        confidence = float(result.get("confidence", 0.0) or 0.0)
        engine = str(result.get("engine", "") or "-")
        status = QtWidgets.QLabel(
            ("● 인식 조건 성공" if success else "● 인식 조건 실패")
            + f"   ·   신뢰도 {confidence:.1%}   ·   {engine}   ·   {elapsed:.0f} ms"
        )
        status.setStyleSheet(f"color: {'#45D6A8' if success else '#FF6B81'}; font-weight: 700;")
        layout.addWidget(status)

        match_box = result.get("match_box") if isinstance(result.get("match_box"), dict) else None
        if match_box:
            center = match_box.get("center") or [0, 0]
            match_label = QtWidgets.QLabel(
                f"찾은 글자: {match_box.get('text', '')}   ·   실제 화면 중심 X {center[0]}, Y {center[1]}"
            )
            match_label.setObjectName("Muted")
            layout.addWidget(match_label)

        if "extracted_value" in result or "extracted_number" in result:
            extracted = result.get("extracted_number", result.get("extracted_value", ""))
            extracted_label = QtWidgets.QLabel(f"변수에 저장될 값: {extracted}")
            extracted_label.setStyleSheet("color: #5ED9FF; font-weight: 700;")
            layout.addWidget(extracted_label)

        layout.addWidget(QtWidgets.QLabel("인식된 전체 텍스트"))
        text_view = QtWidgets.QPlainTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(str(result.get("text", "") or "(인식된 텍스트 없음)"))
        text_view.setMaximumHeight(150)
        layout.addWidget(text_view)

        boxes = result.get("boxes") if isinstance(result.get("boxes"), list) else []
        layout.addWidget(QtWidgets.QLabel(f"인식 영역과 실제 화면 좌표 · {len(boxes)}개"))
        table = QtWidgets.QTableWidget(len(boxes), 5)
        table.setHorizontalHeaderLabels(["텍스트", "신뢰도", "중심 X", "중심 Y", "화면 영역"])
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        for row, box in enumerate(boxes):
            if not isinstance(box, dict):
                continue
            center = box.get("center") or [0, 0]
            rect = box.get("rect") or [0, 0, 0, 0]
            values = [
                str(box.get("text", "")),
                f"{float(box.get('confidence', 0.0) or 0.0):.1%}",
                str(center[0]),
                str(center[1]),
                f"{rect[0]}, {rect[1]} → {rect[2]}, {rect[3]}",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 5):
            table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)

        capture_meta = result.get("capture_meta") if isinstance(result.get("capture_meta"), dict) else {}
        actual_region = capture_meta.get("actual_region")
        if actual_region:
            capture_label = QtWidgets.QLabel(f"실제 캡처 범위: {actual_region}")
            capture_label.setObjectName("Muted")
            layout.addWidget(capture_label)

        close_button = QtWidgets.QPushButton("확인")
        close_button.clicked.connect(dialog.accept)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        dialog.exec()

    def _pick_region(self, action: str, key: str) -> None:
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = ScreenCaptureDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        rect = picker.selected_screen_rect() if accepted else QtCore.QRect()
        self._restore_host_windows(hosts)
        if rect.isValid():
            for offset, value in enumerate((rect.left(), rect.top(), rect.right(), rect.bottom())):
                self._set_field_value(action, f"{key}.{offset}", value)

    def _pick_window(self, action: str, target_kind: str = "window") -> None:
        hosts = self._hide_host_windows()
        picker = WindowPickerDialog(ignored_hwnds=self._host_hwnds(hosts))
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        self._restore_host_windows(hosts)
        if not accepted:
            return
        if action == "image_search":
            self._set_field_value(action, "click.window", picker.window_token)
            self._set_field_value(action, "click.window_exe", picker.exe_name)
            self._set_field_value(action, "region_window", picker.window_token)
            self._set_field_value(action, "region_window_exe", picker.exe_name)
            self._set_field_value(action, "region_mode", "client")
            self._set_field_value(action, "region_coords", "relative")
            for key in ("region", "region2"):
                for offset in range(4):
                    self._set_field_value(action, f"{key}.{offset}", 0)
        else:
            self._set_field_value(action, "window", picker.window_token)
            self._set_field_value(action, "window_exe", picker.exe_name)

    def _capture_image(self) -> QtGui.QImage:
        hosts = self._hide_host_windows()
        ignored_hwnds = self._host_hwnds(hosts)
        self._last_capture_rect = QtCore.QRect()
        self._last_capture_target = None
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return QtGui.QImage()
        picker = ScreenCaptureDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        image = picker.captured_image() if accepted else QtGui.QImage()
        if accepted:
            self._last_capture_rect = picker.selected_screen_rect()
            if self._last_capture_rect.isValid():
                self._last_capture_target = self._window_target_at(self._last_capture_rect.center(), ignored_hwnds)
        self._restore_host_windows(hosts)
        return image

    @staticmethod
    def _window_target_at(position: QtCore.QPoint, ignored_hwnds: set[int] | None = None) -> dict[str, Any] | None:
        """캡처 중심점이 일반 앱의 클라이언트 영역이면 자동 검색 대상을 반환합니다."""
        ignored = set(ignored_hwnds or ())
        try:
            user32 = ctypes.windll.user32
            point = wintypes.POINT(position.x(), position.y())
            hwnd = int(user32.WindowFromPoint(point) or 0)
            root = int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
            if not root or root in ignored or not user32.IsWindowVisible(root):
                return None

            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(root, class_buffer, len(class_buffer))
            if class_buffer.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
                return None

            client_rect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if not user32.GetClientRect(root, ctypes.byref(client_rect)) or not user32.ClientToScreen(root, ctypes.byref(origin)):
                return None
            width = int(client_rect.right - client_rect.left)
            height = int(client_rect.bottom - client_rect.top)
            screen_rect = QtCore.QRect(int(origin.x), int(origin.y), width, height)
            window_rect = wintypes.RECT()
            if not user32.GetWindowRect(root, ctypes.byref(window_rect)):
                return None
            window_screen_rect = QtCore.QRect(
                int(window_rect.left),
                int(window_rect.top),
                int(window_rect.right - window_rect.left),
                int(window_rect.bottom - window_rect.top),
            )
            if width < 32 or height < 32 or not window_screen_rect.contains(position):
                return None
            capture_scope = "client" if screen_rect.contains(position) else "window"
            capture_rect = screen_rect if capture_scope == "client" else window_screen_rect

            pid = wintypes.DWORD()
            thread_id = int(user32.GetWindowThreadProcessId(root, ctypes.byref(pid)) or 0)
            title_buffer = ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(root, title_buffer, len(title_buffer))
            exe_name = ""
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    size = wintypes.DWORD(32768)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        exe_name = Path(buffer.value).name
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            if not exe_name:
                return None
            stable_window = f"{title_buffer.value} ahk_exe {exe_name}" if title_buffer.value else (
                f"ahk_class {class_buffer.value} ahk_exe {exe_name}" if class_buffer.value else f"ahk_exe {exe_name}"
            )
            return {
                "window": stable_window,
                "exe": exe_name,
                "hwnd": root,
                "pid": int(pid.value),
                "thread": thread_id,
                "title": title_buffer.value,
                "class": class_buffer.value,
                "client_origin": [int(origin.x), int(origin.y)],
                "client_size": [width, height],
                "capture_scope": capture_scope,
                "capture_origin": [int(capture_rect.left()), int(capture_rect.top())],
                "capture_size": [int(capture_rect.width()), int(capture_rect.height())],
                "window_rect": [
                    int(window_rect.left),
                    int(window_rect.top),
                    int(window_rect.right),
                    int(window_rect.bottom),
                ],
                "width": int(capture_rect.width()),
                "height": int(capture_rect.height()),
                "rect": capture_rect,
            }
        except Exception:
            return None

    def _capture_register_asset(self, _checked: bool = False, automatic: bool = False) -> str:
        image = self._capture_image()
        if image.isNull():
            return ""
        default = QtCore.QDateTime.currentDateTime().toString("yyyyMMdd-HHmmss")
        alias, accepted = QtWidgets.QInputDialog.getText(self, "검색 이미지 등록", "이미지 이름", text=f"search-{default}")
        if not accepted or not alias.strip():
            return ""
        try:
            key = self.repository.add_asset_image(image.convertToFormat(QtGui.QImage.Format_RGB32), alias.strip())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "이미지 등록 실패", str(exc))
            return ""
        path = self.repository.asset_path(key)
        if path is not None:
            editor = ImageEditorDialog(path, key, self.repository.history_dir, self.window())
            editor.saved.connect(lambda _path: self.repository.refresh_asset_metadata(key))
            editor.exec()
        self.refresh_sources()
        combo = self.widgets.get("image_search", {}).get("asset")
        if isinstance(combo, QtWidgets.QComboBox):
            index = combo.findData(key)
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                QtWidgets.QMessageBox.warning(self, "이미지 등록 확인", f"'{key}' 이미지를 목록에서 찾지 못했습니다.")
                return ""
        self._update_offset_preview()
        return key

    def _auto_configure_image_search(self) -> None:
        key = self._capture_register_asset(automatic=True)
        if not key:
            return
        geometry = virtual_desktop_geometry()
        target = self._last_capture_target
        engine_widget = self.widgets.get("image_search", {}).get("engine")
        selected_engine = (
            str(engine_widget.currentData() or "ahk") if isinstance(engine_widget, QtWidgets.QComboBox) else "ahk"
        )
        target_scope = str(target.get("capture_scope") or "client") if target else "screen"
        values = {
            "engine": selected_engine,
            "search_profile": "fast",
            "variation": 24,
            "confidence": 86,
            "timeout": 1200,
            "poll_delay": 35,
            "region_mode": target_scope if target else "screen",
            "region_coords": "relative" if target else "screen",
            "region_window": str(target.get("window") or "") if target else "",
            "region_window_exe": str(target.get("exe") or "") if target else "",
            "click_enabled": True,
            "click.method": "auto",
            "click.click_offset": False,
            "click.window": str(target.get("window") or "") if target else "",
            "click.window_exe": str(target.get("exe") or "") if target else "",
        }
        for field, value in values.items():
            self._set_field_value("image_search", field, value)
        if target:
            region_values = (0, 0, max(0, int(target["width"]) - 1), max(0, int(target["height"]) - 1))
        elif geometry.isValid():
            region_values = (geometry.left(), geometry.top(), geometry.right(), geometry.bottom())
        else:
            region_values = (0, 0, 0, 0)
        for offset, value in enumerate(region_values):
            self._set_field_value("image_search", f"region.{offset}", value)
        for offset in range(4):
            self._set_field_value("image_search", f"region2.{offset}", 0)
        target_message = (
            f"'{target['exe']}'의 {'창 전체' if target_scope == 'window' else '클라이언트 영역'}을 감지해 상대 좌표로 설정했습니다."
            if target
            else "일반 앱 창을 감지하지 못해 다중 모니터 전체 화면·절대 좌표로 설정했습니다."
        )
        QtWidgets.QMessageBox.information(
            self,
            "이미지 서치 자동 설정",
            f"검색 이미지와 고속 검색값을 자동 설정했습니다.\n{target_message}\n크기 변화가 큰 대상만 검색 품질을 정밀로 바꾸세요.",
        )

    def _update_offset_preview(self) -> None:
        widgets = self.widgets.get("image_search", {})
        combo = widgets.get("asset")
        picker = widgets.get("assets")
        editor = widgets.get("click.offset")
        if not isinstance(combo, QtWidgets.QComboBox) or not isinstance(editor, OffsetEditor):
            return
        if isinstance(picker, MultiAssetPicker):
            aliases = picker.value()
            if len(aliases) > 1:
                existing = editor.multi_offsets()
                offsets = picker.offsets()
                offsets.update({alias: value for alias, value in existing.items() if alias in aliases})
                entries = [
                    (alias, path)
                    for alias in aliases
                    if (path := self.repository.asset_path(alias)) is not None
                ]
                editor.set_multi_assets(entries, offsets)
                return
        editor.clear_multi_assets()
        alias = str(combo.currentData() or combo.currentText() or "")
        editor.set_preview(self.repository.asset_path(alias) if alias else None)

    def _diagnose_image_search(self) -> None:
        step = self.build_step()
        aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
        alias = str(step.get("asset") or "")
        if alias and alias not in aliases:
            aliases.insert(0, alias)
        issues: list[str] = []
        if not aliases:
            issues.append("검색 이미지가 선택되지 않았습니다.")
        else:
            for candidate in aliases:
                path = self.repository.asset_path(candidate)
                if path is None:
                    issues.append(f"'{candidate}' 이미지 파일이 인덱스에 없거나 이동되었습니다.")
                    continue
                image = QtGui.QImage(str(path))
                if image.isNull():
                    issues.append(f"'{candidate}' 이미지 파일을 디코딩할 수 없습니다.")
                elif image.width() < 3 or image.height() < 3:
                    issues.append(f"'{candidate}' 검색 이미지가 너무 작습니다.")
        regions = step.get("regions") or []
        raw_regions: list[list[int]] = []
        for key in ("region", "region2"):
            values: list[int] = []
            for offset in range(4):
                widget = self.widgets.get("image_search", {}).get(f"{key}.{offset}")
                values.append(widget.value() if isinstance(widget, QtWidgets.QSpinBox) else 0)
            raw_regions.append(values)
        if any(any(values) and (values[2] <= values[0] or values[3] <= values[1]) for values in raw_regions):
            issues.append("입력한 검색 범위의 폭 또는 높이가 0입니다. 범위를 다시 드래그하세요.")
        if regions and not any(
            isinstance(region, list) and len(region) >= 4 and region[0] != region[2] and region[1] != region[3]
            for region in regions
        ):
            issues.append("저장된 검색 범위가 한 점(0×0)입니다. 전체 화면 범위로 다시 지정하세요.")
        if str(step.get("engine") or "ahk") == "opencv":
            try:
                __import__("cv2")
            except Exception:
                issues.append("현재 실행 환경에는 OpenCV가 없어 OpenCV 엔진을 사용할 수 없습니다.")
        if issues:
            QtWidgets.QMessageBox.warning(self, "이미지 서치 설정 검사", "\n".join(f"• {issue}" for issue in issues))
        else:
            QtWidgets.QMessageBox.information(self, "이미지 서치 설정 검사", "파일, 범위, 검색 엔진 설정이 정상입니다.")

    def _make_widget(self, spec: FieldSpec) -> QtWidgets.QWidget:
        if spec.kind in {"int", "duration"}:
            widget = WheelSafeSpinBox()
            widget.setRange(spec.minimum, spec.maximum)
            if spec.kind == "duration":
                widget.setSuffix(" ms")
        elif spec.kind == "offset":
            widget = OffsetEditor()
        elif spec.kind == "assets":
            widget = MultiAssetPicker()
        elif spec.kind == "bool":
            widget = QtWidgets.QCheckBox()
        elif spec.kind in {"choice", "asset", "macro", "table"}:
            widget = SearchableAssetCombo() if spec.kind == "asset" else QtWidgets.QComboBox()
            if spec.kind in {"asset", "macro", "table"}:
                widget.setEditable(spec.kind in {"asset", "table"})
            else:
                for label, value in spec.options:
                    widget.addItem(label, value)
        elif spec.kind == "multiline":
            widget = QtWidgets.QPlainTextEdit()
            widget.setMaximumHeight(96)
        elif spec.kind == "path":
            holder = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            editor = QtWidgets.QLineEdit()
            browse = QtWidgets.QPushButton("찾기")
            browse.setFixedWidth(58)
            browse.clicked.connect(lambda _=False, target=editor: self._browse_path(target))
            row.addWidget(editor, 1)
            row.addWidget(browse)
            holder.setProperty("editor", editor)
            widget = holder
        else:
            widget = QtWidgets.QLineEdit()
        target = self._value_widget(widget)
        if spec.tooltip:
            widget.setToolTip(spec.tooltip)
            target.setToolTip(spec.tooltip)
        if spec.placeholder and isinstance(target, QtWidgets.QLineEdit):
            target.setPlaceholderText(spec.placeholder)
        return widget

    @staticmethod
    def _value_widget(widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        editor = widget.property("editor")
        return editor if isinstance(editor, QtWidgets.QWidget) else widget

    def _browse_path(self, editor: QtWidgets.QLineEdit) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "파일 선택", editor.text())
        if filename:
            editor.setText(filename)

    def refresh_sources(self) -> None:
        assets = list(self.repository.load_assets())
        asset_paths = {alias: self.repository.asset_path(alias) for alias in assets}
        macros = [summary.name for summary in self.repository.list_macros()]
        tables = list(self.repository.load_tables())
        for action, specs in ACTION_FIELDS.items():
            for spec in specs:
                if spec.key in COMMON_FIELD_KEYS:
                    continue
                if spec.kind not in {"asset", "assets", "macro", "table"}:
                    continue
                combo = self.widgets[action].get(spec.key)
                if isinstance(combo, MultiAssetPicker):
                    combo.set_options(assets, asset_paths)
                    continue
                if isinstance(combo, SearchableAssetCombo):
                    combo.set_asset_options(assets, asset_paths)
                    continue
                if not isinstance(combo, QtWidgets.QComboBox):
                    continue
                previous = combo.currentText()
                combo.clear()
                combo.addItem("선택 안 함", "")
                for value in assets if spec.kind == "asset" else macros if spec.kind == "macro" else tables:
                    combo.addItem(value, value)
                index = combo.findData(previous)
                if index < 0:
                    index = combo.findText(previous)
                combo.setCurrentIndex(max(index, 0))

    def set_action(self, action: str) -> None:
        if action not in self.pages:
            return
        self.current_action = action
        self.stack.setCurrentWidget(self.pages[action])

    def load_step(self, step: dict[str, Any]) -> None:
        self.original = deepcopy(step)
        action = str(step.get("action") or "mouse_click")
        self.set_action(action)
        normalized = deepcopy(step)
        if action == "image_search":
            regions = normalized.get("regions")
            if isinstance(regions, list) and regions:
                normalized["region"] = regions[0]
                if len(regions) > 1:
                    normalized["region2"] = regions[1]
            normalized["click_enabled"] = isinstance(normalized.get("click"), dict) and bool(normalized.get("click"))
        if action == "text_condition":
            needles = normalized.get("needles")
            normalized["needles_text"] = ", ".join(str(item) for item in needles) if isinstance(needles, list) else str(needles or "")
        for spec in ACTION_FIELDS.get(action, []):
            if spec.key in COMMON_FIELD_KEYS:
                continue
            value = get_path(normalized, spec.key, spec.default)
            self._set_widget_value(self.widgets[action][spec.key], spec, value)
        if action == "image_search":
            picker = self.widgets[action].get("assets")
            offset_editor = self.widgets[action].get("click.offset")
            if isinstance(offset_editor, OffsetEditor):
                offset_editor.clear_multi_assets()
            if isinstance(picker, MultiAssetPicker):
                picker.set_offsets(normalized.get("asset_offsets") or {})
            self._update_offset_preview()

    def build_step(self) -> dict[str, Any]:
        action = self.current_action
        payload = deepcopy(self.original) if self.original.get("action") == action else action_template(action)
        original_handle_method = str(self.original.get("method") or "") if action == "inactive_click" else ""
        original_click = self.original.get("click") if action == "image_search" and isinstance(self.original.get("click"), dict) else {}
        payload["action"] = action
        for spec in ACTION_FIELDS.get(action, []):
            if spec.key in COMMON_FIELD_KEYS:
                continue
            value = self._widget_value(self.widgets[action][spec.key], spec)
            if spec.kind in {"text", "path"} and value == "" and spec.default == "":
                remove_path(payload, spec.key)
            else:
                set_path(payload, spec.key, value)
        if action == "image_search":
            multi_assets = [str(value) for value in payload.get("assets") or [] if str(value).strip()]
            primary_asset = str(payload.get("asset") or "").strip()
            if multi_assets:
                if primary_asset and primary_asset not in multi_assets:
                    multi_assets.insert(0, primary_asset)
                payload["assets"] = list(dict.fromkeys(multi_assets))
                payload["asset"] = payload["assets"][0]
                payload["engine"] = "opencv"
                picker = self.widgets[action].get("assets")
                if isinstance(picker, MultiAssetPicker):
                    offset_editor = self.widgets[action].get("click.offset")
                    if isinstance(offset_editor, OffsetEditor):
                        payload["asset_offsets"] = offset_editor.multi_offsets()
                        picker.set_offsets(payload["asset_offsets"])
                    else:
                        payload["asset_offsets"] = picker.offsets()
            else:
                payload.pop("assets", None)
                payload.pop("asset_offsets", None)
            click_enabled = bool(payload.pop("click_enabled", False))
            region2 = payload.pop("region2", None)
            region = payload.get("region")
            def valid_region(value: Any) -> bool:
                if not isinstance(value, list) or len(value) < 4:
                    return False
                try:
                    left, top, right, bottom = (int(item or 0) for item in value[:4])
                except (TypeError, ValueError):
                    return False
                return right > left and bottom > top

            has_region = valid_region(region)
            has_region2 = valid_region(region2)
            if has_region and has_region2:
                payload["regions"] = [region, region2]
                payload.pop("region", None)
            elif has_region:
                payload["regions"] = [region]
                payload.pop("region", None)
            else:
                payload.pop("regions", None)
                payload.pop("region", None)
            if not click_enabled:
                payload.pop("click", None)
            elif str(original_click.get("method") or "") == "handle_probe" and isinstance(payload.get("click"), dict):
                payload["click"]["method"] = "handle_probe"
                for key in ("target_control", "target_hwnd", "target_child_class"):
                    if key in original_click:
                        payload["click"][key] = original_click[key]
        if action == "inactive_click" and original_handle_method == "handle_probe":
            payload["method"] = "handle_probe"
            for key in ("target_control", "target_hwnd", "target_child_class"):
                if key in self.original:
                    payload[key] = self.original[key]
        if action == "text_condition":
            text = str(payload.pop("needles_text", "") or "")
            if text:
                payload["needles"] = [item.strip() for item in text.split(",") if item.strip()]
            else:
                payload.pop("needles", None)
        return payload

    def _set_widget_value(self, widget: QtWidgets.QWidget, spec: FieldSpec, value: Any) -> None:
        target = self._value_widget(widget)
        if isinstance(target, OffsetEditor):
            target.set_value(value)
        elif isinstance(target, MultiAssetPicker):
            target.set_value(value)
        elif isinstance(target, QtWidgets.QSpinBox):
            try:
                target.setValue(int(value or 0))
            except (TypeError, ValueError):
                target.setValue(int(spec.default or 0))
        elif isinstance(target, QtWidgets.QCheckBox):
            target.setChecked(bool(value))
        elif isinstance(target, QtWidgets.QComboBox):
            index = target.findData(value)
            if index < 0:
                index = target.findText(str(value))
            if index < 0 and spec.kind in {"asset", "macro", "table"} and str(value or "").strip():
                # A smart recording can create an asset after this editor's
                # source list was last refreshed.  Keep that new value instead
                # of silently falling back to the 'no selection' row.
                target.addItem(str(value), str(value))
                index = target.count() - 1
            target.setCurrentIndex(max(index, 0))
        elif isinstance(target, QtWidgets.QPlainTextEdit):
            target.setPlainText(str(value or ""))
        elif isinstance(target, QtWidgets.QLineEdit):
            target.setText(str(value if value is not None else ""))

    def _widget_value(self, widget: QtWidgets.QWidget, spec: FieldSpec) -> Any:
        target = self._value_widget(widget)
        if isinstance(target, OffsetEditor):
            return target.value()
        if isinstance(target, MultiAssetPicker):
            return target.value()
        if isinstance(target, QtWidgets.QSpinBox):
            return target.value()
        if isinstance(target, QtWidgets.QCheckBox):
            return target.isChecked()
        if isinstance(target, SearchableAssetCombo):
            return target.selected_value()
        if isinstance(target, QtWidgets.QComboBox):
            return target.currentData() if target.currentData() is not None else target.currentText()
        if isinstance(target, QtWidgets.QPlainTextEdit):
            return target.toPlainText()
        if isinstance(target, QtWidgets.QLineEdit):
            return target.text().strip() if spec.kind != "multiline" else target.text()
        return spec.default
