from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .color_widgets import ColorToleranceBarWidget
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
        self._confidence: int = 86
        self._asset_confidences: dict[str, int] = {}
        self._asset_regions: dict[str, list[int]] = {}
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

    def set_confidences(self, confidence: int, asset_confidences: dict[str, int] | None) -> None:
        self._confidence = max(1, min(100, int(confidence or 86)))
        self._asset_confidences = dict(asset_confidences or {})
        self._refresh_previews()

    def confidences(self) -> tuple[int, dict[str, int]]:
        return self._confidence, dict(self._asset_confidences)

    def set_asset_regions(self, values: Any) -> None:
        self._asset_regions = {}
        if isinstance(values, dict):
            for alias, reg in values.items():
                if isinstance(reg, (list, tuple)) and len(reg) >= 4:
                    try:
                        l, t, r, b = (int(x or 0) for x in reg[:4])
                        if r > l and b > t:
                            self._asset_regions[str(alias)] = [l, t, r, b]
                    except (TypeError, ValueError):
                        pass
        self._refresh_previews()

    def asset_regions(self) -> dict[str, list[int]]:
        return {alias: list(self._asset_regions[alias]) for alias in self.value() if alias in self._asset_regions}

    def _open_confidence_dialog(self, target_alias: str = "") -> None:
        repo = getattr(self.window(), "repository", None) or getattr(self.parent(), "repository", None)
        step_dict = getattr(self.window(), "original", None) or getattr(self.parent(), "original", None) or {}
        dialog = ImageSearchConfidenceDialog(
            repo,
            self.value(),
            self._confidence,
            self._asset_confidences,
            search_region=None,
            parent=self.window(),
            step=step_dict,
            asset_regions=self._asset_regions,
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._confidence = dialog.get_confidence()
            self._asset_confidences = dialog.get_asset_confidences()
            self._asset_regions = dialog.get_asset_regions()
            bounding = dialog.get_bounding_region()
            if bounding:
                editor = self.window()
                if hasattr(editor, "_set_field_value"):
                    for offset, val in enumerate(bounding):
                        editor._set_field_value("image_search", f"region.{offset}", val)
            self._refresh_previews()
            self.offset_edited.emit()

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
            card = QtWidgets.QFrame()
            card.setFixedWidth(150)
            card.setStyleSheet("QFrame { background: #171A22; border: 1px solid #2B354A; border-radius: 6px; padding: 2px; } QFrame:hover { border-color: #4D9FFF; }")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(4, 4, 4, 4)
            card_layout.setSpacing(3)
            image = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
            image.setFixedSize(142, 64)
            image.setPixmap(pixmap.scaled(image.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            image.setCursor(QtCore.Qt.PointingHandCursor)
            shown_name = QtGui.QFontMetrics(card.font()).elidedText(alias, QtCore.Qt.ElideMiddle, 140)
            name = QtWidgets.QLabel(shown_name, alignment=QtCore.Qt.AlignCenter)
            name.setToolTip(alias)
            cur_conf = self._asset_confidences.get(alias, self._confidence)
            reg_tag = " · 📐" if alias in self._asset_regions else ""
            conf_btn = QtWidgets.QPushButton(f"🎯 {cur_conf}%{reg_tag}")
            conf_btn.setStyleSheet("font-size: 8pt; font-weight: 700; padding: 2px 4px; border-radius: 4px; background: #20283C; color: #4D9FFF; border: 1px solid #334466;")
            reg_desc = f"지정 영역: {self._asset_regions[alias]}" if alias in self._asset_regions else "영역: 기본/전체"
            conf_btn.setToolTip(f"신뢰도: {cur_conf}%\n{reg_desc}\n클릭하여 신뢰도 및 검색 영역 설정")
            conf_btn.clicked.connect(lambda _, a=alias: self._open_confidence_dialog(a))
            card_layout.addWidget(image)
            card_layout.addWidget(name)
            card_layout.addWidget(conf_btn)
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
    "screen_condition": "화면 조건",
    "type_text": "텍스트 입력",
    "wait": "대기",
    "datetime_condition": "날짜·시간 조건",
    "browser_action": "브라우저 요소",
    "ocr": "OCR 텍스트 인식",
    "table_store": "테이블 값 저장",
    "table_copy": "테이블 복사",
    "table_paste": "테이블 붙여넣기",
    "table_excel_read": "Excel 읽기",
    "table_excel_write": "Excel 쓰기",
    "set_var": "변수 설정",
    "vault_get": "보안 보관함 값 불러오기",
    "calc_var": "변수 계산",
    "coord_mode": "좌표 기준 변경",
    "call_submacro": "서브매크로 호출",
    "flow_control": "반복 이동",
    "text_condition": "텍스트 조건",
    "run_program": "프로그램 실행",
    "terminate_program": "프로그램 종료",
    "remote_notify": "모바일 알림",
    "pixel_search": "픽셀 색상 서치",
    "ocr_tracking": "OCR 추적 인식",
    "multi_pixel_check": "다중 픽셀 체크",
    "wait_color": "색상 변화 대기",
    "color_ratio": "색상 비율 게이지",
}


ACTION_FIELDS: dict[str, list[FieldSpec]] = {
    "mouse_click": [
        FieldSpec(
            "coordinate_scope",
            "좌표 기준",
            "choice",
            "screen",
            options=choice(("화면 절대 좌표", "screen"), ("대상 프로그램 기준 · 위치가 변해도 유지", "client")),
            tooltip="프로그램 기준은 대상 창의 클라이언트 왼쪽 위를 0,0으로 저장합니다.",
        ),
        FieldSpec("window", "대상 창", "text", "", placeholder="프로그램 기준 좌표에서 자동 설정"),
        FieldSpec("window_exe", "대상 프로그램", "text", "", placeholder="예: notepad.exe"),
        FieldSpec("window_hwnd", "현재 창 핸들", "text", "", tooltip="현재 실행 중인 창을 우선 찾고, 창을 다시 열면 프로그램 이름으로 자동 재탐색합니다."),
        FieldSpec("x", "X 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("y", "Y 좌표", "int", 0, -100_000, 100_000),
        FieldSpec("button", "마우스 버튼", "choice", "Left", options=choice(("왼쪽", "Left"), ("오른쪽", "Right"), ("가운데", "Middle"), ("휠 위", "WheelUp"), ("휠 아래", "WheelDown"))),
        FieldSpec("count", "클릭 횟수", "int", 1, 1, 50),
        FieldSpec("action_type", "동작", "choice", "click", options=choice(("클릭", "click"), ("드래그", "drag"))),
        FieldSpec("drag_to.0", "드래그 끝 X", "int", 0, -100_000, 100_000, section="드래그"),
        FieldSpec("drag_to.1", "드래그 끝 Y", "int", 0, -100_000, 100_000, section="드래그"),
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
        FieldSpec("action_type", "클릭 동작", "choice", "click", options=choice(
            ("좌클릭 (기본)", "click"),
            ("우클릭", "right_click"),
            ("드래그", "drag"),
            ("마우스 누름 (Down 상태 유지)", "down"),
            ("마우스 뗌 (Up 상태)", "up"),
        ), tooltip="• 좌/우클릭: 누름 유지 시간 후 손을 뗍니다.\n• 마우스 누름: 버튼을 누른 상태로 유지합니다 (길게 누르기 시작).\n• 마우스 뗌: 누르고 있던 버튼을 뗍니다 (길게 누르기 종료).\n• 드래그: 끝 좌표까지 부드럽게 끕니다."),
        FieldSpec("press_duration", "누름 유지 시간", "duration", 60, 0, 10_000, tooltip="마우스를 누르고 있을 시간(ms)입니다. 앱플레이어/모바일 게임은 0ms 즉시 뗌 대신 50~80ms 이상 눌러야 게임 내 터치 입력이 100% 안정적으로 인식됩니다."),
        FieldSpec("drag_to.0", "드래그 끝 X", "int", 0, -100_000, 100_000, section="드래그·재시도"),
        FieldSpec("drag_to.1", "드래그 끝 Y", "int", 0, -100_000, 100_000, section="드래그·재시도"),
        FieldSpec("drag_click_after", "드래그 후 클릭", "bool", False, section="드래그·재시도"),
        FieldSpec("retry_count", "재시도 횟수", "int", 0, 0, 100, section="드래그·재시도"),
        FieldSpec("retry_delay", "재시도 간격", "duration", 80, 10, 60_000, section="드래그·재시도"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="드래그·재시도"),
    ],
    "image_search": [
        FieldSpec("asset", "검색 이미지", "asset", "", tooltip="화면에서 찾을 기준 이미지 에셋을 선택합니다."),
        FieldSpec(
            "assets",
            "멀티 검색 이미지",
            "assets",
            [],
            tooltip="2개 이상 선택하면 한 화면 캡처에서 모두 비교하고 정확도가 가장 높은 이미지를 선택합니다. 단일 검색 이미지는 우선 후보로 함께 포함됩니다.",
        ),
        FieldSpec("engine", "검색 엔진", "choice", "opencv", options=choice(("OpenCV · 정밀(권장)", "opencv"), ("AutoHotkey · 가볍고 빠름", "ahk")), tooltip="• OpenCV: 창 크기/배율 변화 자동 대응 및 정밀 매칭 (권장)\n• AutoHotkey: 가볍고 빠른 즉시 반응속도 (고정 창/앱플레이어 최적)"),
        FieldSpec("search_profile", "검색 품질", "choice", "fast", options=choice(("빠름 · 권장", "fast"), ("균형 · 색상 보강", "balanced"), ("정밀 · 70~150% 자동 배율", "precise")), tooltip="• 빠름: 35ms 주기 고속 탐색\n• 균형: 표준 매칭\n• 정밀: 70~150% 크기 변화 및 고해상도 지원"),
        FieldSpec("wait_condition", "대기 조건", "choice", "appear", options=choice(("이미지 나타남 대기", "appear"), ("이미지 사라짐 대기", "vanish")), tooltip="화면에서 이미지가 완전히 사라질 때까지 대기하려면 '이미지 사라짐 대기'를 선택하세요."),
        FieldSpec("search_mode", "탐색 방식", "choice", "first", options=choice(("첫 번째 발견 위치만", "first"), ("화면 내 발견된 모든 위치", "all")), tooltip="같은 이미지가 여러 개 있을 때 모든 위치를 처리하려면 '화면 내 발견된 모든 위치'를 선택하세요."),
        FieldSpec("max_matches", "최대 탐색 개수", "int", 20, 1, 100, section="다중 탐색 설정", tooltip="화면 내 발견된 모든 위치를 찾을 때 검출할 최대 개수입니다."),
        FieldSpec("all_action", "다중 발견 시 동작", "choice", "click_all", options=choice(("모든 위치 순차 클릭", "click_all"), ("좌표 목록 변수 저장", "store_list"), ("발견 개수만 카운트", "count_only")), section="다중 탐색 설정", tooltip="여러 위치가 발견되었을 때 순차 클릭할지, 좌표만 저장할지 선택합니다."),
        FieldSpec("match_condition", "성공 판정 조건", "choice", "at_least_1", options=choice(("1개 이상 발견 시 참 (기본)", "at_least_1"), ("모든 멀티 이미지 발견 시 참 (전체 일치)", "all_matched"), ("지정 개수 이상 발견 시 참", "at_least_n"), ("지정 개수와 일치 시 참", "exact_n")), section="다중 탐색 설정", tooltip="발견된 개수에 따라 노드의 성공(참)/실패(거짓) 분기를 결정합니다.\n• 1개 이상: 하나라도 발견되면 성공\n• 모든 멀티 이미지: 등록된 멀티 이미지(예: 3개)가 전부 발견되어야 성공(참)\n• 지정 개수 이상: 아래 '성공 기준 최소 개수' 이상 발견 시 성공"),
        FieldSpec("required_count", "성공 기준 최소 개수", "int", 0, 0, 100, section="다중 탐색 설정", tooltip="성공(참, 녹색선)으로 인정하기 위한 최소 발견 개수입니다.\n• 0: 기본값 (조건 설정에 따름)\n• 3: 3개 모두 발견되어야 참(성공), 2개 이하이면 거짓(실패)\n• 초보자 팁: 멀티 이미지 3개를 모두 찾아야 할 때 3을 입력하거나 위 조건에서 '모든 멀티 이미지 발견 시 참'을 선택하세요."),
        FieldSpec("store_count_var", "발견 개수 저장 변수", "text", "FoundCount", section="다중 탐색 설정", placeholder="예: FoundCount", tooltip="발견된 이미지 총 개수를 자동으로 저장할 변수명입니다. (기본: FoundCount)\n초보자도 별도 변수 설정 노드 없이 바로 후속 분기나 알림에서 이 변수값을 사용할 수 있습니다."),
        FieldSpec("click_delay", "각 위치 클릭 간격", "duration", 100, 0, 10_000, section="다중 탐색 설정", tooltip="모든 위치를 순차 클릭할 때 각 클릭 사이의 대기 시간(ms)입니다."),
        FieldSpec("variation", "색상 허용 오차", "int", 16, 0, 255, tooltip="낮을수록 더 정확하고 엄격하게 일치합니다. (0~255, 기본 16)"),
        FieldSpec("confidence", "일치 신뢰도", "int", 84, 50, 99, tooltip="OpenCV에서 사용하는 최소 일치율입니다. 텍스트/UI는 80~85 권장, 정밀 매칭은 90 이상 권장."),
        FieldSpec("timeout", "검색 제한 시간", "duration", 1200, 0, 600_000, tooltip="이미지가 나타날 때까지 대기할 최대 시간(ms)입니다. 0이면 1회만 검사합니다."),
        FieldSpec("poll_delay", "검색 반복 간격", "duration", 35, 10, 60_000, tooltip="대기 중 화면을 다시 캡처하여 비교하는 반복 간격(ms)입니다. 보통 35~50ms 권장."),
        FieldSpec(
            "capture_cache_ms",
            "연속 노드 화면 재사용",
            "duration",
            45,
            0,
            250,
            tooltip="같은 창·같은 범위를 매우 짧은 간격으로 다시 검색할 때 캡처를 재사용합니다. 대상 창이 바뀌면 자동 분리됩니다.",
            section="성능 최적화",
        ),
        FieldSpec("trans", "투명색", "text", "", placeholder="예: FFFFFF"),
        FieldSpec("region_mode", "범위 기준", "choice", "screen", options=choice(("전체 화면 · 모든 모니터", "screen"), ("창", "window"), ("클라이언트", "client")), section="검색 범위"),
        FieldSpec("region_coords", "좌표 해석", "choice", "screen", options=choice(("화면 절대 좌표", "screen"), ("대상 기준 상대 좌표", "relative")), section="검색 범위"),
        FieldSpec("region_window", "검색 대상 창", "text", "", section="검색 범위"),
        FieldSpec("region_window_exe", "검색 대상 프로그램", "text", "", section="검색 범위"),
        FieldSpec("region.0", "왼쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.1", "위", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.2", "오른쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.3", "아래", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec(
            "fallback_full_region",
            "지정 범위 실패 시 프로그램 전체 확장",
            "bool",
            False,
            section="검색 범위",
            tooltip="지정한 검색 범위에서 이미지를 찾지 못했을 때 프로그램 창 전체를 다시 한 번 검색합니다. 지정한 범위 안에서만 엄격하게 찾으려면 끄세요.",
        ),
        FieldSpec("region2.0", "추가 범위 왼쪽", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.1", "추가 범위 위", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.2", "추가 범위 오른쪽", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("region2.3", "추가 범위 아래", "int", 0, -100_000, 100_000, section="추가 검색 범위"),
        FieldSpec("click_enabled", "찾으면 클릭", "bool", True, section="검색 성공 후 동작"),
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
        FieldSpec("fail_click_enabled", "못 찾으면 실패 클릭 실행", "bool", False, section="검색 실패 시 동작", tooltip="이미지 검색에 실패했을 때 지정한 좌표/오프셋 위치를 대체 클릭합니다. (예: 팝업 닫기 버튼)"),
        FieldSpec("fail_click_offset.0", "실패 클릭 X", "int", 0, -100_000, 100_000, section="검색 실패 시 동작", tooltip="검색 실패 시 대체 클릭할 X 좌표 (창 기준)"),
        FieldSpec("fail_click_offset.1", "실패 클릭 Y", "int", 0, -100_000, 100_000, section="검색 실패 시 동작", tooltip="검색 실패 시 대체 클릭할 Y 좌표 (창 기준)"),
        FieldSpec("fail_click_mode", "실패 클릭 방식", "choice", "inactive", options=choice(("비활성 클릭 (권장)", "inactive"), ("활성 마우스 클릭", "active")), section="검색 실패 시 동작", tooltip="실패 시 비활성 메시지 전송(권장) 또는 실제 마우스 커서 이동 클릭"),
        FieldSpec("fail_click_count", "실패 클릭 횟수", "int", 1, 1, 10, section="검색 실패 시 동작", tooltip="실패 시 클릭할 횟수 (기본 1회)"),
        FieldSpec("abort_on_fail", "검색 실패 시 중단", "bool", False, section="완료 처리", tooltip="이미지 검색 실패 시 매크로 전체를 즉시 중단합니다."),
        FieldSpec(
            "repeat_on_success",
            "성공하면 같은 노드 재검색",
            "bool",
            False,
            tooltip="이미지가 계속 발견되는 동안 현재 노드를 반복하고, 처음 미탐지되면 실패선으로 이동합니다.",
            section="완료 처리",
        ),
        FieldSpec("repeat_on_success_delay", "재검색 간격", "duration", 50, 0, 60_000, section="완료 처리", tooltip="성공 재검색 시 다음 검색 전 대기 시간(ms)"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="완료 처리", tooltip="노드 실행 완료 후 다음 노드로 진행하기 전 대기 시간(ms)"),
    ],
    "screen_condition": [
        FieldSpec("asset", "조건 이미지", "asset", ""),
        FieldSpec("engine", "검색 엔진", "choice", "opencv", options=choice(("OpenCV · 크기 변화 대응", "opencv"), ("AutoHotkey · 단순 일치", "ahk"))),
        FieldSpec("search_profile", "검색 품질", "choice", "fast", options=choice(("빠름 · 권장", "fast"), ("균형", "balanced"), ("정밀 · 배율 대응", "precise"))),
        FieldSpec("confidence", "일치 신뢰도", "int", 86, 50, 99),
        FieldSpec("timeout", "확인 제한 시간", "duration", 800, 0, 600_000),
        FieldSpec("poll_delay", "반복 확인 간격", "duration", 40, 10, 60_000),
        FieldSpec("region_mode", "범위 기준", "choice", "client", options=choice(("대상 프로그램", "client"), ("전체 화면", "screen"), ("창 전체", "window")), section="검색 범위"),
        FieldSpec("region_coords", "좌표 해석", "choice", "relative", options=choice(("대상 기준", "relative"), ("화면 절대 좌표", "screen")), section="검색 범위"),
        FieldSpec("region_window", "대상 창", "text", "", section="검색 범위"),
        FieldSpec("region_window_exe", "대상 프로그램", "text", "", section="검색 범위"),
        FieldSpec("region.0", "왼쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.1", "위", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.2", "오른쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("region.3", "아래", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("fallback_full_region", "범위 실패 시 프로그램 전체 확인", "bool", False, section="검색 범위"),
        FieldSpec("sleep_after", "판정 후 대기", "duration", 0, 0, 600_000),
    ],
    "type_text": [
        FieldSpec("text", "입력 내용", "multiline", ""),
        FieldSpec("send_mode", "전송 방식", "choice", "input", options=choice(("빠른 입력", "input"), ("이벤트 입력", "event"), ("원문 입력", "raw"))),
        FieldSpec("mode", "대상 방식", "choice", "active", options=choice(("현재 활성 창", "active"), ("비활성 창", "inactive"))),
        FieldSpec("window", "비활성 대상 창", "text", ""),
        FieldSpec("delay", "입력 후 대기", "duration", 0, 0, 600_000),
    ],
    "wait": [FieldSpec("duration", "대기 시간", "duration", 500, 0, 3_600_000)],
    "datetime_condition": [
        FieldSpec("date_start_enabled", "시작 날짜 사용", "bool", False, section="날짜 제한"),
        FieldSpec("date_start", "시작 날짜", "date", "", section="날짜 제한"),
        FieldSpec("date_end_enabled", "종료 날짜 사용", "bool", False, section="날짜 제한"),
        FieldSpec("date_end", "종료 날짜", "date", "", section="날짜 제한"),
        FieldSpec("time_start", "시작 시간", "time", "00:00", section="시간 제한"),
        FieldSpec("time_end_enabled", "종료 시간 사용", "bool", False, section="시간 제한"),
        FieldSpec("time_end", "종료 시간", "time", "23:59", section="시간 제한"),
        FieldSpec("weekday_enabled", "요일 제한 사용", "bool", False, section="요일 제한", tooltip="끄면 매일 동작합니다."),
        FieldSpec("weekday_mon", "월요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_tue", "화요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_wed", "수요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_thu", "목요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_fri", "금요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_sat", "토요일", "bool", True, section="요일 상세"),
        FieldSpec("weekday_sun", "일요일", "bool", True, section="요일 상세"),
        FieldSpec("invert", "조건 결과 반전", "bool", False, section="판정 방식"),
        FieldSpec("wait_until", "조건을 만족할 때까지 대기", "bool", False, section="판정 방식"),
        FieldSpec("wait_timeout", "최대 대기 시간", "duration", 0, 0, 86_400_000, tooltip="0이면 제한 없이 대기합니다.", section="판정 방식"),
        FieldSpec("poll_delay", "대기 중 확인 간격", "duration", 500, 100, 60_000, section="판정 방식"),
    ],
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
    "pixel_search": [
        FieldSpec("color", "검색 색상 (HEX)", "text", "#FF0000", placeholder="예: #FF3A2B 또는 0xFF3A2B"),
        FieldSpec("tolerance", "색상 허용 오차", "color_tolerance", 10, 0, 255, tooltip="기준 색상 위치와 허용 오차(±Tolerance)를 시각적 색상 바에서 휠이나 드래그, 숫자로 조절합니다."),
        FieldSpec("region_mode", "범위 기준", "choice", "screen", options=choice(
            ("전체 화면 · 모든 모니터", "screen"),
            ("클라이언트 (앱플레이어 내부)", "client"),
            ("창 전체", "window"),
        ), section="검색 범위", tooltip="• 클라이언트: 앱플레이어나 게임 창의 내부 화면만을 기준으로 검색 (창 이동/해상도 변화에 안전)\n• 전체 화면: 모니터 바탕화면 절대 좌표 기준으로 검색"),
        FieldSpec("region_coords", "좌표 해석", "choice", "screen", options=choice(
            ("화면 절대 좌표", "screen"),
            ("대상 기준 상대 좌표", "relative"),
        ), section="검색 범위"),
        FieldSpec("region_window", "검색 대상 창", "text", "", section="검색 범위", placeholder="예: ahk_exe dnplayer.exe 또는 창 제목"),
        FieldSpec("region_window_exe", "검색 대상 프로그램", "text", "", section="검색 범위", placeholder="예: dnplayer.exe"),
        FieldSpec("search_region.0", "검색 왼쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("search_region.1", "검색 위", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("search_region.2", "검색 오른쪽", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("search_region.3", "검색 아래", "int", 0, -100_000, 100_000, section="검색 범위"),
        FieldSpec("match_condition", "일치 조건 (성공 판정)", "choice", "all_matched", options=choice(
            ("모든 색상 일치 시 참 (AND)", "all_matched"),
            ("1개 이상 일치 시 참 (OR)", "at_least_1"),
            ("N개 이상 일치 시 참", "at_least_n"),
            ("정확히 N개 일치 시 참", "exact_n"),
        ), section="조건 분기", tooltip="멀티 색상 서치 시 성공(참)으로 판정할 조건입니다."),
        FieldSpec("required_count", "요구 개수 (N개)", "int", 1, 1, 100, section="조건 분기", tooltip="N개 이상 또는 정확히 N개 일치 조건일 때 기준 개수입니다."),
        FieldSpec("action_on_found", "발견 시 동작", "choice", "click", options=choice(
            ("발견 위치 클릭", "click"),
            ("좌표만 변수 저장", "store_var"),
            ("조건 분기 (성공/실패)", "condition"),
        ), section="동작 설정"),
        FieldSpec("store_x_var", "X좌표 저장 변수", "text", "PixelFoundX", section="동작 설정"),
        FieldSpec("store_y_var", "Y좌표 저장 변수", "text", "PixelFoundY", section="동작 설정"),
        FieldSpec("store_count_var", "일치 개수 저장 변수", "text", "PixelMatchCount", section="동작 설정"),
        FieldSpec("timeout", "검색 제한 시간", "duration", 3000, 0, 600_000, section="타이밍"),
        FieldSpec("poll_delay", "반복 간격", "duration", 50, 10, 60_000, section="타이밍"),
        FieldSpec("click_offset_x", "클릭 오프셋 X", "int", 0, -10000, 10000, section="클릭 옵션", tooltip="발견된 픽셀 X좌표 기준 상대 클릭 오프셋 (px). 0이면 픽셀 위치 그대로 클릭"),
        FieldSpec("click_offset_y", "클릭 오프셋 Y", "int", 0, -10000, 10000, section="클릭 옵션", tooltip="발견된 픽셀 Y좌표 기준 상대 클릭 오프셋 (px). 0이면 픽셀 위치 그대로 클릭"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="클릭 옵션"),
    ],
    "ocr_tracking": [
        FieldSpec("tracking_mode", "추적 방식", "choice", "image", options=choice(
            ("이미지만 추적 (Image)", "image"),
            ("색상/픽셀만 추적 (Color)", "color"),
            ("혼합: 이미지 + 색상 둘 다 일치 (Both)", "both_and"),
            ("혼합: 둘 중 하나라도 발견 시 (Either)", "either_or"),
        ), tooltip="대상을 찾을 방식을 선택합니다. 이미지만, 색상만, 또는 둘 다 일치해야 할 때를 선택할 수 있습니다."),
        FieldSpec("tracking_asset", "추적 이미지", "asset", "", tooltip="화면에서 추적할 대상 이미지를 선택하세요.", section="이미지 추적"),
        FieldSpec("tracking_confidence", "이미지 일치 신뢰도", "int", 80, 50, 99, tooltip="추적 대상을 찾기 위한 최소 일치율입니다.", section="이미지 추적"),
        FieldSpec("tracking_color", "추적 색상 (HEX)", "text", "#FF0000", placeholder="예: #FF3A2B", section="색상 추적", tooltip="추적 대상에서 감지할 고유 색상입니다."),
        FieldSpec("tracking_tolerance", "색상 허용 오차", "int", 15, 0, 255, section="색상 추적", tooltip="색상 일치 허용 오차입니다."),
        FieldSpec("tracking_color_offset_x", "색상 오프셋 X", "int", 0, -2000, 2000, section="색상 추적", tooltip="이미지 기준 색상을 검사할 상대 X 오프셋 (단독 색상 추적 시 0)"),
        FieldSpec("tracking_color_offset_y", "색상 오프셋 Y", "int", 0, -2000, 2000, section="색상 추적", tooltip="이미지 기준 색상을 검사할 상대 Y 오프셋 (단독 색상 추적 시 0)"),
        FieldSpec("ocr_offset_x", "OCR 영역 오프셋 X", "int", 0, -2000, 2000, section="OCR 영역", tooltip="추적 기준점 대비 OCR 영역의 X 오프셋"),
        FieldSpec("ocr_offset_y", "OCR 영역 오프셋 Y", "int", 0, -2000, 2000, section="OCR 영역", tooltip="추적 기준점 대비 OCR 영역의 Y 오프셋"),
        FieldSpec("ocr_width", "OCR 영역 너비", "int", 200, 10, 5000, section="OCR 영역"),
        FieldSpec("ocr_height", "OCR 영역 높이", "int", 50, 10, 5000, section="OCR 영역"),
        FieldSpec("lang", "인식 언어", "text", "eng+kor", section="OCR 설정"),
        FieldSpec("engine_preference", "OCR 엔진", "choice", "auto", options=choice(
            ("자동 · 한국어 모델 우선", "auto"),
            ("PaddleOCR · 한국어 PP-OCRv5", "paddle"),
            ("Tesseract · 문장 보강", "tesseract"),
        ), section="OCR 설정"),
        FieldSpec("ocr_action", "OCR 동작", "choice", "extract", options=choice(
            ("텍스트 추출", "extract"),
            ("텍스트 찾기", "find_text"),
            ("텍스트 클릭", "find_click"),
            ("숫자 추출", "extract_number"),
        ), section="OCR 설정"),
        FieldSpec("find_text", "찾을 텍스트", "text", "", section="텍스트 찾기"),
        FieldSpec("match_mode", "매칭 모드", "choice", "contains", options=choice(
            ("포함", "contains"),
            ("완전 일치", "exact"),
            ("정규식", "regex"),
        ), section="텍스트 찾기"),
        FieldSpec("store_var", "결과 저장 변수", "text", "", section="결과 저장"),
        FieldSpec("timeout", "검색 제한 시간", "duration", 5000, 0, 600_000, section="타이밍"),
        FieldSpec("poll_delay", "반복 간격", "duration", 100, 10, 60_000, section="타이밍"),
    ],
    "multi_pixel_check": [
        FieldSpec("pixels", "픽셀 목록 (JSON/텍스트)", "multiline", "[]", tooltip='예: [{"x": 100, "y": 200, "color": "#FF0000"}]', placeholder='[{"x": 100, "y": 200, "color": "#FF0000"}]'),
        FieldSpec("match_policy", "일치 조건", "choice", "all", options=choice(
            ("모든 픽셀 일치 (AND) · 권장", "all"),
            ("하나 이상 일치 (OR)", "any"),
        )),
        FieldSpec("tolerance", "색상 허용 오차", "int", 10, 0, 255, tooltip="낮을수록 더 정확하고 엄격하게 일치합니다."),
        FieldSpec("coord_mode", "좌표 기준", "choice", "Screen", options=choice(
            ("전체 화면 (Screen)", "Screen"),
            ("대상 창 내부 (Client)", "Window"),
        ), tooltip="창이 이동해도 창 내부 핀 위치를 유지하려면 '대상 창 내부'를 선택하세요.", section="동작 설정"),
        FieldSpec("action_on_found", "일치 시 동작", "choice", "branch", options=choice(
            ("성공 분기 (흐름 제어)", "branch"),
            ("첫 번째 픽셀 클릭", "click_first"),
            ("변수 저장 (일치 여부)", "store_var"),
        ), section="동작 설정"),
        FieldSpec("store_var", "결과 저장 변수", "text", "MultiPixelMatch", section="동작 설정"),
        FieldSpec("timeout", "검색 제한 시간", "duration", 1000, 0, 600_000, section="타이밍"),
        FieldSpec("poll_delay", "반복 간격", "duration", 50, 10, 60_000, section="타이밍"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="타이밍"),
    ],
    "wait_color": [
        FieldSpec("x", "X 좌표", "int", 0, -100_000, 100_000, section="좌표 설정"),
        FieldSpec("y", "Y 좌표", "int", 0, -100_000, 100_000, section="좌표 설정"),
        FieldSpec("target_color", "목표 색상 (HEX)", "text", "#FF0000", placeholder="예: #FF3A2B"),
        FieldSpec("condition", "대기 조건", "choice", "become", options=choice(
            ("목표 색상으로 변할 때까지 대기", "become"),
            ("현재 색상에서 벗어날 때까지 대기", "leave"),
        )),
        FieldSpec("tolerance", "색상 허용 오차", "int", 10, 0, 255, tooltip="낮을수록 더 엄격하게 색상을 비교합니다."),
        FieldSpec("search_region.0", "영역 왼쪽", "int", 0, -100_000, 100_000, section="대기 영역 (선택)", tooltip="0이면 위의 단일 (X, Y) 좌표를 검사합니다. 영역을 지정하면 영역 전체에서 해당 색상이 나타나거나 사라질 때까지 대기합니다."),
        FieldSpec("search_region.1", "영역 위", "int", 0, -100_000, 100_000, section="대기 영역 (선택)"),
        FieldSpec("search_region.2", "영역 오른쪽", "int", 0, -100_000, 100_000, section="대기 영역 (선택)"),
        FieldSpec("search_region.3", "영역 아래", "int", 0, -100_000, 100_000, section="대기 영역 (선택)"),
        FieldSpec("timeout", "최대 대기 시간", "duration", 5000, 0, 600_000, section="타이밍"),
        FieldSpec("poll_delay", "확인 주기", "duration", 50, 10, 60_000, section="타이밍"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="타이밍"),
    ],
    "color_ratio": [
        FieldSpec("target_color", "검출 색상 (HEX)", "text", "#FF0000", placeholder="예: #FF3A2B"),
        FieldSpec("tolerance", "색상 허용 오차", "int", 15, 0, 255),
        FieldSpec("search_region.0", "영역 왼쪽", "int", 0, -100_000, 100_000, section="게이지 영역"),
        FieldSpec("search_region.1", "영역 위", "int", 0, -100_000, 100_000, section="게이지 영역"),
        FieldSpec("search_region.2", "영역 오른쪽", "int", 0, -100_000, 100_000, section="게이지 영역"),
        FieldSpec("search_region.3", "영역 아래", "int", 0, -100_000, 100_000, section="게이지 영역"),
        FieldSpec("store_ratio_var", "비율(%) 저장 변수", "text", "GaugePercent", section="결과 변수"),
        FieldSpec("store_count_var", "픽셀 수 저장 변수", "text", "PixelCount", section="결과 변수"),
        FieldSpec("condition_operator", "조건 비교 방식", "choice", "none", options=choice(
            ("조건 검사 안 함 (변수만 저장)", "none"),
            ("비율 <= 기준값 (%)", "lte"),
            ("비율 >= 기준값 (%)", "gte"),
            ("비율 < 기준값 (%)", "lt"),
            ("비율 > 기준값 (%)", "gt"),
            ("비율 == 기준값 (%)", "eq"),
        ), section="조건 분기"),
        FieldSpec("condition_value", "비교 기준값 (%)", "int", 30, 0, 100, section="조건 분기"),
        FieldSpec("sleep_after", "완료 후 대기", "duration", 0, 0, 600_000, section="타이밍"),
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
    "vault_get": [
        FieldSpec("name", "저장할 변수 이름", "text", "secret_value"),
        FieldSpec("secret", "보관함 항목 이름", "text", "", placeholder="설정 > 보안 보관함에 저장한 이름"),
    ],
    "calc_var": [
        FieldSpec("name", "결과 변수", "text", "value"),
        FieldSpec("expr", "수식", "text", "", placeholder="예: price * 1.1"),
        FieldSpec("op", "간단 연산", "choice", "", options=choice(("사용 안 함", ""), ("더하기", "+"), ("빼기", "-"), ("곱하기", "*"), ("나누기", "/"))),
        FieldSpec("value", "연산 값", "text", ""),
    ],
    "coord_mode": [FieldSpec("mode", "좌표 기준", "choice", "Screen", options=choice(("전체 화면", "Screen"), ("창", "Window"), ("클라이언트", "Client")))],
    "call_submacro": [
        FieldSpec("macro", "호출할 매크로", "macro", ""),
        FieldSpec(
            "inputs_text",
            "입력 변수 전달",
            "multiline",
            "",
            placeholder="예: id=$account_id\npassword=$account_password",
            tooltip="자식변수=값 또는 자식변수=$부모변수 형식으로 한 줄씩 입력합니다.",
            section="입출력",
        ),
        FieldSpec(
            "outputs_text",
            "출력 변수 반환",
            "multiline",
            "",
            placeholder="예: login_message=$child_message",
            tooltip="부모변수=$자식변수 형식으로 한 줄씩 입력합니다.",
            section="입출력",
        ),
        FieldSpec(
            "result_var",
            "성공 여부 변수",
            "text",
            "",
            placeholder="예: login_success",
            tooltip="서브플로우 성공 시 1, 실패 시 0이 저장됩니다.",
            section="입출력",
        ),
    ],
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

    def __init__(self, parent=None, ignored_hwnds: set[int] | None = None, hint_text: str | None = None) -> None:
        super().__init__(parent)
        self.window_token = ""
        self.exe_name = ""
        self.window_hwnd = 0
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
        hint = QtWidgets.QLabel(hint_text or "대상 창을 클릭하세요  ·  Esc 취소", self)
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
                self.window_hwnd = root
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
            self.window_hwnd = 0
        if self.window_token:
            self.accept()
        else:
            self.reject()

    def selected_screen_point(self) -> QtCore.QPoint:
        return QtCore.QPoint(self._point)

    def selected_client_point(self) -> QtCore.QPoint | None:
        if not self.window_hwnd:
            return None
        try:
            point = wintypes.POINT(self._point.x(), self._point.y())
            if ctypes.windll.user32.ScreenToClient(self.window_hwnd, ctypes.byref(point)):
                return QtCore.QPoint(int(point.x), int(point.y))
        except Exception:
            pass
        return None

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

    def __init__(
        self,
        parent=None,
        single_click: bool = False,
        reference_point: QtCore.QPoint | None = None,
        reference_label: str = "",
        click_label: str = "",
        instruction_step1: str = "",
        instruction_step2: str = "",
    ) -> None:
        super().__init__(parent)
        self.single_click = single_click
        self.reference_point: QtCore.QPoint | None = reference_point
        self.reference_label = reference_label or "1 · 이미지 기준점"
        self.click_label = click_label or "2 · 실제 클릭점"
        self.instruction_step1 = instruction_step1
        self.instruction_step2 = instruction_step2
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
        if self.click_point is None:
            return [0, 0]
        if self.reference_point is None:
            return [self.click_point.x(), self.click_point.y()]
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
        if self.single_click:
            self.click_point = point
            if self.reference_point is None:
                try:
                    import ctypes, ctypes.wintypes as wt
                    user32 = ctypes.windll.user32
                    pt = wt.POINT(point.x(), point.y())
                    hwnd = user32.WindowFromPoint(pt)
                    root = user32.GetAncestor(hwnd, 2) or hwnd
                    origin = wt.POINT(0, 0)
                    user32.ClientToScreen(root, ctypes.byref(origin))
                    self.reference_point = QtCore.QPoint(origin.x, origin.y)
                except Exception:
                    self.reference_point = QtCore.QPoint(0, 0)
            self.accept()
            event.accept()
            return
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
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self.reference_point is not None and self.click_point is None:
                self.click_point = self.cursor_point
                self.accept()
                event.accept()
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
        if self.single_click:
            self._draw_marker(painter, cursor, QtGui.QColor("#38E7FF"), "🎯 클릭 위치")
            instruction = self.instruction_step1 or "🎯 클릭할 위치를 마우스 좌클릭하세요   ·   Esc 취소"
        elif reference is not None:
            painter.setPen(QtGui.QPen(QtGui.QColor("#46C2C7"), 2, QtCore.Qt.DashLine))
            painter.drawLine(reference, cursor)
            self._draw_marker(painter, reference, QtGui.QColor("#2997FF"), self.reference_label)
            self._draw_marker(painter, cursor, QtGui.QColor("#46C2C7"), self.click_label)
            delta = self.cursor_point - self.reference_point
            if self.instruction_step2:
                instruction = self.instruction_step2.replace("{dx}", f"{delta.x():+d}").replace("{dy}", f"{delta.y():+d}")
            else:
                instruction = f"2/2  오프셋 클릭점을 클릭하세요   ·   X {delta.x():+d}px  Y {delta.y():+d}px   ·   Esc 취소"
        else:
            self._draw_marker(painter, cursor, QtGui.QColor("#2997FF"), self.reference_label)
            instruction = self.instruction_step1 or "1/2  검색 이미지에서 기준이 될 위치를 클릭하세요   ·   Esc 취소"
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
        pick_single = QtWidgets.QPushButton("🎯 1점 좌표 지정")
        pick_single.setToolTip("화면의 목표 위치를 마우스 좌클릭 1번으로 찍어 오프셋 좌표를 즉시 지정합니다.")
        pick_single.clicked.connect(self._pick_single_from_screen)
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
        value_row.addWidget(pick_single)
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

    def _pick_single_from_screen(self) -> None:
        picker = OffsetPointPickerDialog(self.window(), single_click=True)
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


class OCRFilterTuningDialog(QtWidgets.QDialog):
    """Interactive preview dialog for tuning OCR image preprocessing filters."""

    def __init__(self, sample_image: QtGui.QImage | QtGui.QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎨 OCR 필터 튜닝 & 실시간 텍스트 프리뷰")
        self.resize(800, 520)
        self.setStyleSheet("QDialog { background: #11151F; color: #E2E8F0; }")

        if isinstance(sample_image, QtGui.QPixmap):
            self._original_pixmap = sample_image
        else:
            self._original_pixmap = QtGui.QPixmap.fromImage(sample_image)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Left panel: controls
        ctrl_box = QtWidgets.QGroupBox("전처리 필터 조절")
        ctrl_box.setStyleSheet("QGroupBox { font-weight: 700; color: #8A98B0; border: 1px solid #2B354A; border-radius: 8px; margin-top: 8px; padding: 10px; }")
        ctrl_layout = QtWidgets.QVBoxLayout(ctrl_box)
        ctrl_layout.setSpacing(10)

        self.chk_grayscale = QtWidgets.QCheckBox("흑백(Grayscale) 변환")
        self.chk_grayscale.setChecked(True)
        self.chk_grayscale.toggled.connect(self._apply_filters)
        ctrl_layout.addWidget(self.chk_grayscale)

        self.chk_otsu = QtWidgets.QCheckBox("OTSU 자동 이진화")
        self.chk_otsu.setChecked(True)
        self.chk_otsu.toggled.connect(self._on_otsu_toggled)
        ctrl_layout.addWidget(self.chk_otsu)

        lbl_thresh = QtWidgets.QLabel("수동 이진화 임계값 (0~255):")
        ctrl_layout.addWidget(lbl_thresh)
        thresh_row = QtWidgets.QHBoxLayout()
        self.slider_thresh = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_thresh.setRange(0, 255)
        self.slider_thresh.setValue(128)
        self.slider_thresh.setEnabled(False)
        self.slider_thresh.valueChanged.connect(self._apply_filters)
        self.lbl_thresh_val = QtWidgets.QLabel("128")
        self.slider_thresh.valueChanged.connect(lambda v: self.lbl_thresh_val.setText(str(v)))
        thresh_row.addWidget(self.slider_thresh)
        thresh_row.addWidget(self.lbl_thresh_val)
        ctrl_layout.addLayout(thresh_row)

        self.chk_invert = QtWidgets.QCheckBox("색상 반전 (어두운 배경 글자용)")
        self.chk_invert.setChecked(False)
        self.chk_invert.toggled.connect(self._apply_filters)
        ctrl_layout.addWidget(self.chk_invert)

        lbl_contrast = QtWidgets.QLabel("대비 강화 (Contrast 1.0x ~ 3.0x):")
        ctrl_layout.addWidget(lbl_contrast)
        contrast_row = QtWidgets.QHBoxLayout()
        self.slider_contrast = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_contrast.setRange(10, 30)
        self.slider_contrast.setValue(15)
        self.slider_contrast.valueChanged.connect(self._apply_filters)
        self.lbl_contrast_val = QtWidgets.QLabel("1.5x")
        self.slider_contrast.valueChanged.connect(lambda v: self.lbl_contrast_val.setText(f"{v/10:.1f}x"))
        contrast_row.addWidget(self.slider_contrast)
        contrast_row.addWidget(self.lbl_contrast_val)
        ctrl_layout.addLayout(contrast_row)

        lbl_morph = QtWidgets.QLabel("글자 굵기/팽창 (-2~+2):")
        ctrl_layout.addWidget(lbl_morph)
        morph_row = QtWidgets.QHBoxLayout()
        self.slider_morph = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider_morph.setRange(-2, 2)
        self.slider_morph.setValue(0)
        self.slider_morph.valueChanged.connect(self._apply_filters)
        self.lbl_morph_val = QtWidgets.QLabel("0")
        self.slider_morph.valueChanged.connect(lambda v: self.lbl_morph_val.setText(str(v)))
        morph_row.addWidget(self.slider_morph)
        morph_row.addWidget(self.lbl_morph_val)
        ctrl_layout.addLayout(morph_row)

        ctrl_layout.addStretch(1)

        btn_save = QtWidgets.QPushButton("✔ 이 필터 설정 적용")
        btn_save.setStyleSheet("background: #7C6CFF; color: white; font-weight: 700; padding: 8px; border-radius: 6px;")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QtWidgets.QPushButton("닫기")
        btn_cancel.clicked.connect(self.reject)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        ctrl_layout.addLayout(btn_row)

        layout.addWidget(ctrl_box, 1)

        # Right panel: image previews + live OCR text
        right_box = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        lbl_orig = QtWidgets.QLabel("원본 이미지:")
        lbl_orig.setStyleSheet("font-weight: 700; color: #8A98B0;")
        right_layout.addWidget(lbl_orig)
        self.view_orig = QtWidgets.QLabel()
        self.view_orig.setFixedHeight(110)
        self.view_orig.setAlignment(QtCore.Qt.AlignCenter)
        self.view_orig.setStyleSheet("background: #171A22; border: 1px solid #2B354A; border-radius: 6px;")
        self.view_orig.setPixmap(self._original_pixmap.scaled(340, 100, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        right_layout.addWidget(self.view_orig)

        lbl_proc = QtWidgets.QLabel("필터 적용 미리보기:")
        lbl_proc.setStyleSheet("font-weight: 700; color: #8A98B0;")
        right_layout.addWidget(lbl_proc)
        self.view_proc = QtWidgets.QLabel()
        self.view_proc.setFixedHeight(110)
        self.view_proc.setAlignment(QtCore.Qt.AlignCenter)
        self.view_proc.setStyleSheet("background: #171A22; border: 1px solid #2B354A; border-radius: 6px;")
        right_layout.addWidget(self.view_proc)

        lbl_ocr = QtWidgets.QLabel("실시간 OCR 인식 결과:")
        lbl_ocr.setStyleSheet("font-weight: 700; color: #8A98B0;")
        right_layout.addWidget(lbl_ocr)
        self.txt_ocr = QtWidgets.QPlainTextEdit()
        self.txt_ocr.setReadOnly(True)
        self.txt_ocr.setFixedHeight(85)
        self.txt_ocr.setStyleSheet("background: #171A22; border: 1px solid #2B354A; border-radius: 6px; color: #32E6D0; font-size: 10pt;")
        right_layout.addWidget(self.txt_ocr)

        layout.addWidget(right_box, 1)

        self._apply_filters()

    def _on_otsu_toggled(self, checked: bool) -> None:
        self.slider_thresh.setEnabled(not checked)
        self._apply_filters()

    def _apply_filters(self) -> None:
        try:
            from io import BytesIO
            import numpy as np
            from PIL import Image, ImageEnhance, ImageOps

            buffer = QtCore.QBuffer()
            buffer.open(QtCore.QIODevice.ReadWrite)
            self._original_pixmap.save(buffer, "PNG")
            pil_img = Image.open(BytesIO(buffer.data()))

            if self.chk_grayscale.isChecked():
                pil_img = pil_img.convert("L")
            else:
                pil_img = pil_img.convert("RGB")

            if self.chk_invert.isChecked():
                pil_img = ImageOps.invert(pil_img)

            contrast_val = self.slider_contrast.value() / 10.0
            if contrast_val != 1.0:
                enhancer = ImageEnhance.Contrast(pil_img)
                pil_img = enhancer.enhance(contrast_val)

            if self.chk_otsu.isChecked():
                arr = np.array(pil_img)
                if len(arr.shape) == 2:
                    thresh = int(np.mean(arr))
                    arr = np.where(arr > thresh, 255, 0).astype(np.uint8)
                    pil_img = Image.fromarray(arr)
            else:
                thresh_val = self.slider_thresh.value()
                arr = np.array(pil_img)
                if len(arr.shape) == 2:
                    arr = np.where(arr > thresh_val, 255, 0).astype(np.uint8)
                    pil_img = Image.fromarray(arr)

            morph_val = self.slider_morph.value()
            if morph_val != 0:
                from PIL import ImageFilter
                if morph_val > 0:
                    for _ in range(morph_val):
                        pil_img = pil_img.filter(ImageFilter.MaxFilter(3))
                else:
                    for _ in range(abs(morph_val)):
                        pil_img = pil_img.filter(ImageFilter.MinFilter(3))

            out_io = BytesIO()
            pil_img.save(out_io, format="PNG")
            out_pixmap = QtGui.QPixmap()
            out_pixmap.loadFromData(out_io.getvalue())
            self.view_proc.setPixmap(out_pixmap.scaled(340, 100, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

            # Try quick pytesseract OCR
            try:
                import pytesseract
                text = pytesseract.image_to_string(pil_img, lang="kor+eng")
                self.txt_ocr.setPlainText(text.strip() or "(추출된 글자 없음)")
            except Exception:
                self.txt_ocr.setPlainText("(필터 적용 성공 · 텍스트 프리뷰 준비 완료)")
        except Exception as e:
            self.txt_ocr.setPlainText(f"필터 적용 안내: {e}")


class ImageSearchConfidenceDialog(QtWidgets.QDialog):
    """Dialog for adjusting image search confidence (1~100) individually and in batch, with image editor integration."""

    def __init__(
        self,
        repository: MacroRepository,
        aliases: list[str],
        confidence: int = 86,
        asset_confidences: dict[str, int] | None = None,
        search_region: list[int] | None = None,
        parent=None,
        step: dict[str, Any] | None = None,
        asset_regions: dict[str, list[int]] | None = None,
    ) -> None:
        if isinstance(search_region, QtWidgets.QWidget) and parent is None:
            parent = search_region
            search_region = None
        super().__init__(parent)
        self.repository = repository
        self.step = dict(step or {})
        self.aliases = [str(a) for a in aliases if str(a).strip()]
        self._confidence = max(1, min(100, int(confidence or 86)))
        self._asset_confidences = dict(asset_confidences or {})

        def _is_valid_reg(r: Any) -> bool:
            if isinstance(r, (list, tuple)) and len(r) >= 4:
                try:
                    return int(r[2]) > int(r[0]) and int(r[3]) > int(r[1])
                except (TypeError, ValueError):
                    return False
            return False

        if _is_valid_reg(search_region):
            self._search_region = [int(x) for x in search_region[:4]]
        else:
            self._search_region = None

        if self._search_region is None and self.step:
            st_reg = self.step.get("region") or self.step.get("search_region")
            if not _is_valid_reg(st_reg) and isinstance(self.step.get("regions"), list) and self.step["regions"]:
                st_reg = self.step["regions"][0]
            if _is_valid_reg(st_reg):
                self._search_region = [int(x) for x in st_reg[:4]]

        self._asset_regions: dict[str, list[int]] = {}
        input_asset_regs = dict(asset_regions or {})
        if not input_asset_regs and self.step and isinstance(self.step.get("asset_regions"), dict):
            input_asset_regs = dict(self.step["asset_regions"])
        for a, r in input_asset_regs.items():
            if _is_valid_reg(r):
                self._asset_regions[str(a)] = [int(x) for x in r[:4]]

        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowTitle("이미지 서치 · 신뢰도 및 검색 영역 설정")
        self.resize(880 if len(self.aliases) > 1 else 700, 560 if len(self.aliases) > 1 else 480)
        self.setStyleSheet("QDialog { background: #11151F; color: #E2E8F0; }")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hdr_layout = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("🎯 이미지 일치 신뢰도 및 검색 영역 설정 (1~100)")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: 800; color: #FFFFFF;")
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addStretch(1)
        layout.addLayout(hdr_layout)

        self.conf_sliders: dict[str, tuple[QtWidgets.QSlider, QtWidgets.QSpinBox]] = {}
        self.region_widgets: dict[str, tuple[QtWidgets.QPushButton, QtWidgets.QPushButton]] = {}

        if len(self.aliases) <= 1:
            alias = self.aliases[0] if self.aliases else ""
            card = QtWidgets.QGroupBox("검색 이미지" if not alias else f"검색 이미지 · {alias}")
            card.setStyleSheet("QGroupBox { font-weight: 700; color: #8A98B0; border: 1px solid #2B354A; border-radius: 8px; margin-top: 8px; padding: 12px; }")
            card_layout = QtWidgets.QVBoxLayout(card)

            preview_lbl = QtWidgets.QLabel()
            preview_lbl.setAlignment(QtCore.Qt.AlignCenter)
            preview_lbl.setFixedHeight(150)
            preview_lbl.setStyleSheet("background: #171A22; border: 1px solid #2A3040; border-radius: 6px;")
            p = self.repository.asset_path(alias) if (alias and self.repository) else None
            pixmap = QtGui.QPixmap(str(p)) if (p and Path(p).is_file()) else None
            if pixmap and not pixmap.isNull():
                preview_lbl.setPixmap(pixmap.scaled(280, 140, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            else:
                preview_lbl.setText("(이미지 미리보기 없음)")
            card_layout.addWidget(preview_lbl)

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("신뢰도(%):"))
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(1, 100)
            slider.setValue(self._confidence)
            spin = QtWidgets.QSpinBox()
            spin.setRange(1, 100)
            spin.setValue(self._confidence)
            spin.setSuffix("%")
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(spin)
            card_layout.addLayout(row)

            if alias:
                self.conf_sliders[alias] = (slider, spin)
            self.single_slider = (slider, spin)

            if alias:
                btn_edit_img = QtWidgets.QPushButton("🎨 이미지 상세 편집기 열기 (자르기/누끼/회전)")
                btn_edit_img.setStyleSheet("background: #1E2330; border: 1px solid #3B455C; color: #E2E8F0; padding: 6px; border-radius: 6px; font-weight: 600;")
                btn_edit_img.clicked.connect(lambda: self._open_image_editor(alias))
                card_layout.addWidget(btn_edit_img)

            reg_text = f"[{self._search_region[0]}, {self._search_region[1]}, {self._search_region[2]}, {self._search_region[3]}]" if self._search_region else "전체/미지정"
            reg_btn_row = QtWidgets.QHBoxLayout()
            self.btn_pick_region = QtWidgets.QPushButton(f"📐 검색 영역 지정 (클라이언트 영역 드래그) · 현재: {reg_text}")
            self.btn_pick_region.setStyleSheet("background: #1B293A; border: 1px solid #2B5A8A; color: #70C5FF; padding: 6px; border-radius: 6px; font-weight: 600;")
            self.btn_pick_region.setToolTip("화면에서 원하는 범위를 마우스로 드래그하면 대상 창(앱플레이어 등) 기준 클라이언트 상대 좌표로 자동 변환 저장됩니다.")
            self.btn_pick_region.clicked.connect(self._pick_search_region)
            reg_btn_row.addWidget(self.btn_pick_region, 1)

            self.btn_quick_save = QtWidgets.QPushButton("⚡ 즉시 저장")
            self.btn_quick_save.setStyleSheet("background: #238636; color: white; padding: 6px 12px; border-radius: 6px; font-weight: 700;")
            self.btn_quick_save.setToolTip("현재 지정된 영역과 신뢰도를 즉시 노드에 저장하고 창을 닫습니다.")
            self.btn_quick_save.clicked.connect(self.accept)
            reg_btn_row.addWidget(self.btn_quick_save)
            card_layout.addLayout(reg_btn_row)

            self.notice_lbl = QtWidgets.QLabel()
            self.notice_lbl.setStyleSheet("background: #0D2818; border: 1px solid #1B5E38; color: #4ADE80; padding: 6px 10px; border-radius: 6px; font-size: 11px; font-weight: 700;")
            self.notice_lbl.setVisible(False)
            card_layout.addWidget(self.notice_lbl)

            layout.addWidget(card, 1)
        else:
            batch_box = QtWidgets.QGroupBox("⚡ 전체 일괄 신뢰도 설정")
            batch_box.setStyleSheet("QGroupBox { font-weight: 700; color: #35C89A; border: 1px solid #2A4A3E; border-radius: 8px; margin-top: 6px; padding: 10px; }")
            batch_layout = QtWidgets.QHBoxLayout(batch_box)
            batch_lbl = QtWidgets.QLabel("일괄 적용 값:")
            self.batch_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.batch_slider.setRange(1, 100)
            self.batch_slider.setValue(self._confidence)
            self.batch_spin = QtWidgets.QSpinBox()
            self.batch_spin.setRange(1, 100)
            self.batch_spin.setValue(self._confidence)
            self.batch_spin.setSuffix("%")
            self.batch_slider.valueChanged.connect(self.batch_spin.setValue)
            self.batch_spin.valueChanged.connect(self.batch_slider.setValue)
            btn_apply_all = QtWidgets.QPushButton("한번에 전체 적용")
            btn_apply_all.setStyleSheet("background: #238636; color: white; font-weight: 700; padding: 6px 14px; border-radius: 6px;")
            btn_apply_all.clicked.connect(self._apply_all_confidence)

            batch_layout.addWidget(batch_lbl)
            batch_layout.addWidget(self.batch_slider, 1)
            batch_layout.addWidget(self.batch_spin)
            batch_layout.addWidget(btn_apply_all)
            layout.addWidget(batch_box)

            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { border: 1px solid #2A3040; border-radius: 8px; background: #131722; }")
            container = QtWidgets.QWidget()
            scroll_layout = QtWidgets.QVBoxLayout(container)
            scroll_layout.setContentsMargins(8, 8, 8, 8)
            scroll_layout.setSpacing(8)

            for alias in self.aliases:
                cur_val = self._asset_confidences.get(alias, self._confidence)
                card = QtWidgets.QFrame()
                card.setStyleSheet("QFrame { background: #171A22; border: 1px solid #2B354A; border-radius: 6px; padding: 6px; }")
                crow = QtWidgets.QHBoxLayout(card)
                crow.setContentsMargins(6, 4, 6, 4)
                crow.setSpacing(10)

                thumb = QtWidgets.QLabel()
                thumb.setFixedSize(70, 50)
                thumb.setAlignment(QtCore.Qt.AlignCenter)
                thumb.setStyleSheet("background: #0E1118; border: 1px solid #202636; border-radius: 4px;")
                path = self.repository.asset_path(alias) if self.repository else None
                pix = QtGui.QPixmap(str(path)) if (path and Path(path).is_file()) else None
                if pix and not pix.isNull():
                    thumb.setPixmap(pix.scaled(66, 46, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                else:
                    thumb.setText("No Img")
                crow.addWidget(thumb)

                name_lbl = QtWidgets.QLabel(alias)
                name_lbl.setFixedWidth(130)
                name_lbl.setStyleSheet("font-weight: 600; color: #E2E8F0;")
                name_lbl.setToolTip(alias)
                crow.addWidget(name_lbl)

                islider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                islider.setRange(1, 100)
                islider.setValue(cur_val)
                ispin = QtWidgets.QSpinBox()
                ispin.setRange(1, 100)
                ispin.setValue(cur_val)
                ispin.setSuffix("%")
                islider.valueChanged.connect(ispin.setValue)
                ispin.valueChanged.connect(islider.setValue)
                crow.addWidget(islider, 1)
                crow.addWidget(ispin)

                self.conf_sliders[alias] = (islider, ispin)

                btn_reg = QtWidgets.QPushButton()
                cur_reg = self._asset_regions.get(alias)
                self._update_asset_region_btn_text(btn_reg, cur_reg)
                btn_reg.setToolTip(
                    "클릭: 이 이미지 전용 검색 영역을 화면 드래그로 지정합니다.\n"
                    "우클릭: 지정 영역을 해제하고 기본/전체 영역으로 복원합니다."
                )
                btn_reg.clicked.connect(lambda _, a=alias, b=btn_reg: self._pick_asset_search_region(a, b))
                btn_reg.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                btn_reg.customContextMenuRequested.connect(lambda _pos, a=alias, b=btn_reg: self._clear_asset_search_region(a, b))
                crow.addWidget(btn_reg)

                btn_clear_reg = QtWidgets.QPushButton("✕")
                btn_clear_reg.setFixedWidth(24)
                btn_clear_reg.setStyleSheet(
                    "background: #251B22; border: 1px solid #5C2B38; color: #FF7088; "
                    "font-weight: 700; border-radius: 4px; padding: 2px;"
                )
                btn_clear_reg.setToolTip("지정 영역 초기화 (기본/전체 영역 사용)")
                btn_clear_reg.setVisible(bool(cur_reg))
                btn_clear_reg.clicked.connect(lambda _, a=alias, b=btn_reg, c=btn_clear_reg: self._do_clear_asset_region(a, b, c))
                crow.addWidget(btn_clear_reg)

                self.region_widgets[alias] = (btn_reg, btn_clear_reg)

                btn_edit = QtWidgets.QPushButton("✏ 상세 편집")
                btn_edit.setStyleSheet("background: #1E2330; border: 1px solid #3B455C; color: #E2E8F0; padding: 4px 8px; border-radius: 4px;")
                btn_edit.clicked.connect(lambda _, a=alias: self._open_image_editor(a))
                crow.addWidget(btn_edit)

                scroll_layout.addWidget(card)

            scroll_layout.addStretch(1)
            scroll.setWidget(container)
            layout.addWidget(scroll, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_test_visual = QtWidgets.QPushButton("🔍 현재 화면으로 영역 시각화 및 실시간 테스트")
        btn_test_visual.setStyleSheet(
            "background: #065F46; border: 1px solid #059669; color: #A7F3D0; "
            "font-weight: 700; padding: 8px 16px; border-radius: 6px;"
        )
        btn_test_visual.setToolTip("현재 실행 중인 게임 화면을 캡처하여 각 이미지의 지정 영역이 올바른 위치에 있는지 시각적 사각형 박스로 확인하고 테스트합니다.")
        btn_test_visual.clicked.connect(self._open_visual_test)
        btn_row.addWidget(btn_test_visual)
        btn_row.addStretch(1)
        btn_save = QtWidgets.QPushButton("✔ 신뢰도 및 검색 영역 저장")
        btn_save.setStyleSheet("background: #7C6CFF; color: white; font-weight: 700; padding: 8px 18px; border-radius: 6px;")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QtWidgets.QPushButton("닫기")
        btn_cancel.setStyleSheet("padding: 8px 14px; border-radius: 6px; background: #1E2330; color: #E2E8F0; border: 1px solid #2A3040;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _open_visual_test(self) -> None:
        from .region_visual_test import RegionVisualTestDialog

        cur_step = dict(self.step or {})
        cur_step["assets"] = list(self.aliases)
        cur_step["asset_regions"] = self.get_asset_regions()
        cur_step["asset_confidences"] = self.get_asset_confidences()
        cur_step["confidence"] = self.get_confidence()
        if self._search_region:
            cur_step["region"] = list(self._search_region)

        dlg = RegionVisualTestDialog(cur_step, self.repository, asset_regions=self._asset_regions, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            updated_regs = dlg.get_asset_regions()
            self._asset_regions.update(updated_regs)
            for alias, reg in self._asset_regions.items():
                if alias in self.region_widgets:
                    btn_reg, btn_clear = self.region_widgets[alias]
                    self._update_asset_region_btn_text(btn_reg, reg)
                    btn_clear.setVisible(True)
            bounding = dlg.get_bounding_region()
            if bounding:
                self._search_region = bounding
                if hasattr(self, "btn_pick_region"):
                    w_text = f"[{bounding[0]}, {bounding[1]}, {bounding[2]}, {bounding[3]}]"
                    self.btn_pick_region.setText(f"📐 검색 영역 지정됨: {w_text} (재지정 가능)")

    def get_bounding_region(self) -> list[int] | None:
        valid_regs = [r for r in self._asset_regions.values() if isinstance(r, (list, tuple)) and len(r) >= 4]
        if not valid_regs:
            return None
        min_l = min(int(r[0]) for r in valid_regs)
        min_t = min(int(r[1]) for r in valid_regs)
        max_r = max(int(r[2]) for r in valid_regs)
        max_b = max(int(r[3]) for r in valid_regs)
        return [min_l, min_t, max_r, max_b]

    def _apply_all_confidence(self) -> None:
        target_val = self.batch_spin.value()
        for slider, spin in self.conf_sliders.values():
            slider.setValue(target_val)
            spin.setValue(target_val)

    def _open_image_editor(self, alias: str) -> None:
        if not self.repository:
            return
        path = self.repository.asset_path(alias)
        if not path or not Path(path).is_file():
            return
        from .image_editor import ImageEditorDialog
        editor = ImageEditorDialog(Path(path), alias, self.repository.history_dir, self)
        editor.exec()

    def get_confidence(self) -> int:
        if hasattr(self, "single_slider"):
            return self.single_slider[1].value()
        if self.aliases:
            first = self.aliases[0]
            if first in self.conf_sliders:
                return self.conf_sliders[first][1].value()
        if hasattr(self, "batch_spin"):
            return self.batch_spin.value()
        return self._confidence

    def get_asset_confidences(self) -> dict[str, int]:
        res: dict[str, int] = {}
        for alias, (_, spin) in self.conf_sliders.items():
            res[alias] = spin.value()
        return res

    def search_region(self) -> list[int] | None:
        if self._search_region and len(self._search_region) >= 4:
            try:
                if int(self._search_region[2]) > int(self._search_region[0]) and int(self._search_region[3]) > int(self._search_region[1]):
                    return list(self._search_region)
            except (TypeError, ValueError):
                pass
        return None

    def _update_asset_region_btn_text(self, btn: QtWidgets.QPushButton, reg: list[int] | None) -> None:
        if reg and len(reg) >= 4:
            btn.setText(f"📐 [{reg[0]}, {reg[1]}, {reg[2]}, {reg[3]}]")
            btn.setStyleSheet(
                "background: #143528; border: 1px solid #287A50; color: #4ADE80; "
                "font-weight: 700; padding: 4px 8px; border-radius: 4px;"
            )
        else:
            btn.setText("📐 영역 지정")
            btn.setStyleSheet(
                "background: #1E2330; border: 1px solid #3B455C; color: #8A98B0; "
                "font-weight: 600; padding: 4px 8px; border-radius: 4px;"
            )

    def _capture_drag_region(self, hint_text: str = "검색 범위를 마우스로 드래그하세요 (완료 시 Enter, 취소 시 Esc)") -> list[int] | None:
        hosts: list[QtWidgets.QWidget] = []
        for w in QtWidgets.QApplication.topLevelWidgets():
            if w.isVisible() and w is not self:
                hosts.append(w)
                w.hide()

        # Do NOT call self.hide()! Calling hide() on a dialog inside exec() breaks
        # Qt's modal event loop on Windows. Set opacity to 0.0 instead.
        orig_opacity = self.windowOpacity()
        self.setWindowOpacity(0.0)
        QtCore.QThread.msleep(150)
        QtWidgets.QApplication.processEvents()
        picker: ScreenCaptureDialog | None = None
        rel_region: list[int] | None = None
        try:
            pixmap, geometry = capture_virtual_desktop()
            if not pixmap.isNull() and geometry.isValid():
                picker = ScreenCaptureDialog(pixmap, geometry, parent=None, hint_text=hint_text)
                if picker.exec() == QtWidgets.QDialog.Accepted:
                    rect = picker.selected_screen_rect()
                    if rect.isValid() and rect.width() >= 4 and rect.height() >= 4:
                        client_rect = self._detect_target_client_rect(rect)
                        if client_rect is not None and client_rect.isValid():
                            rel_region = [
                                max(0, rect.left() - client_rect.left()),
                                max(0, rect.top() - client_rect.top()),
                                rect.right() - client_rect.left(),
                                rect.bottom() - client_rect.top(),
                            ]
                        else:
                            rel_region = [rect.left(), rect.top(), rect.right(), rect.bottom()]
        except Exception as exc:
            import logging
            logging.warning("영역 드래그 캡처 중 예외: %s", exc)
        finally:
            if picker is not None:
                try:
                    picker.deleteLater()
                except Exception:
                    pass
            for h in hosts:
                try:
                    h.show()
                except Exception:
                    pass
            self.setWindowOpacity(orig_opacity if orig_opacity > 0 else 1.0)
            self.show()
            self.raise_()
            self.activateWindow()
        return rel_region

    def _pick_search_region(self) -> None:
        rel_region = self._capture_drag_region("검색 범위를 마우스로 드래그하세요 (완료 시 Enter, 취소 시 Esc)")
        if rel_region is not None:
            self._search_region = rel_region
            w_text = f"[{rel_region[0]}, {rel_region[1]}, {rel_region[2]}, {rel_region[3]}]"
            if hasattr(self, "btn_pick_region"):
                self.btn_pick_region.setText(f"📐 검색 영역 지정됨: {w_text} (재지정 가능)")
                self.btn_pick_region.setStyleSheet("background: #143528; border: 1px solid #287A50; color: #4ADE80; padding: 6px; border-radius: 6px; font-weight: 700;")
            if hasattr(self, "notice_lbl"):
                self.notice_lbl.setText(f"✔ 클라이언트 상대 영역 {w_text} 지정 완료!\n창 아래쪽 [✔ 신뢰도 설정 저장] 또는 [⚡ 즉시 저장]을 누르면 완료됩니다.")
                self.notice_lbl.setVisible(True)

    def _pick_asset_search_region(self, alias: str, btn: QtWidgets.QPushButton) -> None:
        rel_region = self._capture_drag_region(f"[{alias}] 이미지의 개별 검색 범위를 마우스로 드래그하세요 (완료 시 Enter, 취소 시 Esc)")
        if rel_region is not None:
            self._asset_regions[alias] = rel_region
            self._update_asset_region_btn_text(btn, rel_region)
            if alias in self.region_widgets:
                self.region_widgets[alias][1].setVisible(True)

    def _do_clear_asset_region(self, alias: str, btn: QtWidgets.QPushButton, clear_btn: QtWidgets.QPushButton) -> None:
        self._asset_regions.pop(alias, None)
        self._update_asset_region_btn_text(btn, None)
        clear_btn.setVisible(False)

    def _clear_asset_search_region(self, alias: str, btn: QtWidgets.QPushButton) -> None:
        if alias in self.region_widgets:
            clear_btn = self.region_widgets[alias][1]
            self._do_clear_asset_region(alias, btn, clear_btn)
        else:
            self._asset_regions.pop(alias, None)
            self._update_asset_region_btn_text(btn, None)

    def get_asset_regions(self) -> dict[str, list[int]]:
        res: dict[str, list[int]] = {}
        for alias, reg in self._asset_regions.items():
            if isinstance(reg, (list, tuple)) and len(reg) >= 4:
                try:
                    if int(reg[2]) > int(reg[0]) and int(reg[3]) > int(reg[1]):
                        res[str(alias)] = [int(x) for x in reg[:4]]
                except (TypeError, ValueError):
                    pass
        return res

    def _detect_target_client_rect(self, screen_rect: QtCore.QRect) -> QtCore.QRect | None:
        # 1순위: 현재 노드(step)에 지정된 대상 창(dnplayer.exe 등)을 최우선 검색
        win_text = str(self.step.get("region_window") or self.step.get("window") or "").strip()
        exe_text = str(self.step.get("region_window_exe") or self.step.get("window_exe") or "").strip()
        if win_text or exe_text:
            try:
                from .image_search_test import _find_window
                hwnd = _find_window(exe_text, win_text)
                if hwnd:
                    import ctypes, ctypes.wintypes as wt
                    user32 = ctypes.windll.user32
                    crect = wt.RECT()
                    origin_pt = wt.POINT(0, 0)
                    if user32.GetClientRect(hwnd, ctypes.byref(crect)) and user32.ClientToScreen(hwnd, ctypes.byref(origin_pt)):
                        cw = int(crect.right - crect.left)
                        ch = int(crect.bottom - crect.top)
                        c_rect = QtCore.QRect(int(origin_pt.x), int(origin_pt.y), cw, ch)
                        if c_rect.intersects(screen_rect):
                            return c_rect
            except Exception:
                pass

        # 2순위: 중심 좌표 기준 Z-order 최상위 창 감지
        try:
            target = ActionEditor._window_target_at(screen_rect.center())
            if target and target.get("client_origin") and target.get("client_size"):
                co = target["client_origin"]
                cs = target["client_size"]
                return QtCore.QRect(co[0], co[1], cs[0], cs[1])
        except Exception:
            pass

        # 3순위: 64비트 호환 WindowFromPoint ctypes 프로토타입
        try:
            import ctypes, ctypes.wintypes as wt
            user32 = ctypes.windll.user32
            class _POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            user32.WindowFromPoint.argtypes = [_POINT]
            user32.WindowFromPoint.restype = wt.HWND
            pt = _POINT(screen_rect.center().x(), screen_rect.center().y())
            hwnd = user32.WindowFromPoint(pt)
            root = user32.GetAncestor(hwnd, 2) or hwnd
            if root:
                crect = wt.RECT()
                user32.GetClientRect(root, ctypes.byref(crect))
                origin = wt.POINT(0, 0)
                user32.ClientToScreen(root, ctypes.byref(origin))
                cw = int(crect.right - crect.left)
                ch = int(crect.bottom - crect.top)
                if cw > 10 and ch > 10:
                    return QtCore.QRect(int(origin.x), int(origin.y), cw, ch)
        except Exception:
            pass
        return None

SECTION_TOOLTIPS: dict[str, str] = {
    "기본 설정": "노드의 기본 검색 대상, 텍스트, 색상 및 핵심 동작 파라미터를 설정합니다.",
    "검색 범위": "화면 전체 대신 특정 사각 영역만 제한하여 탐색 속도와 정확도를 극대화합니다.",
    "추가 검색 범위": "1차 검색 영역 외에 보조로 탐색할 제2의 사각 영역을 지정합니다.",
    "검색 성공 후 동작": "이미지를 성공적으로 찾았을 때 클릭 모드(활성/비활성), 오프셋, 키 입력 등을 수행합니다.",
    "검색 실패 시 동작": "이미지를 못 찾았을 때 멈추지 않고 닫기(X)나 다른 좌표를 대체 클릭(오프셋 클릭)하도록 설정합니다.",
    "성능 최적화": "연속 실행 시 화면 캡처 캐시 재사용 시간을 설정하여 CPU 사용량을 줄이고 탐색 속도를 높입니다.",
    "완료 처리": "현재 노드 완료 후 대기 시간 및 성공 시 같은 노드 재검색(반복) 설정을 구성합니다.",
    "다중 탐색 설정": "화면에 동일한 이미지가 여러 개 나타날 때 모든 위치를 순차 클릭하거나 카운트합니다.",
    "대기 영역 (선택)": "단일 좌표 대신 특정 사각 영역 안에서 색상의 출현/소멸을 감지합니다.",
    "게이지 영역": "체력바나 진행 게이지가 위치한 화면 사각 영역입니다.",
    "결과 변수": "인식 결과, 추출된 텍스트, 좌표, 게이지 비율(%) 등을 저장할 변수명을 지정합니다.",
    "조건 분기": "추출된 값이나 탐색 결과를 기준값과 비교하여 조건 분기(성공/실패) 흐름을 제어합니다.",
    "동작 설정": "발견 또는 조건 충족 시 수행할 마우스 클릭, 변수 저장 등의 후속 동작입니다.",
    "클릭 옵션": "클릭 위치 오프셋, 클릭 후 대기 시간 등을 설정합니다.",
    "타이밍": "제한 시간(Timeout) 및 주기적인 재확인 간격(Poll Delay)을 설정합니다.",
    "OCR 설정": "인식 엔진(자동/Paddle/Tesseract), 언어, 추출 모드를 설정합니다.",
    "텍스트 찾기": "인식된 전체 문자열 중에서 특정 단어가 포함되어 있는지 검사합니다.",
    "좌표 설정": "동작을 수행할 특정 화면 또는 창 기준 (X, Y) 좌표입니다.",
    "상세 설정": "재시도 횟수, 오류 처리 및 기타 부가 옵션입니다.",
    "보안 설정": "보안 보관함(Vault) 암호화 키 및 마스킹 옵션입니다.",
    "창/프로그램": "동작 대상이 되는 특정 프로그램의 제목, 실행 파일명입니다.",
    "이미지 추적": "움직이는 UI나 대상을 찾기 위한 기준 템플릿 이미지와 신뢰도입니다.",
    "색상 추적": "대상을 식별하거나 검증할 고유 픽셀 색상과 오차 범위입니다.",
}

ACTION_GUIDE_SUMMARIES: dict[str, str] = {
    "image_search": "<b>💡 이미지 서치 핵심 가이드</b><br>• <b>엔진</b>: 배율/크기 변화 대응은 <b>OpenCV</b>, 가장 빠른 반응속도는 <b>AutoHotkey</b> 권장<br>• <b>프리셋</b>: 오탐 방지는 <b>🎯 정밀도 우선</b>, 연타/고속은 <b>⚡ 속도 우선</b> 선택<br>• <b>실패 시 대체 클릭</b>: 대상을 못 찾았을 때 닫기(X)나 다른 영역을 대신 클릭하도록 설정 가능",
    "mouse_click": "<b>💡 마우스 클릭 가이드</b><br>• 실제 마우스 커서가 좌표로 이동하여 클릭합니다.<br>• 프로그램 창 위치가 바뀌어도 클릭되게 하려면 좌표 기준을 <b>'대상 프로그램 기준'</b>으로 설정하세요.",
    "inactive_click": "<b>💡 비활성 클릭 가이드</b><br>• 창이 다른 창 뒤에 가려져 있어도 마우스 이동 없이 백그라운드로 클릭을 전송합니다.<br>• 전송 방식은 <b>'자동'</b>으로 두시면 PostMessage와 ControlClick을 결합하여 최적 전송합니다.",
    "type_text": "<b>💡 텍스트 입력 가이드</b><br>• 한글, 영문, 특수문자, 줄바꿈을 대상 창에 타이핑합니다.<br>• 백그라운드 입력을 원하시면 방식을 <b>'비활성 창'</b>으로 설정하세요.",
    "wait": "<b>💡 대기 시간 가이드</b><br>• 다음 노드를 실행하기 전에 일정 시간(ms) 동안 대기합니다. (1000ms = 1초)",
    "pixel_search": "<b>💡 픽셀 색상 서치 가이드</b><br>• 화면의 특정 좌표나 범위에서 지정한 색상(#RRGGBB)을 초고속으로 검출합니다.<br>• 그라데이션이나 그림자가 있으면 허용 오차(Tolerance)를 15~25 정도로 늘려주세요.",
    "ocr_tracking": "<b>💡 OCR 추적 가이드</b><br>• 기준 이미지를 먼저 화면에서 찾은 후, 그 위치를 기준으로 오프셋 영역의 텍스트/숫자를 정밀 판독합니다.<br>• 골드량, 체력 수치, 변동성 UI 인식에 최적입니다.",
    "datetime_condition": "<b>💡 날짜·시간 조건 가이드</b><br>• 특정 날짜, 특정 요일(월~일), 또는 특정 시간대(예: 09:00~18:00)에만 매크로가 실행되도록 제어합니다.",
    "flow_control": "<b>💡 반복 이동 가이드</b><br>• 지정한 노드 번호로 다시 이동하여 루프(반복)를 형성합니다.<br>• 반복 횟수를 지정하면 무한 루프 없이 안전하게 순환합니다.",
}


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
        self._cached_assets: list[str] | None = None
        self._cached_asset_paths: dict[str, Path] | None = None
        self._cached_macros: list[str] | None = None
        self._cached_tables: list[str] | None = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack)
        # Image search is the most frequently inspected page and several capture
        # helpers need its coordinate widgets before set_action() is called.
        self._ensure_page("image_search")

    def _build_page(self, action: str) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 4, 4, 4)
        body_layout.setSpacing(8)
        self.widgets[action] = {}
        guide_text = ACTION_GUIDE_SUMMARIES.get(action, "")
        if guide_text:
            guide_card = QtWidgets.QFrame()
            guide_card.setStyleSheet("""
                QFrame {
                    background: #141A26;
                    border: 1px solid #2B384E;
                    border-left: 4px solid #38E7FF;
                    border-radius: 6px;
                    padding: 8px 10px;
                }
            """)
            g_layout = QtWidgets.QVBoxLayout(guide_card)
            g_layout.setContentsMargins(4, 4, 4, 4)
            g_label = QtWidgets.QLabel(guide_text)
            g_label.setWordWrap(True)
            g_label.setStyleSheet("color: #E2E8F0; font-size: 8.8pt; line-height: 1.45;")
            g_layout.addWidget(g_label)
            body_layout.addWidget(guide_card)
        helpers = self._build_helpers(action)
        if helpers is not None:
            body_layout.addWidget(helpers)
        sections: dict[str, QtWidgets.QFormLayout] = {}
        for spec in ACTION_FIELDS.get(action, []):
            if spec.key in COMMON_FIELD_KEYS:
                continue
            if spec.section not in sections:
                sec_desc = SECTION_TOOLTIPS.get(spec.section, "")
                group = QtWidgets.QGroupBox(f"{spec.section} ❓" if sec_desc else spec.section)
                if sec_desc:
                    group.setToolTip(f"💡 [{spec.section}]\n{sec_desc}")
                form = QtWidgets.QFormLayout(group)
                form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
                form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                sections[spec.section] = form
                body_layout.addWidget(group)
            widget = self._make_widget(spec)
            if spec.tooltip:
                label_widget = QtWidgets.QLabel(f"{spec.label} ⓘ")
                tip_text = f"💡 [{spec.label}]\n{spec.tooltip}"
                label_widget.setToolTip(tip_text)
                widget.setToolTip(tip_text)
                sections[spec.section].addRow(label_widget, widget)
            else:
                sections[spec.section].addRow(spec.label, widget)
            self.widgets[action][spec.key] = widget
            if action in {"image_search", "screen_condition"} and spec.key == "engine":
                preset_bar = QtWidgets.QWidget()
                preset_layout = QtWidgets.QHBoxLayout(preset_bar)
                preset_layout.setContentsMargins(0, 3, 0, 5)
                preset_layout.setSpacing(6)

                lbl_pre = QtWidgets.QLabel("속도 프리셋:")
                lbl_pre.setStyleSheet("color: #70C5FF; font-weight: bold; font-size: 8.5pt;")
                preset_layout.addWidget(lbl_pre)

                btn_ahk_fast = QtWidgets.QPushButton("⚡ AHK 초고속 (1~3ms)")
                btn_ahk_fast.setStyleSheet("background: #162C3D; border: 1px solid #2B5A8A; color: #5ED9FF; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 8.5pt;")
                btn_ahk_fast.setToolTip("AutoHotkey 초고속 프리셋:\n· AHK 엔진 전환 + 색상 오차 10 + 간격 20ms + 타임아웃 300ms\n· ※ 설정된 검색 범위·대상 창·클릭 설정은 100% 안전하게 유지됩니다.")
                btn_ahk_fast.clicked.connect(lambda _, act=action: self.apply_speed_preset(act, "ahk_ultra_fast"))
                preset_layout.addWidget(btn_ahk_fast)

                btn_cv_fast = QtWidgets.QPushButton("⚡ OpenCV 초고속 (5~10ms)")
                btn_cv_fast.setStyleSheet("background: #143528; border: 1px solid #287A50; color: #4ADE80; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 8.5pt;")
                btn_cv_fast.setToolTip("OpenCV 초고속 프리셋:\n· OpenCV 엔진 전환 + Fast(피라미드 축소) + 신뢰도 82% + 간격 25ms\n· ※ 설정된 검색 범위·대상 창·클릭 설정은 100% 안전하게 유지됩니다.")
                btn_cv_fast.clicked.connect(lambda _, act=action: self.apply_speed_preset(act, "opencv_fast"))
                preset_layout.addWidget(btn_cv_fast)

                btn_balanced = QtWidgets.QPushButton("◐ 균형 (권장)")
                btn_balanced.setStyleSheet("background: #25223A; border: 1px solid #5A488A; color: #D8B4FE; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 8.5pt;")
                btn_balanced.setToolTip("균형 프리셋:\n· 현재 선택된 엔진 기준 안정적인 권장 속도 설정\n· ※ 설정된 검색 범위·대상 창·클릭 설정은 100% 안전하게 유지됩니다.")
                btn_balanced.clicked.connect(lambda _, act=action: self._apply_balanced_preset(act))
                preset_layout.addWidget(btn_balanced)

                btn_precise = QtWidgets.QPushButton("🎯 정밀 (정확도 우선)")
                btn_precise.setStyleSheet("background: #2D2314; border: 1px solid #785818; color: #FBBF24; font-weight: bold; padding: 4px 8px; border-radius: 4px; font-size: 8.5pt;")
                btn_precise.setToolTip("정밀 프리셋:\n· 현재 엔진 기준 정확도를 최우선으로 탐색 (OpenCV: Precise/90% 신뢰도, AHK: 오차 20)\n· ※ 설정된 검색 범위·대상 창·클릭 설정은 100% 안전하게 유지됩니다.")
                btn_precise.clicked.connect(lambda _, act=action: self._apply_precise_preset(act))
                preset_layout.addWidget(btn_precise)

                preset_layout.addStretch(1)
                sections[spec.section].addRow("", preset_bar)
            if action == "image_search" and spec.key == "fail_click_offset.1":
                btn_pick_fail = QtWidgets.QPushButton("🎯 화면 클릭으로 실패 위치 지정")
                btn_pick_fail.setStyleSheet("background: #1B293A; border: 1px solid #2B5A8A; color: #70C5FF; font-weight: 700; padding: 5px;")
                btn_pick_fail.setToolTip("화면에서 원하는 위치를 마우스 좌클릭으로 1번 찍어 실패 클릭 좌표를 자동 입력합니다.")
                btn_pick_fail.clicked.connect(self._capture_fail_click_cursor)
                sections[spec.section].addRow("", btn_pick_fail)
            if action in {"pixel_search", "ocr"} and spec.key == "click_offset_y":
                btn_row = QtWidgets.QWidget()
                btn_layout = QtWidgets.QHBoxLayout(btn_row)
                btn_layout.setContentsMargins(0, 3, 0, 5)
                btn_layout.setSpacing(6)

                btn_pick_offset = QtWidgets.QPushButton("🎯 화면 좌클릭으로 오프셋 좌표 지정")
                btn_pick_offset.setStyleSheet("background: #251B32; border: 1px solid #6B357E; color: #FF94D2; font-weight: 700; padding: 5px 10px; border-radius: 4px;")
                btn_pick_offset.setToolTip("화면에서 1번: 기준 위치, 2번: 실제 클릭할 목표 위치를 차례로 좌클릭하여 오프셋(X, Y)을 자동 계산합니다.\n(같은 위치를 클릭하거나 Enter를 누르면 오프셋 0으로 설정됩니다)")
                btn_pick_offset.clicked.connect(lambda _, act=action: self._capture_pixel_search_offset(act))
                btn_layout.addWidget(btn_pick_offset, 2)

                btn_reset_offset = QtWidgets.QPushButton("⟲ (0, 0) 초기화")
                btn_reset_offset.setStyleSheet("background: #171C26; border: 1px solid #2B354A; color: #CBD5E1; font-weight: 600; padding: 5px 8px; border-radius: 4px;")
                btn_reset_offset.setToolTip("오프셋을 X: 0, Y: 0으로 초기화하여 발견된 위치를 그대로 클릭합니다.")
                btn_reset_offset.clicked.connect(lambda _, act=action: self._reset_pixel_search_offset(act))
                btn_layout.addWidget(btn_reset_offset, 1)

                sections[spec.section].addRow("오프셋 지정", btn_row)
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
        elif action == "datetime_condition":
            for key in ("date_start_enabled", "date_end_enabled", "time_end_enabled", "weekday_enabled"):
                toggle = self.widgets[action].get(key)
                if isinstance(toggle, QtWidgets.QCheckBox):
                    toggle.toggled.connect(self._sync_datetime_controls)
            self._sync_datetime_controls()
        elif action == "pixel_search":
            color_edit = self.widgets[action].get("color")
            tol_bar = self.widgets[action].get("tolerance")
            if isinstance(color_edit, QtWidgets.QLineEdit) and isinstance(tol_bar, ColorToleranceBarWidget):
                color_edit.textChanged.connect(lambda txt, tb=tol_bar: tb.setColor(txt))
        if action in {"image_search", "screen_condition"}:
            engine = self.widgets[action].get("engine")
            if isinstance(engine, QtWidgets.QComboBox):
                engine.currentIndexChanged.connect(lambda _idx, act=action: self._on_engine_changed(act))
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _update_engine_controls_state(self, action: str) -> None:
        if action not in {"image_search", "screen_condition"}:
            return
        widgets = self.widgets.get(action, {})
        engine_widget = widgets.get("engine")
        if not isinstance(engine_widget, QtWidgets.QComboBox):
            return
        engine = str(engine_widget.currentData() or "opencv").lower()
        conf_w = widgets.get("confidence")
        prof_w = widgets.get("search_profile")
        var_w = widgets.get("variation")

        if engine == "ahk":
            if conf_w:
                conf_w.setEnabled(False)
                conf_w.setToolTip("⚠️ AutoHotkey 엔진은 유사도(%)를 지원하지 않습니다 (100% 픽셀 일치만 사용). OpenCV 엔진에서 활성화됩니다.")
            if prof_w:
                prof_w.setEnabled(False)
                prof_w.setToolTip("⚠️ AutoHotkey 엔진은 피라미드 축소 다운샘플링을 사용하지 않습니다. OpenCV 엔진에서 활성화됩니다.")
            if var_w:
                var_w.setEnabled(True)
                var_w.setToolTip("💡 AHK 색상 허용 오차: 낮을수록(0~15) 1~2ms 초고속으로 일치합니다. 너무 높으면 전체 화면에서 속도가 저하됩니다.")
        else:
            if conf_w:
                conf_w.setEnabled(True)
                conf_w.setToolTip("OpenCV에서 사용하는 최소 일치율입니다 (75~88 권장).")
            if prof_w:
                prof_w.setEnabled(True)
                prof_w.setToolTip("OpenCV 탐색 품질: 빠름(권장 · 피라미드 축소) / 균형 / 정밀")
            if var_w:
                var_w.setEnabled(True)

    def _on_engine_changed(self, action: str) -> None:
        if action not in {"image_search", "screen_condition"}:
            return
        widgets = self.widgets.get(action, {})
        engine_widget = widgets.get("engine")
        if not isinstance(engine_widget, QtWidgets.QComboBox):
            return
        new_engine = str(engine_widget.currentData() or "opencv").lower()
        old_engine = getattr(self, f"_last_engine_{action}", None)
        if old_engine == new_engine:
            return

        if not hasattr(self, "_engine_configs"):
            self._engine_configs = {}
        if action not in self._engine_configs:
            self._engine_configs[action] = {}

        # 1. Backup current settings under old_engine
        if old_engine:
            backup = {}
            for key in ("search_profile", "variation", "confidence", "timeout", "poll_delay", "capture_cache_ms"):
                w = widgets.get(key)
                if isinstance(w, QtWidgets.QSpinBox):
                    backup[key] = w.value()
                elif isinstance(w, QtWidgets.QComboBox):
                    backup[key] = w.currentData()
            self._engine_configs[action][old_engine] = backup

        setattr(self, f"_last_engine_{action}", new_engine)

        # 2. Restore saved settings for new_engine if available, or adapt
        saved = self._engine_configs[action].get(new_engine)

        if new_engine == "ahk":
            if saved:
                for key, val in saved.items():
                    w = widgets.get(key)
                    if isinstance(w, QtWidgets.QSpinBox) and isinstance(val, (int, float)):
                        w.setValue(int(val))
                    elif isinstance(w, QtWidgets.QComboBox):
                        idx = w.findData(val)
                        if idx >= 0:
                            w.setCurrentIndex(idx)
            else:
                # First time switching to AHK: adapt to fast & reliable AHK settings
                var_w = widgets.get("variation")
                if isinstance(var_w, QtWidgets.QSpinBox):
                    if var_w.value() > 20:
                        var_w.setValue(12)
                poll_w = widgets.get("poll_delay")
                if isinstance(poll_w, QtWidgets.QSpinBox):
                    poll_w.setValue(25)
                timeout_w = widgets.get("timeout")
                if isinstance(timeout_w, QtWidgets.QSpinBox) and timeout_w.value() > 1000:
                    timeout_w.setValue(500)
        elif new_engine == "opencv":
            if saved:
                for key, val in saved.items():
                    w = widgets.get(key)
                    if isinstance(w, QtWidgets.QSpinBox) and isinstance(val, (int, float)):
                        w.setValue(int(val))
                    elif isinstance(w, QtWidgets.QComboBox):
                        idx = w.findData(val)
                        if idx >= 0:
                            w.setCurrentIndex(idx)
            else:
                # First time switching to OpenCV: ensure valid confidence and profile
                conf_w = widgets.get("confidence")
                if isinstance(conf_w, QtWidgets.QSpinBox) and conf_w.value() < 50:
                    conf_w.setValue(84)
                prof_w = widgets.get("search_profile")
                if isinstance(prof_w, QtWidgets.QComboBox):
                    idx = prof_w.findData("fast")
                    if idx >= 0:
                        prof_w.setCurrentIndex(idx)

        self._update_engine_controls_state(action)

    def apply_speed_preset(self, action: str, preset_name: str) -> None:
        """Apply engine & speed presets without touching user-configured search regions, target windows or clicks."""
        widgets = self.widgets.get(action, {})
        if not widgets:
            return

        presets = {
            "ahk_ultra_fast": {
                "name": "AutoHotkey 초고속 (1~3ms)",
                "engine": "ahk",
                "variation": 10,
                "poll_delay": 20,
                "timeout": 300,
                "search_preset": "ultra_fast",
            },
            "ahk_balanced": {
                "name": "AutoHotkey 균형 (3~8ms)",
                "engine": "ahk",
                "variation": 16,
                "poll_delay": 30,
                "timeout": 800,
                "search_preset": "balanced",
            },
            "ahk_precise": {
                "name": "AutoHotkey 정밀 (정확도 우선)",
                "engine": "ahk",
                "variation": 20,
                "poll_delay": 40,
                "timeout": 1500,
                "search_preset": "precise",
            },
            "opencv_fast": {
                "name": "OpenCV 초고속 (5~10ms)",
                "engine": "opencv",
                "search_profile": "fast",
                "confidence": 82,
                "poll_delay": 25,
                "timeout": 400,
                "capture_cache_ms": 50,
                "search_preset": "ultra_fast",
            },
            "opencv_balanced": {
                "name": "OpenCV 균형 (권장 · 10~15ms)",
                "engine": "opencv",
                "search_profile": "balanced",
                "confidence": 86,
                "poll_delay": 35,
                "timeout": 1000,
                "capture_cache_ms": 45,
                "search_preset": "balanced",
            },
            "opencv_precise": {
                "name": "OpenCV 정밀 (정확도 우선)",
                "engine": "opencv",
                "search_profile": "precise",
                "confidence": 90,
                "poll_delay": 50,
                "timeout": 1800,
                "capture_cache_ms": 40,
                "search_preset": "precise",
            },
        }

        spec = presets.get(preset_name)
        if not spec:
            return

        self._current_search_preset = spec.get("search_preset", "")

        # 1. Update engine first if needed (this triggers _on_engine_changed)
        target_engine = spec.get("engine")
        engine_widget = widgets.get("engine")
        if target_engine and isinstance(engine_widget, QtWidgets.QComboBox):
            idx = engine_widget.findData(target_engine)
            if idx >= 0:
                engine_widget.setCurrentIndex(idx)

        # 2. Apply speed tuning values (preserving regions and clicks!)
        for key, val in spec.items():
            if key in {"name", "engine", "search_preset"}:
                continue
            w = widgets.get(key)
            if isinstance(w, QtWidgets.QSpinBox) and isinstance(val, (int, float)):
                w.setValue(int(val))
            elif isinstance(w, QtWidgets.QComboBox):
                idx = w.findData(val)
                if idx >= 0:
                    idx_val = w.findData(val)
                    w.setCurrentIndex(idx_val if idx_val >= 0 else w.findText(str(val)))

        self._update_engine_controls_state(action)

        # Notify user
        status_msg = f"⚡ [{spec['name']}] 적용 완료\n※ 지정된 검색 범위·대상 창·클릭 설정은 안전하게 보존되었습니다."
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), status_msg, self)

    def _apply_balanced_preset(self, action: str) -> None:
        widgets = self.widgets.get(action, {})
        engine_widget = widgets.get("engine")
        curr_engine = str(engine_widget.currentData() or "opencv").lower() if isinstance(engine_widget, QtWidgets.QComboBox) else "opencv"
        if curr_engine == "ahk":
            self.apply_speed_preset(action, "ahk_balanced")
        else:
            self.apply_speed_preset(action, "opencv_balanced")

    def _apply_precise_preset(self, action: str) -> None:
        widgets = self.widgets.get(action, {})
        engine_widget = widgets.get("engine")
        curr_engine = str(engine_widget.currentData() or "opencv").lower() if isinstance(engine_widget, QtWidgets.QComboBox) else "opencv"
        if curr_engine == "ahk":
            self.apply_speed_preset(action, "ahk_precise")
        else:
            self.apply_speed_preset(action, "opencv_precise")

    def _show_speed_preset_menu(self, action: str) -> None:
        menu = QtWidgets.QMenu(self)
        act1 = menu.addAction("⚡ AutoHotkey 초고속 프리셋 (1~3ms · 오차 10 · 간격 20ms)")
        act2 = menu.addAction("⚡ OpenCV 초고속 프리셋 (5~10ms · Fast 품질 · 신뢰도 82%)")
        menu.addSeparator()
        act3 = menu.addAction("◐ AutoHotkey 균형 프리셋 (오차 16 · 간격 30ms)")
        act4 = menu.addAction("◐ OpenCV 균형 프리셋 (Balanced 품질 · 신뢰도 86%)")
        menu.addSeparator()
        act5 = menu.addAction("🎯 AutoHotkey 정밀 프리셋 (오차 20 · 간격 40ms)")
        act6 = menu.addAction("🎯 OpenCV 정밀 프리셋 (Precise 품질 · 신뢰도 90%)")
        chosen = menu.exec(QtGui.QCursor.pos())
        if chosen == act1:
            self.apply_speed_preset(action, "ahk_ultra_fast")
        elif chosen == act2:
            self.apply_speed_preset(action, "opencv_fast")
        elif chosen == act3:
            self.apply_speed_preset(action, "ahk_balanced")
        elif chosen == act4:
            self.apply_speed_preset(action, "opencv_balanced")
        elif chosen == act5:
            self.apply_speed_preset(action, "ahk_precise")
        elif chosen == act6:
            self.apply_speed_preset(action, "opencv_precise")

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
            buttons.append(("◎ 프로그램 기준 좌표 · 대상 위치 클릭", lambda: self._capture_program_cursor(action)))
        if action == "image_search":
            buttons.extend(
                [
                    ("⚡ 속도 프리셋 ▾", lambda: self._show_speed_preset_menu(action)),
                    ("⚡ 자동 설정", self._auto_configure_image_search),
                    ("🎯 멀티 전체 발견 시 참(성공) 설정", self._preset_multi_count_all),
                    ("🔍 지정 영역 시각화 및 실시간 검사", self._open_region_visual_test),
                    ("▣ 전체 화면 · 모든 모니터", self._use_full_virtual_screen),
                    ("⌖ 캡처 등록·편집", self._capture_register_asset),
                    ("▣ 주 검색 범위 잡기", lambda: self._pick_region(action, "region")),
                    ("▣ 추가 검색 범위", lambda: self._pick_region(action, "region2")),
                    ("◎ 대상 창", lambda: self._pick_window(action, "window")),
                    ("▣ 대상 프로그램", lambda: self._pick_window(action, "program")),
                    ("설정 검사", self._diagnose_image_search),
                    ("▤ 이미지 서치 테스트 센터", self._open_image_search_test_center),
                    ("🎯 실패 클릭 좌표 지정", self._capture_fail_click_cursor),
                ]
            )
        elif action == "screen_condition":
            buttons.extend(
                [
                    ("⚡ 속도 프리셋 ▾", lambda: self._show_speed_preset_menu(action)),
                    ("▣ 주 확인 범위 잡기", lambda: self._pick_region(action, "region")),
                    ("◎ 대상 창", lambda: self._pick_window(action, "window")),
                    ("▣ 대상 프로그램", lambda: self._pick_window(action, "program")),
                ]
            )
        elif action == "ocr":
            buttons.append(("▣ OCR 인식 범위 잡기", lambda: self._pick_region(action, "region")))
            buttons.append(("▶ OCR 테스트", lambda: self._test_ocr(action)))
            buttons.append(("🎨 OCR 필터 튜닝", lambda: self._open_ocr_filter_tuner(action)))
        elif action == "pixel_search":
            buttons.append(("🔍 색상 검색 영역 검증 및 실시간 검사", self._open_region_visual_test))
            buttons.append(("🎯 색상 스포이트 (돋보기 좌클릭)", lambda: self._pick_pixel_color(action)))
            buttons.append(("▣ 검색 영역 잡기 (드래그)", lambda: self._pick_region(action, "search_region")))
            buttons.append(("🎯 멀티 전체 발견 시 참(성공) 설정", self._preset_color_multi_count_all))
        elif action == "ocr_tracking":
            buttons.append(("🎯 1단계: 추적 대상 이미지 캡처", lambda: self._pick_ocr_track_target(action)))
            buttons.append(("🎨 1단계-B: 추적 색상 스포이트", lambda: self._pick_ocr_track_color(action)))
            buttons.append(("▣ 2단계: OCR 영역 잡기 (드래그)", lambda: self._pick_ocr_track_offset(action)))
        elif action == "multi_pixel_check":
            buttons.append(("❖ 다중 픽셀 핀 찍기 (돋보기 좌클릭)", lambda: self._pick_multi_pixels(action)))
        elif action == "wait_color":
            buttons.append(("⏳ 목표 색상·좌표 추출 (돋보기 좌클릭)", lambda: self._pick_wait_color(action)))
            buttons.append(("▣ 대기 영역 잡기 (드래그)", lambda: self._pick_region(action, "search_region")))
        elif action == "color_ratio":
            buttons.append(("🎨 게이지 색상 추출 (돋보기 좌클릭)", lambda: self._pick_gauge_color(action)))
            buttons.append(("▣ 게이지 영역 잡기 (드래그)", lambda: self._pick_region(action, "search_region")))
        elif action == "set_var":
            return self._build_set_var_helpers(action)
        elif action == "calc_var":
            return self._build_calc_var_helpers(action)
        elif action == "remote_notify":
            buttons.append(("🔔 휴대폰으로 이 알림 즉시 테스트", self._test_remote_notify))
        elif action in {"inactive_click", "type_text", "table_paste"}:
            buttons.append(("◎ 대상 창 찾기", lambda: self._pick_window(action)))
        if action == "type_text":
            buttons.extend(
                [
                    ("↵ Enter 추가", lambda: self._append_type_text_key("{Enter}")),
                    ("⇥ Tab 추가", lambda: self._append_type_text_key("{Tab}")),
                    ("Esc 추가", lambda: self._append_type_text_key("{Esc}")),
                    ("⌨ 기능키 선택", self._choose_type_text_key),
                ]
            )
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

    def _test_remote_notify(self) -> None:
        action = "remote_notify"
        title_w = self.widgets.get(action, {}).get("title")
        msg_w = self.widgets.get(action, {}).get("message")
        level_w = self.widgets.get(action, {}).get("level")
        title = title_w.text().strip() if isinstance(title_w, QtWidgets.QLineEdit) else "MacroRelay"
        message = msg_w.toPlainText().strip() if isinstance(msg_w, QtWidgets.QTextEdit) else "작업이 완료되었습니다."
        level = str(level_w.currentData() if isinstance(level_w, QtWidgets.QComboBox) else "info")
        title = title or "MacroRelay"
        message = message or "작업이 완료되었습니다."
        level = level or "info"
        try:
            from remote_common import load_config, post_agent_event
            base_dir = Path(__file__).resolve().parent.parent
            config = load_config(base_dir, create=False)
            if not config.get("enabled"):
                QtWidgets.QMessageBox.warning(
                    self,
                    "원격 알림",
                    "원격 제어가 비활성화되어 있습니다.\nStudio 설정(⚙)의 '원격 제어' 탭에서 활성화해주세요.",
                )
                return
            res = post_agent_event(config, "notification", message, {"title": title, "level": level})
            if res.get("ok"):
                QtWidgets.QMessageBox.information(
                    self,
                    "알림 전송 성공",
                    f"휴대폰으로 테스트 알림이 성공적으로 전송되었습니다!\n\n제목: {title}\n내용: {message}",
                )
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "알림 전송 실패",
                    f"전송 실패: {res.get('error', '알 수 없는 오류')}\n중계 서버 연결 상태를 확인해주세요.",
                )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "알림 전송 오류", f"오류 발생: {e}")

    def _sync_datetime_controls(self) -> None:
        widgets = self.widgets.get("datetime_condition", {})
        dependencies = {
            "date_start": "date_start_enabled",
            "date_end": "date_end_enabled",
            "time_end": "time_end_enabled",
        }
        for field, toggle_key in dependencies.items():
            editor = widgets.get(field)
            toggle = widgets.get(toggle_key)
            if editor is not None and isinstance(toggle, QtWidgets.QCheckBox):
                editor.setEnabled(toggle.isChecked())
        weekday_toggle = widgets.get("weekday_enabled")
        weekday_enabled = isinstance(weekday_toggle, QtWidgets.QCheckBox) and weekday_toggle.isChecked()
        for key in ("weekday_mon", "weekday_tue", "weekday_wed", "weekday_thu", "weekday_fri", "weekday_sat", "weekday_sun"):
            widget = widgets.get(key)
            if widget is not None:
                widget.setEnabled(weekday_enabled)

    def _append_type_text_key(self, token: str) -> None:
        editor = self.widgets.get("type_text", {}).get("text")
        mode = self.widgets.get("type_text", {}).get("send_mode")
        if isinstance(mode, QtWidgets.QComboBox):
            index = mode.findData("input")
            if index >= 0:
                mode.setCurrentIndex(index)
        if isinstance(editor, QtWidgets.QPlainTextEdit):
            editor.textCursor().insertText(token)
            editor.setFocus(QtCore.Qt.OtherFocusReason)

    def _choose_type_text_key(self) -> None:
        choices = [
            ("Enter", "{Enter}"), ("Tab", "{Tab}"), ("Escape", "{Esc}"),
            ("Backspace", "{Backspace}"), ("Delete", "{Delete}"),
            ("Home", "{Home}"), ("End", "{End}"), ("Page Up", "{PgUp}"),
            ("Page Down", "{PgDn}"), ("방향키 ↑", "{Up}"), ("방향키 ↓", "{Down}"),
            ("방향키 ←", "{Left}"), ("방향키 →", "{Right}"),
            *[(f"F{number}", f"{{F{number}}}") for number in range(1, 13)],
            ("Ctrl+A", "^a"), ("Ctrl+C", "^c"), ("Ctrl+V", "^v"),
            ("Alt+F4", "!{F4}"), ("Shift+Tab", "+{Tab}"),
        ]
        labels = [label for label, _token in choices]
        selected, accepted = QtWidgets.QInputDialog.getItem(self, "기능키 추가", "보낼 키", labels, 0, False)
        if accepted:
            token = next((value for label, value in choices if label == selected), "")
            if token:
                self._append_type_text_key(token)

    def _open_image_search_test_center(self) -> None:
        from .image_search_test import ImageSearchTestDialog

        step = self.build_step()
        dialog = ImageSearchTestDialog(self.repository, step, self.window())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        applied = dict(dialog.applied_settings)
        for key, value in applied.items():
            self._set_field_value("image_search", key, value)
        if applied:
            summary = " · ".join(f"{key}={value}" for key, value in applied.items())
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(),
                f"테스트 추천값을 적용했습니다.\n{summary}",
                self,
            )

    def _set_field_value(self, action: str, key: str, value: Any) -> None:
        widget = self.widgets.get(action, {}).get(key)
        spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == key), None)
        if widget is not None and spec is not None:
            self._set_widget_value(widget, spec, value)
        if action == "pixel_search" and key == "color" and value:
            tol_w = self.widgets.get("pixel_search", {}).get("tolerance")
            if isinstance(tol_w, ColorToleranceBarWidget):
                tol_w.set_color(str(value).strip())

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
            if action == "mouse_click":
                self._set_field_value(action, "coordinate_scope", "screen")

    def _capture_program_cursor(self, action: str) -> None:
        """Bind one clicked point to its target application's client area."""
        if action not in {"mouse_click", "inactive_click"}:
            return
        hosts = self._hide_host_windows()
        picker = WindowPickerDialog(ignored_hwnds=self._host_hwnds(hosts))
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        client_point = picker.selected_client_point() if accepted else None
        self._restore_host_windows(hosts)
        if not accepted or client_point is None:
            return
        self._set_field_value(action, "x", client_point.x())
        self._set_field_value(action, "y", client_point.y())
        self._set_field_value(action, "window", picker.window_token)
        self._set_field_value(action, "window_exe", picker.exe_name)
        if action == "mouse_click":
            self._set_field_value(action, "coordinate_scope", "client")
            self._set_field_value(action, "window_hwnd", picker.window_hwnd)
        QtWidgets.QToolTip.showText(
            QtGui.QCursor.pos(),
            f"{picker.exe_name or picker.window_token} 기준 X {client_point.x()}, Y {client_point.y()} 저장",
            self,
        )

    def _capture_fail_click_cursor(self) -> None:
        hosts = self._hide_host_windows()
        picker = OffsetPointPickerDialog(single_click=True)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        offset = picker.offset() if accepted else [0, 0]
        self._restore_host_windows(hosts)
        if accepted:
            self._set_field_value("image_search", "fail_click_offset.0", offset[0])
            self._set_field_value("image_search", "fail_click_offset.1", offset[1])
            self._set_field_value("image_search", "fail_click_enabled", True)
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(),
                f"실패 시 대체 클릭 좌표 X {offset[0]}, Y {offset[1]} 설정 완료",
                self,
            )

    def _capture_pixel_search_offset(self, action: str = "pixel_search") -> None:
        hosts = self._hide_host_windows()
        is_ocr = (action == "ocr")
        ref_label = "1 · 인식 텍스트 위치" if is_ocr else "1 · 기준 픽셀 위치"
        step1_hint = (
            "1/2  화면에서 인식할 텍스트 기준 위치를 마우스 좌클릭하세요   ·   Esc 취소"
            if is_ocr
            else "1/2  화면에서 검색할 기준 픽셀(색상) 위치를 마우스 좌클릭하세요   ·   Esc 취소"
        )
        picker = OffsetPointPickerDialog(
            parent=None,
            single_click=False,
            reference_label=ref_label,
            click_label="2 · 실제 클릭 위치",
            instruction_step1=step1_hint,
            instruction_step2="2/2  실제 마우스가 클릭할 목표 위치를 좌클릭하세요   ·   오프셋 X {dx}px  Y {dy}px   ·   Esc 취소",
        )
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        offset = picker.offset() if accepted else [0, 0]
        self._restore_host_windows(hosts)
        if accepted:
            self._set_field_value(action, "click_offset_x", int(offset[0]))
            self._set_field_value(action, "click_offset_y", int(offset[1]))
            if action == "pixel_search":
                self._set_field_value(action, "action_on_found", "click")
            sign_x = f"{offset[0]:+d}" if offset[0] != 0 else "0"
            sign_y = f"{offset[1]:+d}" if offset[1] != 0 else "0"
            label = "OCR 텍스트" if is_ocr else "픽셀"
            suffix = " (동작: 발견 위치 클릭)" if action == "pixel_search" else ""
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(),
                f"{label} 클릭 오프셋 X: {sign_x}px, Y: {sign_y}px 설정 완료{suffix}",
                self,
            )

    def _reset_pixel_search_offset(self, action: str = "pixel_search") -> None:
        self._set_field_value(action, "click_offset_x", 0)
        self._set_field_value(action, "click_offset_y", 0)
        QtWidgets.QToolTip.showText(
            QtGui.QCursor.pos(),
            "클릭 오프셋을 X: 0, Y: 0으로 초기화했습니다. (발견 위치 그대로 클릭)",
            self,
        )

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

    def _open_ocr_filter_tuner(self, action: str) -> None:
        """Open interactive preview tuner for OCR image preprocessing filters."""
        step = self.build_step()
        asset_name = str(step.get("asset") or "")
        pixmap = None
        if asset_name and self.repository:
            pixmap = self.repository.asset_pixmap(asset_name)
        if pixmap is None or pixmap.isNull():
            region_val = step.get("region")
            from .automation import capture_virtual_desktop
            desk_pix, desk_geom = capture_virtual_desktop()
            if isinstance(region_val, list) and len(region_val) >= 4 and int(region_val[2]) > int(region_val[0]):
                l, t, r, b = int(region_val[0]), int(region_val[1]), int(region_val[2]), int(region_val[3])
                w, h = max(10, r - l), max(10, b - t)
                pixmap = desk_pix.copy(l, t, w, h)
            else:
                pixmap = desk_pix.copy(100, 100, 400, 150)
        dlg = OCRFilterTuningDialog(pixmap, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            # Set profile to precise or update preprocessing parameters
            w_profile = self.widgets.get(action, {}).get("profile")
            if isinstance(w_profile, QtWidgets.QComboBox):
                idx = w_profile.findData("precise")
                if idx >= 0:
                    w_profile.setCurrentIndex(idx)

    def _pick_pixel_color(self, action: str) -> None:
        from .automation import capture_virtual_desktop, PixelColorPickerDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = PixelColorPickerDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        color = picker.selected_color()
        self._restore_host_windows(hosts)
        if accepted and color:
            hex_val = color.name().upper()
            self._set_field_value(action, "color", hex_val)
            tol_w = self.widgets.get(action, {}).get("tolerance")
            if isinstance(tol_w, ColorToleranceBarWidget):
                tol_w.set_color(hex_val)

    def _preset_color_multi_count_all(self) -> None:
        self._set_field_value("pixel_search", "match_condition", "all_matched")
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "멀티 색상 모두 발견 시 성공으로 설정되었습니다.")

    def _pick_wait_color(self, action: str) -> None:
        from .automation import capture_virtual_desktop, PixelColorPickerDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = PixelColorPickerDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        color = picker.selected_color()
        pt = picker.selected_point()
        self._restore_host_windows(hosts)
        if accepted and color and pt:
            self._set_field_value(action, "target_color", color.name().upper())
            self._set_field_value(action, "x", pt.x())
            self._set_field_value(action, "y", pt.y())

    def _pick_gauge_color(self, action: str) -> None:
        from .automation import capture_virtual_desktop, PixelColorPickerDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = PixelColorPickerDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        color = picker.selected_color()
        self._restore_host_windows(hosts)
        if accepted and color:
            self._set_field_value(action, "target_color", color.name().upper())

    def _pick_multi_pixels(self, action: str) -> None:
        from .automation import capture_virtual_desktop, MultiPixelPickerDialog
        import json
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = MultiPixelPickerDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        pts = picker.selected_points() if accepted else []
        self._restore_host_windows(hosts)
        if accepted and pts:
            self._set_field_value(action, "pixels", json.dumps(pts, ensure_ascii=False))

    def _pick_ocr_track_target(self, action: str) -> None:
        from .automation import capture_virtual_desktop, ScreenCaptureDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = ScreenCaptureDialog(pixmap, geometry, hint_text="추적할 대상 이미지를 드래그 선택 후 Enter")
        try:
            accepted = picker.exec() == QtWidgets.QDialog.Accepted
            img = picker.captured_image() if accepted else QtGui.QImage()
        finally:
            picker.deleteLater()
        self._restore_host_windows(hosts)
        if accepted and not img.isNull():
            alias = f"track-target-{datetime.now():%Y%m%d-%H%M%S}"
            self.repository.add_asset_image(img, alias)
            self.refresh_sources()
            self._set_field_value(action, "tracking_asset", alias)

    def _pick_ocr_track_offset(self, action: str) -> None:
        from .automation import capture_virtual_desktop, ScreenCaptureDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker1 = ScreenCaptureDialog(pixmap, geometry, hint_text="[ 1단계 ] 추적 기준 영역 드래그 선택 후 Enter")
        try:
            if picker1.exec() != QtWidgets.QDialog.Accepted:
                self._restore_host_windows(hosts)
                return
            ref_rect = picker1.selected_screen_rect()
        finally:
            picker1.deleteLater()
        picker2 = ScreenCaptureDialog(pixmap, geometry, hint_text="[ 2단계 ] OCR 수행할 텍스트 영역 드래그 선택 후 Enter")
        try:
            accepted = picker2.exec() == QtWidgets.QDialog.Accepted
            ocr_rect = picker2.selected_screen_rect() if accepted else QtCore.QRect()
        finally:
            picker2.deleteLater()
        self._restore_host_windows(hosts)
        if accepted and ref_rect.isValid() and ocr_rect.isValid():
            self._set_field_value(action, "ocr_offset_x", ocr_rect.left() - ref_rect.left())
            self._set_field_value(action, "ocr_offset_y", ocr_rect.top() - ref_rect.top())
            self._set_field_value(action, "ocr_width", ocr_rect.width())
            self._set_field_value(action, "ocr_height", ocr_rect.height())

    def _pick_ocr_track_color(self, action: str) -> None:
        from .automation import capture_virtual_desktop, PixelColorPickerDialog
        hosts = self._hide_host_windows()
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = PixelColorPickerDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        color = picker.selected_color()
        self._restore_host_windows(hosts)
        if accepted and color:
            self._set_field_value(action, "tracking_color", color.name().upper())

    def _build_set_var_helpers(self, action: str) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("💡 초보자용 변수 추천 & 빠른 설정 (클릭 시 자동 입력)")
        box.setStyleSheet("QGroupBox { font-weight: 700; color: #C47CFF; border: 1px solid #3A2D54; border-radius: 6px; margin-top: 6px; padding: 6px; }")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(6)
        btn_count0 = QtWidgets.QPushButton("🔢 Count = 0 초기화")
        btn_count0.setToolTip("반복 횟수를 셀 때 사용할 카운트 변수를 0으로 생성합니다.")
        btn_count0.clicked.connect(lambda: self._set_var_fields("Count", "0"))

        btn_time = QtWidgets.QPushButton("⏱ StartTime = %A_TickCount%")
        btn_time.setToolTip("현재 밀리초 시각을 저장해 매크로 경과 시간을 측정할 수 있도록 합니다.")
        btn_time.clicked.connect(lambda: self._set_var_fields("StartTime", "%A_TickCount%"))

        btn_flag = QtWidgets.QPushButton("🚩 IsSuccess = 1 (성공 플래그)")
        btn_flag.setToolTip("작업 성공 여부를 나타내는 플래그 변수를 1로 설정합니다.")
        btn_flag.clicked.connect(lambda: self._set_var_fields("IsSuccess", "1"))

        row1.addWidget(btn_count0)
        row1.addWidget(btn_time)
        row1.addWidget(btn_flag)
        layout.addLayout(row1)

        chips_box = QtWidgets.QHBoxLayout()
        chips_lbl = QtWidgets.QLabel("추천 변수명:")
        chips_lbl.setStyleSheet("color: #8A98B0; font-size: 8pt;")
        chips_box.addWidget(chips_lbl)

        rec_vars = [
            ("Count", "반복 카운트"),
            ("LoopIndex", "루프 회차"),
            ("FoundX", "X 좌표"),
            ("FoundY", "Y 좌표"),
            ("OCR_Result", "인식 텍스트"),
            ("OCR_Number", "인식 숫자"),
            ("GaugePercent", "게이지 %"),
            ("CurrentTime", "현재 시각"),
        ]
        for vname, tip in rec_vars:
            chip = QtWidgets.QPushButton(vname)
            chip.setFixedHeight(22)
            chip.setStyleSheet("QPushButton { background: #1C1929; color: #D8B4FE; border: 1px solid #4C386A; border-radius: 4px; font-size: 8pt; padding: 0 6px; } QPushButton:hover { background: #2D2244; color: #FFFFFF; }")
            chip.setToolTip(f"{vname} ({tip}) 변수명을 이름에 입력")
            chip.clicked.connect(lambda _c=False, vn=vname: self._set_var_name_field(vn))
            chips_box.addWidget(chip)
        chips_box.addStretch(1)
        layout.addLayout(chips_box)
        return box

    def _set_var_fields(self, name: str, value: str) -> None:
        self._set_field_value("set_var", "name", name)
        self._set_field_value("set_var", "value", value)

    def _set_var_name_field(self, name: str) -> None:
        if self.current_action in {"set_var", "calc_var"}:
            self._set_field_value(self.current_action, "name", name)

    def _build_calc_var_helpers(self, action: str) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox("💡 초보자용 비주얼 계산 프리셋 (클릭하면 수식 자동 완성)")
        box.setStyleSheet("QGroupBox { font-weight: 700; color: #C47CFF; border: 1px solid #3A2D54; border-radius: 6px; margin-top: 6px; padding: 6px; }")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        row1 = QtWidgets.QHBoxLayout()
        btn_inc = QtWidgets.QPushButton("➕ 카운트 1 증가 (Count + 1)")
        btn_inc.setToolTip("변수 Count의 값을 1 증가시킵니다.")
        btn_inc.clicked.connect(lambda: self._set_calc_fields("Count", "Count + 1"))

        btn_dec = QtWidgets.QPushButton("➖ 카운트 1 감소 (Count - 1)")
        btn_dec.setToolTip("변수 Count의 값을 1 감소시킵니다.")
        btn_dec.clicked.connect(lambda: self._set_calc_fields("Count", "Count - 1"))

        btn_rnd = QtWidgets.QPushButton("🎲 랜덤 난수 생성 (1~100)")
        btn_rnd.setToolTip("1부터 100 사이의 무작위 난수를 생성해 변수에 저장합니다.")
        btn_rnd.clicked.connect(lambda: self._set_calc_fields("RandomVal", "Random(1, 100)"))

        row1.addWidget(btn_inc)
        row1.addWidget(btn_dec)
        row1.addWidget(btn_rnd)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        btn_time = QtWidgets.QPushButton("⏱ 경과 시간(초) 계산")
        btn_time.setToolTip("StartTime 기준으로 현재까지 경과한 시간을 초(소수점 1자리) 단위로 계산합니다.")
        btn_time.clicked.connect(lambda: self._set_calc_fields("ElapsedSec", "Round((A_TickCount - StartTime) / 1000, 1)"))

        btn_join = QtWidgets.QPushButton("🧩 텍스트 합치기 (A + B)")
        btn_join.setToolTip("두 텍스트 또는 변수를 하나로 결합합니다.")
        btn_join.clicked.connect(lambda: self._set_calc_fields("CombinedText", 'VarA . "_" . VarB'))

        btn_num = QtWidgets.QPushButton("✂ 숫자만 추출")
        btn_num.setToolTip("문자열에서 숫자만 남기고 제거합니다.")
        btn_num.clicked.connect(lambda: self._set_calc_fields("CleanNum", 'RegExReplace(OCR_Text, "[^0-9]", "")'))

        row2.addWidget(btn_time)
        row2.addWidget(btn_join)
        row2.addWidget(btn_num)
        layout.addLayout(row2)
        return box

    def _set_calc_fields(self, name: str, expr: str) -> None:
        self._set_field_value("calc_var", "name", name)
        self._set_field_value("calc_var", "expr", expr)

    def _pick_region(self, action: str, key: str) -> None:
        hosts = self._hide_host_windows()
        ignored_hwnds = self._host_hwnds(hosts)
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            self._restore_host_windows(hosts)
            return
        picker = ScreenCaptureDialog(pixmap, geometry)
        try:
            accepted = picker.exec() == QtWidgets.QDialog.Accepted
            rect = picker.selected_screen_rect() if accepted else QtCore.QRect()
        finally:
            picker.deleteLater()
        self._restore_host_windows(hosts)
        if rect.isValid() and rect.width() >= 4 and rect.height() >= 4:
            # Check if this action uses client/relative coordinates
            is_client = False
            mode_widget = self.widgets.get(action, {}).get("region_mode")
            if mode_widget is not None:
                spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == "region_mode"), None)
                if spec:
                    mode_val = str(self._widget_value(mode_widget, spec) or "").lower()
                    if mode_val in {"client", "window"}:
                        is_client = True

            coords_widget = self.widgets.get(action, {}).get("region_coords")
            if coords_widget is not None:
                spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == "region_coords"), None)
                if spec:
                    coords_val = str(self._widget_value(coords_widget, spec) or "").lower()
                    if coords_val == "relative":
                        is_client = True

            scope_widget = self.widgets.get(action, {}).get("coordinate_scope")
            if scope_widget is not None:
                spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == "coordinate_scope"), None)
                if spec:
                    scope_val = str(self._widget_value(scope_widget, spec) or "").lower()
                    if scope_val == "client":
                        is_client = True

            client_origin = None
            target = None

            # 1순위: 노드에 이미 대상 창이 지정되어 있다면, 해당 창을 최우선으로 찾아 겹침 여부 확인
            win_text = ""
            for wk in ("region_window", "window", "click.window"):
                w = self.widgets.get(action, {}).get(wk)
                if w:
                    spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == wk), None)
                    if spec:
                        val = str(self._widget_value(w, spec) or "")
                        if val:
                            win_text = val
                            break
            exe_text = ""
            for ek in ("region_window_exe", "window_exe", "click.window_exe"):
                w = self.widgets.get(action, {}).get(ek)
                if w:
                    spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == ek), None)
                    if spec:
                        val = str(self._widget_value(w, spec) or "")
                        if val:
                            exe_text = val
                            break

            if win_text or exe_text:
                try:
                    from .image_search_test import _find_window
                    hwnd = _find_window(exe_text, win_text)
                    if hwnd and hwnd not in ignored_hwnds:
                        import ctypes, ctypes.wintypes as wt
                        user32 = ctypes.windll.user32
                        crect = wt.RECT()
                        origin_pt = wt.POINT(0, 0)
                        if user32.GetClientRect(hwnd, ctypes.byref(crect)) and user32.ClientToScreen(hwnd, ctypes.byref(origin_pt)):
                            client_w = crect.right - crect.left
                            client_h = crect.bottom - crect.top
                            c_rect = QtCore.QRect(origin_pt.x, origin_pt.y, client_w, client_h)
                            if c_rect.intersects(rect):
                                client_origin = [int(origin_pt.x), int(origin_pt.y)]
                                title_buf = ctypes.create_unicode_buffer(512)
                                user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
                                target = {"title": title_buf.value, "exe": exe_text or win_text, "window": win_text}
                except Exception:
                    pass

            # 2순위: 지정된 창이 없거나 드래그 영역과 겹치지 않으면, Z-Order 최상위(맨 앞) 창을 자동 감지
            if client_origin is None:
                target = self._window_target_at(rect.center(), ignored_hwnds)
                if target and target.get("client_origin"):
                    client_origin = target["client_origin"]

            # 프로그램 클라이언트 영역이 감지되었을 경우: 무조건 클라이언트 상대 모드로 자동 동기화
            if client_origin is not None:
                is_client = True
                if self.widgets.get(action, {}).get("region_mode") is not None:
                    self._set_field_value(action, "region_mode", "client")
                if self.widgets.get(action, {}).get("region_coords") is not None:
                    self._set_field_value(action, "region_coords", "relative")
                if self.widgets.get(action, {}).get("coordinate_scope") is not None:
                    self._set_field_value(action, "coordinate_scope", "client")

                # 대상 창 정보가 비어있으면 자동 바인딩
                if target:
                    target_win = str(target.get("window") or target.get("title") or "")
                    target_exe = str(target.get("exe") or "")
                    for wk in ("region_window", "window", "click.window"):
                        w = self.widgets.get(action, {}).get(wk)
                        if w is not None:
                            spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == wk), None)
                            cur_val = str(self._widget_value(w, spec) or "") if (w and spec) else ""
                            if not cur_val and target_win:
                                self._set_field_value(action, wk, target_win)
                    for ek in ("region_window_exe", "window_exe", "click.window_exe"):
                        w = self.widgets.get(action, {}).get(ek)
                        if w is not None:
                            spec = next((item for item in ACTION_FIELDS.get(action, []) if item.key == ek), None)
                            cur_val = str(self._widget_value(w, spec) or "") if (w and spec) else ""
                            if not cur_val and target_exe:
                                self._set_field_value(action, ek, target_exe)

            if is_client and client_origin:
                values = (
                    max(0, rect.left() - client_origin[0]),
                    max(0, rect.top() - client_origin[1]),
                    max(0, rect.right() - client_origin[0]),
                    max(0, rect.bottom() - client_origin[1]),
                )
            else:
                values = (rect.left(), rect.top(), rect.right(), rect.bottom())

            for offset, value in enumerate(values):
                self._set_field_value(action, f"{key}.{offset}", value)

            if is_client and client_origin:
                win_title = (target or {}).get("title") or (target or {}).get("class") or (target or {}).get("exe") or win_text or "대상 창"
                QtWidgets.QToolTip.showText(
                    QtGui.QCursor.pos(),
                    f"🎯 '{win_title}' 클라이언트 창 내부 영역 자동 보정\n"
                    f"상대 좌표: [{values[0]}, {values[1]}, {values[2]}, {values[3]}] 저장 완료 (오차 이탈 방지)",
                    self,
                )
            else:
                QtWidgets.QToolTip.showText(
                    QtGui.QCursor.pos(),
                    f"🖥️ 전체 화면 절대 좌표: [{values[0]}, {values[1]}, {values[2]}, {values[3]}] 저장 완료",
                    self,
                )

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
        try:
            accepted = picker.exec() == QtWidgets.QDialog.Accepted
            image = picker.captured_image() if accepted else QtGui.QImage()
            if accepted:
                self._last_capture_rect = picker.selected_screen_rect()
                if self._last_capture_rect.isValid():
                    self._last_capture_target = self._window_target_at(self._last_capture_rect.center(), ignored_hwnds)
        finally:
            picker.deleteLater()
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

    def _preset_multi_count_all(self) -> None:
        action = "image_search"
        picker = self.widgets.get(action, {}).get("assets")
        count = len(picker.value()) if isinstance(picker, MultiAssetPicker) else 0
        if count <= 1:
            raw_assets = self.original.get("assets") or []
            count = len(raw_assets) if isinstance(raw_assets, list) else 1
        count = max(1, count)

        self._set_field_value(action, "all_action", "count_only")
        self._set_field_value(action, "match_condition", "all_matched")
        self._set_field_value(action, "required_count", count)
        self._set_field_value(action, "store_count_var", "FoundCount")
        self._set_field_value(action, "click_enabled", False)

        QtWidgets.QMessageBox.information(
            self,
            "초보자 빠른 설정 완료",
            f"🎯 [멀티 이미지 {count}개 모두 발견 시 참(성공)] 설정이 완료되었습니다!\n\n"
            f"• 동작: 발견 개수만 카운트 (클릭 안 함)\n"
            f"• 조건: {count}개 모두 발견 시에만 참(녹색선) 분기\n"
            f"• 미만(0~{count-1}개) 발견 시: 거짓(빨간선) 분기\n"
            f"• 저장 변수: FoundCount (발견된 개수가 자동 저장됨)\n\n"
            "별도의 변수 설정 노드 없이 바로 사용하실 수 있습니다.",
        )

    def _open_region_visual_test(self) -> None:
        from .region_visual_test import RegionVisualTestDialog

        step = self.build_step()
        if self.current_action == "pixel_search" and not step.get("region_window_exe"):
            orig_exe = str(self.original.get("region_window_exe") or (self.original.get("click") or {}).get("window_exe") or "")
            orig_win = str(self.original.get("region_window") or (self.original.get("click") or {}).get("window") or "")
            if orig_exe:
                step["region_window_exe"] = orig_exe
                step["region_window"] = orig_win
                step.setdefault("region_mode", "client")
                step.setdefault("region_coords", "relative")

        dlg = RegionVisualTestDialog(step, self.repository, parent=self.window())
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        if self.current_action == "pixel_search":
            updated_color_regs = dlg.get_color_regions()
            updated_tols = dlg.get_color_tolerances()
            if updated_tols:
                tol_val = next(iter(updated_tols.values()))
                self._set_field_value("pixel_search", "tolerance", tol_val)
                self.original["color_tolerances"] = updated_tols
            if updated_color_regs:
                self.original["color_regions"] = updated_color_regs
            if dlg.step.get("region_window_exe"):
                we = str(dlg.step["region_window_exe"])
                w = str(dlg.step.get("region_window", ""))
                rm = str(dlg.step.get("region_mode", "client"))
                rc = str(dlg.step.get("region_coords", "relative"))
                self._set_field_value("pixel_search", "region_window_exe", we)
                self._set_field_value("pixel_search", "region_window", w)
                self._set_field_value("pixel_search", "region_mode", rm)
                self._set_field_value("pixel_search", "region_coords", rc)
                self.original["region_window_exe"] = we
                self.original["region_window"] = w
                self.original["region_mode"] = rm
                self.original["region_coords"] = rc
            bounding = dlg.get_bounding_region()
            if bounding:
                for offset, val in enumerate(bounding):
                    self._set_field_value("pixel_search", f"search_region.{offset}", val)
            QtWidgets.QMessageBox.information(
                self,
                "색상 및 영역 보정 완료",
                "검색 영역 검사기에서 조절한 대상 창, 허용 오차 및 검색 영역이 노드에 성공적으로 적용되었습니다!",
            )
            return

        updated_regs = dlg.get_asset_regions()
        if updated_regs:
            picker = self.widgets.get("image_search", {}).get("assets")
            if isinstance(picker, MultiAssetPicker):
                picker.set_asset_regions(updated_regs)
            bounding = dlg.get_bounding_region()
            if bounding:
                for offset, val in enumerate(bounding):
                    self._set_field_value("image_search", f"region.{offset}", val)
            QtWidgets.QMessageBox.information(
                self,
                "영역 보정 완료",
                "검색 영역 검사기에서 수정한 영역이 노드에 성공적으로 적용되었습니다!",
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
        elif spec.kind == "color_tolerance":
            widget = ColorToleranceBarWidget(tolerance=int(spec.default or 10))
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
        elif spec.kind == "date":
            widget = QtWidgets.QDateEdit()
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.setDate(QtCore.QDate.currentDate())
        elif spec.kind == "time":
            widget = QtWidgets.QTimeEdit()
            widget.setDisplayFormat("HH:mm")
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
        elif spec.placeholder and isinstance(target, QtWidgets.QPlainTextEdit):
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

    def _ensure_page(self, action: str) -> QtWidgets.QWidget:
        if action not in self.pages:
            page = self._build_page(action)
            self.pages[action] = page
            self.stack.addWidget(page)
            self._refresh_sources_for_action(action)
        return self.pages[action]

    def _get_sources(self):
        if not hasattr(self, "_cached_assets") or self._cached_assets is None:
            self._cached_assets = list(self.repository.load_assets())
            self._cached_asset_paths = {alias: self.repository.asset_path(alias) for alias in self._cached_assets}
            self._cached_macros = [summary.name for summary in self.repository.list_macros()]
            self._cached_tables = list(self.repository.load_tables())
        return self._cached_assets, self._cached_asset_paths, self._cached_macros, self._cached_tables

    def refresh_sources(self) -> None:
        self._cached_assets = None
        self._cached_asset_paths = None
        self._cached_macros = None
        self._cached_tables = None
        for action in list(self.pages.keys()):
            self._refresh_sources_for_action(action)

    def _refresh_sources_for_action(self, action: str) -> None:
        if action not in self.widgets:
            return
        assets, asset_paths, macros, tables = self._get_sources()
        for spec in ACTION_FIELDS.get(action, []):
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
        if action not in ACTION_LABELS and action not in self.pages:
            action = "mouse_click"
        page = self._ensure_page(action)
        self.current_action = action
        self.stack.setCurrentWidget(page)

    def load_step(self, step: dict[str, Any]) -> None:
        self.original = deepcopy(step)
        action = str(step.get("action") or "mouse_click")
        self.set_action(action)
        normalized = deepcopy(step)
        if action in ("image_search", "screen_condition"):
            regions = normalized.get("regions")
            if isinstance(regions, list) and regions:
                r0 = regions[0]
                if isinstance(r0, list) and len(r0) >= 4 and r0[2] > r0[0] and r0[3] > r0[1]:
                    normalized["region"] = list(r0)
                if len(regions) > 1:
                    normalized["region2"] = list(regions[1])
            if action == "image_search":
                normalized["click_enabled"] = isinstance(normalized.get("click"), dict) and bool(normalized.get("click"))
        if action == "ocr":
            if not normalized.get("region") and normalized.get("search_region"):
                normalized["region"] = list(normalized["search_region"])
            elif not normalized.get("search_region") and normalized.get("region"):
                normalized["search_region"] = list(normalized["region"])
        if action == "wait_color":
            if not normalized.get("search_region") and normalized.get("region"):
                normalized["search_region"] = list(normalized["region"])
        if action == "text_condition":
            needles = normalized.get("needles")
            normalized["needles_text"] = ", ".join(str(item) for item in needles) if isinstance(needles, list) else str(needles or "")
        if action == "call_submacro":
            for field, text_field in (("inputs", "inputs_text"), ("outputs", "outputs_text")):
                mapping = normalized.get(field)
                if isinstance(mapping, dict):
                    normalized[text_field] = "\n".join(f"{key}={value}" for key, value in mapping.items())
        if action == "datetime_condition":
            if "date_start_enabled" not in normalized:
                normalized["date_start_enabled"] = bool(str(normalized.get("date_start") or "").strip())
            if "date_end_enabled" not in normalized:
                normalized["date_end_enabled"] = bool(str(normalized.get("date_end") or "").strip())
            if "time_end_enabled" not in normalized:
                normalized["time_end_enabled"] = bool(str(normalized.get("time_end") or "").strip())
            if "weekday_enabled" not in normalized:
                normalized["weekday_enabled"] = str(normalized.get("day_mode") or "everyday") != "everyday"
            legacy_mode = str(normalized.get("day_mode") or "everyday")
            legacy_days = str(normalized.get("custom_days") or "")
            selected_days = set("월화수목금") if legacy_mode == "weekdays" else set("토일") if legacy_mode == "weekend" else set(legacy_days) if legacy_mode == "custom" else set("월화수목금토일")
            for key, label in (("weekday_mon", "월"), ("weekday_tue", "화"), ("weekday_wed", "수"), ("weekday_thu", "목"), ("weekday_fri", "금"), ("weekday_sat", "토"), ("weekday_sun", "일")):
                normalized.setdefault(key, label in selected_days)
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
                picker.set_confidences(normalized.get("confidence", 86), normalized.get("asset_confidences") or {})
                picker.set_asset_regions(normalized.get("asset_regions") or {})
            self._update_offset_preview()
        elif action == "datetime_condition":
            self._sync_datetime_controls()
        elif action == "pixel_search":
            color_val = str(normalized.get("color") or "#FF0000").strip()
            tol_w = self.widgets.get("pixel_search", {}).get("tolerance")
            if isinstance(tol_w, ColorToleranceBarWidget):
                tol_w.setColor(color_val)
        if action in {"image_search", "screen_condition"}:
            current_engine = str(normalized.get("engine") or "opencv").lower()
            setattr(self, f"_last_engine_{action}", current_engine)
            if not hasattr(self, "_engine_configs"):
                self._engine_configs = {}
            if action not in self._engine_configs:
                self._engine_configs[action] = {}
            self._engine_configs[action][current_engine] = {
                "search_profile": normalized.get("search_profile", "fast"),
                "variation": normalized.get("variation", 16),
                "confidence": normalized.get("confidence", 84),
                "timeout": normalized.get("timeout", 1200),
                "poll_delay": normalized.get("poll_delay", 35),
            }
            self._update_engine_controls_state(action)
        self._current_search_preset = normalized.get("search_preset", "")

    def build_step(self) -> dict[str, Any]:
        action = self.current_action
        payload = deepcopy(self.original) if self.original.get("action") == action else action_template(action)
        if getattr(self, "_current_search_preset", None):
            payload["search_preset"] = self._current_search_preset
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
            # Always respect the user's chosen engine from the combobox
            engine_widget = self.widgets[action].get("engine")
            chosen_engine = str(self._widget_value(engine_widget, FieldSpec("engine", "", "choice", "opencv")) or "opencv").lower() if engine_widget else "opencv"
            payload["engine"] = chosen_engine

            multi_assets = [str(value) for value in payload.get("assets") or [] if str(value).strip()]
            primary_asset = str(payload.get("asset") or "").strip()
            if len(multi_assets) > 1:
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
                    conf, asset_confs = picker.confidences()
                    if asset_confs:
                        payload["asset_confidences"] = asset_confs
                    asset_regs = picker.asset_regions()
                    if asset_regs:
                        payload["asset_regions"] = asset_regs
                        try:
                            min_l = min(int(r[0]) for r in asset_regs.values())
                            min_t = min(int(r[1]) for r in asset_regs.values())
                            max_r = max(int(r[2]) for r in asset_regs.values())
                            max_b = max(int(r[3]) for r in asset_regs.values())
                            if max_r > min_l and max_b > min_t:
                                payload["region"] = [min_l, min_t, max_r, max_b]
                                payload["region_mode"] = "client"
                                payload["region_coords"] = "relative"
                        except Exception:
                            pass
                    else:
                        payload.pop("asset_regions", None)
            elif len(multi_assets) == 1:
                payload["asset"] = multi_assets[0]
                payload.pop("assets", None)
                payload.pop("asset_offsets", None)
                payload.pop("asset_confidences", None)
                payload.pop("asset_regions", None)
            else:
                payload.pop("assets", None)
                payload.pop("asset_offsets", None)
                payload.pop("asset_confidences", None)
                payload.pop("asset_regions", None)
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
        elif action == "screen_condition":
            region = payload.get("region")
            def valid_region(value: Any) -> bool:
                if not isinstance(value, list) or len(value) < 4:
                    return False
                try:
                    left, top, right, bottom = (int(item or 0) for item in value[:4])
                except (TypeError, ValueError):
                    return False
                return right > left and bottom > top

            if valid_region(region):
                payload["regions"] = [region]
                payload["region"] = region
            else:
                payload.pop("regions", None)
                payload.pop("region", None)
        elif action == "datetime_condition":
            day_pairs = (
                ("weekday_mon", "월"), ("weekday_tue", "화"), ("weekday_wed", "수"),
                ("weekday_thu", "목"), ("weekday_fri", "금"),
                ("weekday_sat", "토"), ("weekday_sun", "일"),
            )
            if bool(payload.get("weekday_enabled")):
                payload["day_mode"] = "custom"
                payload["custom_days"] = ",".join(label for key, label in day_pairs if bool(payload.get(key)))
            else:
                payload["day_mode"] = "everyday"
                payload["custom_days"] = ""
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
        if action == "call_submacro":
            for text_field, field in (("inputs_text", "inputs"), ("outputs_text", "outputs")):
                raw = str(payload.pop(text_field, "") or "")
                mapping: dict[str, str] = {}
                for line in raw.replace(";", "\n").splitlines():
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip().lstrip("$")
                    value = value.strip()
                    if key and value:
                        mapping[key] = value
                if mapping:
                    payload[field] = mapping
                else:
                    payload.pop(field, None)
        if action == "ocr" and payload.get("region"):
            payload["search_region"] = list(payload["region"])
            payload["regions"] = [list(payload["region"])]
        return payload

    def _set_widget_value(self, widget: QtWidgets.QWidget, spec: FieldSpec, value: Any) -> None:
        target = self._value_widget(widget)
        if isinstance(target, OffsetEditor):
            target.set_value(value)
        elif isinstance(target, MultiAssetPicker):
            target.set_value(value)
        elif isinstance(target, ColorToleranceBarWidget):
            try:
                target.set_tolerance(int(value or spec.default or 10))
            except (TypeError, ValueError):
                target.set_tolerance(int(spec.default or 10))
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
            target.moveCursor(QtGui.QTextCursor.End)
        elif isinstance(target, QtWidgets.QDateEdit):
            parsed = QtCore.QDate.fromString(str(value or ""), "yyyy-MM-dd")
            target.setDate(parsed if parsed.isValid() else QtCore.QDate.currentDate())
        elif isinstance(target, QtWidgets.QTimeEdit):
            parsed = QtCore.QTime.fromString(str(value or spec.default or "00:00"), "HH:mm")
            if not parsed.isValid():
                parsed = QtCore.QTime.fromString(str(value or spec.default or "00:00"), "H:mm")
            target.setTime(parsed if parsed.isValid() else QtCore.QTime(0, 0))
        elif isinstance(target, QtWidgets.QLineEdit):
            target.setText(str(value if value is not None else ""))

    def _widget_value(self, widget: QtWidgets.QWidget, spec: FieldSpec) -> Any:
        target = self._value_widget(widget)
        if isinstance(target, OffsetEditor):
            return target.value()
        if isinstance(target, MultiAssetPicker):
            return target.value()
        if isinstance(target, ColorToleranceBarWidget):
            return target.tolerance()
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
        if isinstance(target, QtWidgets.QDateEdit):
            return target.date().toString("yyyy-MM-dd")
        if isinstance(target, QtWidgets.QTimeEdit):
            return target.time().toString("HH:mm")
        if isinstance(target, QtWidgets.QLineEdit):
            return target.text().strip() if spec.kind != "multiline" else target.text()
        return spec.default


class ActionEditorDialog(QtWidgets.QDialog):
    def __init__(self, repository: MacroRepository, step: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        action = str(step.get("action") or "wait")
        raw_colors = step.get("colors") if isinstance(step.get("colors"), list) else []
        is_multi_pixel = (
            action == "pixel_search"
            and (
                len(raw_colors) > 1
                or bool((step.get("_automation") or {}).get("manual_multi_merge"))
                or "멀티" in str(step.get("label") or "")
            )
        )
        if action == "image_search" and len(step.get("assets") or []) > 1:
            action_name = "멀티 이미지 서치"
        elif is_multi_pixel:
            count = len(raw_colors) or int((step.get("_automation") or {}).get("color_count") or 1)
            action_name = f"멀티 색상 서치 ({count}개)" if count > 1 else "멀티 색상 서치"
        else:
            action_name = ACTION_LABELS.get(action, action)
        self.setWindowTitle(f"{action_name} · 상세 설정")
        self.setMinimumSize(860, 640)

        settings = QtCore.QSettings("MacroRelay", "MacroStudio")
        saved_size = settings.value("action_editor_dialog_size")
        cursor_pos = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(cursor_pos) or QtGui.QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1920, 1080)

        if isinstance(saved_size, QtCore.QSize) and saved_size.width() >= 900 and saved_size.height() >= 700:
            target_w = min(saved_size.width(), avail.width() - 40)
            target_h = min(saved_size.height(), avail.height() - 40)
        else:
            target_w = min(1060, max(940, int(avail.width() * 0.70)))
            target_h = min(900, max(720, int(avail.height() * 0.85)))
        self.resize(target_w, target_h)

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(action_name)
        title.setStyleSheet("font-size:17pt; font-weight:800; color:#38E7FF;" if is_multi_pixel else "font-size:17pt; font-weight:800;")
        hint_text = (
            "멀티 색상 서치 노드의 조건을 편집합니다. 여러 색상의 개별 오차 및 영역은 '색상 서치 / 영역 검증' 창에서 시각적으로 확인하고 미세조정할 수 있습니다."
            if is_multi_pixel
            else "긴 설정을 넓은 창에서 편집합니다. '설정 저장'을 누르면 단계와 매크로 파일에 즉시 반영됩니다."
        )
        hint = QtWidgets.QLabel(hint_text)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        self.multi_color_card: QtWidgets.QFrame | None = None
        if is_multi_pixel:
            self.multi_color_card = QtWidgets.QFrame()
            self.multi_color_card.setObjectName("MultiColorSummaryCard")
            self.multi_color_card.setStyleSheet("""
                QFrame#MultiColorSummaryCard {
                    background: #141A26;
                    border: 1px solid #2B384E;
                    border-left: 4px solid #FF6B9D;
                    border-radius: 8px;
                    padding: 4px;
                }
            """)
            layout.addWidget(self.multi_color_card)
            self._build_multi_color_card(step)

        self.editor = ActionEditor(repository)
        self.editor.refresh_sources()
        self.editor.load_step(step)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("설정 저장")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)

    def _build_multi_color_card(self, step: dict[str, Any]) -> None:
        if not self.multi_color_card:
            return
        if self.multi_color_card.layout() is not None:
            QtWidgets.QWidget().setLayout(self.multi_color_card.layout())
        card_layout = QtWidgets.QVBoxLayout(self.multi_color_card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(6)

        header_row = QtWidgets.QHBoxLayout()
        raw_colors = step.get("colors") if isinstance(step.get("colors"), list) else []
        tols = step.get("color_tolerances") if isinstance(step.get("color_tolerances"), dict) else {}
        regs = step.get("color_regions") if isinstance(step.get("color_regions"), dict) else {}
        base_tol = int(step.get("tolerance") or 10)
        base_reg = step.get("search_region") or step.get("region") or [0, 0, 0, 0]

        cond = str(step.get("match_condition") or "all_matched")
        cond_labels = {
            "all_matched": "모든 색상 일치 시 참 (AND)",
            "at_least_1": "1개 이상 일치 시 참 (OR)",
            "at_least_n": f"{step.get('required_count', len(raw_colors))}개 이상 일치 시 참",
            "exact_n": f"정확히 {step.get('required_count', len(raw_colors))}개 일치 시 참",
        }
        cond_text = cond_labels.get(cond, cond)

        title_lbl = QtWidgets.QLabel(f"🎨 <b>멀티 색상 그룹</b> ({len(raw_colors)}개 색상) · 판정 조건: <span style='color:#38E7FF;'>{cond_text}</span>")
        title_lbl.setStyleSheet("font-size: 9.3pt; color: #E2E8F0;")
        header_row.addWidget(title_lbl, 1)

        btn_verify = QtWidgets.QPushButton("🔍 색상 서치 / 영역 검증 (미리보기 설정창 열기)")
        btn_verify.setStyleSheet("""
            QPushButton {
                background: #1A365D;
                color: #63B3ED;
                border: 1px solid #2B6CB0;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: bold;
                font-size: 8.8pt;
            }
            QPushButton:hover {
                background: #2B6CB0;
                color: #FFFFFF;
            }
        """)
        btn_verify.clicked.connect(self._open_region_visual_test_and_refresh)
        header_row.addWidget(btn_verify)
        card_layout.addLayout(header_row)

        chips_layout = QtWidgets.QHBoxLayout()
        chips_layout.setSpacing(8)
        for idx, col in enumerate(raw_colors, 1):
            col_str = str(col).strip()
            tol_val = tols.get(col_str, base_tol)
            reg_val = regs.get(col_str, base_reg)
            reg_str = f"[{reg_val[0]}, {reg_val[1]}, {reg_val[2]}, {reg_val[3]}]" if len(reg_val) >= 4 else "전체"

            chip = QtWidgets.QFrame()
            chip.setStyleSheet("""
                QFrame {
                    background: #1A2234;
                    border: 1px solid #2E3E5B;
                    border-radius: 6px;
                    padding: 2px 6px;
                }
            """)
            c_layout = QtWidgets.QHBoxLayout(chip)
            c_layout.setContentsMargins(4, 2, 4, 2)
            c_layout.setSpacing(6)

            idx_lbl = QtWidgets.QLabel(f"<b>#{idx}</b>")
            idx_lbl.setStyleSheet("color: #A0AEC0; font-size: 8.5pt;")
            c_layout.addWidget(idx_lbl)

            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(16, 16)
            swatch.setStyleSheet(f"background-color: {col_str}; border-radius: 3px; border: 1px solid #FFFFFF;")
            c_layout.addWidget(swatch)

            hex_lbl = QtWidgets.QLabel(f"<code>{col_str}</code>")
            hex_lbl.setStyleSheet("color: #38E7FF; font-weight: bold; font-size: 8.5pt;")
            c_layout.addWidget(hex_lbl)

            tol_lbl = QtWidgets.QLabel(f"오차 ±{tol_val}")
            tol_lbl.setStyleSheet("color: #F6AD55; font-size: 8.2pt;")
            c_layout.addWidget(tol_lbl)

            reg_lbl = QtWidgets.QLabel(f"영역 {reg_str}")
            reg_lbl.setStyleSheet("color: #CBD5E0; font-size: 8.2pt;")
            c_layout.addWidget(reg_lbl)

            chips_layout.addWidget(chip)
        chips_layout.addStretch(1)
        card_layout.addLayout(chips_layout)

    def _open_region_visual_test_and_refresh(self) -> None:
        self.editor._open_region_visual_test()
        step = self.editor.build_step()
        self._build_multi_color_card(step)

    def done(self, r: int) -> None:
        try:
            settings = QtCore.QSettings("MacroRelay", "MacroStudio")
            settings.setValue("action_editor_dialog_size", self.size())
            settings.setValue("action_editor_dialog_pos", self.pos())
        except Exception:
            pass
        super().done(r)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._position_dialog)

    def _position_dialog(self) -> None:
        settings = QtCore.QSettings("MacroRelay", "MacroStudio")
        saved_pos = settings.value("action_editor_dialog_pos")
        dialog_size = self.size()
        w = dialog_size.width()
        h = dialog_size.height()

        parent = self.parent()
        parent_win = parent.window() if parent else None

        if isinstance(saved_pos, QtCore.QPoint) and any(
            s.availableGeometry().contains(saved_pos) for s in QtGui.QGuiApplication.screens()
        ):
            self.move(saved_pos)
            return

        if parent_win and parent_win.isVisible():
            p_geom = parent_win.geometry()
            x = p_geom.left() + max(10, (p_geom.width() - w) // 2)
            y = p_geom.top() + max(40, (p_geom.height() - h) // 2)
            self.move(x, y)
        else:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen:
                available = screen.availableGeometry()
                x = available.left() + (available.width() - w) // 2
                y = available.top() + (available.height() - h) // 2
                self.move(x, y)

    def payload(self) -> dict[str, Any]:
        return self.editor.build_step()

    def step(self) -> dict[str, Any]:
        return self.editor.build_step()

    def _open_region_visual_test(self) -> None:
        self.editor._open_region_visual_test()

