"""Standalone visual editor for QuickSlot deck pages and executable actions."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets


ACTION_LIBRARY: Dict[str, list[tuple[str, str, str]]] = {
    "시스템": [
        ("open_target", "실행", "프로그램·파일·폴더·웹사이트 실행"),
        ("terminate_program", "프로그램 종료", "지정 프로세스 종료"),
        ("hotkey", "단축키", "키 조합 보내기"),
        ("text", "텍스트 입력", "클립보드 기반 텍스트 입력"),
        ("mouse_click", "마우스 클릭", "대상 프로그램 좌표 클릭"),
        ("wait", "대기", "다음 동작 전 대기"),
        ("studio", "Studio 열기", "MacroRelay Studio 실행"),
        ("stop_all", "전체 중지", "실행 중 매크로 모두 중지"),
    ],
    "다중 작업": [
        ("run_macro", "매크로 실행", "등록 매크로 하나 실행"),
        ("multi_macros", "다중 매크로", "여러 매크로 순차·동시 실행"),
        ("switch_preset", "프리셋 전환", "다른 덱 프리셋 적용"),
    ],
    "페이지": [
        ("page_prev", "이전 페이지", "현재 덱의 이전 페이지"),
        ("page_next", "다음 페이지", "현재 덱의 다음 페이지"),
        ("page_goto", "페이지 이동", "지정 페이지로 바로 이동"),
        ("page_first", "첫 페이지", "첫 번째 페이지로 이동"),
    ],
}

ACTION_TITLES = {
    kind: title
    for entries in ACTION_LIBRARY.values()
    for kind, title, _description in entries
}

ACTION_ICONS = {
    "open_target": "↗", "terminate_program": "■", "hotkey": "⌨", "text": "T", "mouse_click": "🖱",
    "wait": "◷", "studio": "M", "stop_all": "⬛", "run_macro": "▶",
    "multi_macros": "≡", "switch_preset": "★", "page_prev": "◀",
    "page_next": "▶", "page_goto": "▦", "page_first": "Ⅰ",
}


def action_title(action: Dict[str, Any]) -> str:
    return str(action.get("label") or ACTION_TITLES.get(str(action.get("kind") or ""), "Deck 액션"))


def deck_dock_stylesheet() -> str:
    return """
        QMainWindow, QDialog { background: #181A1F; color: #F4F6FA; }
        QWidget { color: #E8ECF3; font-family: "Segoe UI", "Malgun Gothic"; }
        QFrame#Toolbar, QFrame#DeckCard, QFrame#PanelCard {
            background: #24272D; border: 1px solid #4B5059; border-radius: 13px;
        }
        QLabel#Title { font-size: 20pt; font-weight: 800; color: #FFFFFF; }
        QLabel#Hint { color: #9CA4B2; }
        QPushButton, QToolButton {
            background: #30343B; border: 1px solid #555B66; border-radius: 8px;
            color: #F2F4F8; padding: 7px 11px; font-weight: 700;
        }
        QPushButton:hover, QToolButton:hover { background: #3A4049; border-color: #16E0C1; }
        QPushButton:pressed { background: #202329; }
        QLineEdit, QComboBox, QSpinBox, QListWidget, QPlainTextEdit {
            background: #202329; border: 1px solid #555B66; border-radius: 8px;
            color: #F4F6FA; padding: 7px 9px;
        }
        QComboBox QAbstractItemView { background: #292D34; selection-background-color: #187E73; }
        QScrollArea { border: none; background: transparent; }
        QScrollBar:vertical { width: 9px; background: #202329; }
        QScrollBar::handle:vertical { background: #5B616C; border-radius: 4px; min-height: 28px; }
        QGroupBox {
            border: 1px solid #555B66; border-radius: 11px; margin-top: 12px;
            padding-top: 11px; font-weight: 800; color: #FFFFFF;
        }
        QCheckBox { color: #DEE3EA; spacing: 8px; }
    """


class ActionPaletteButton(QtWidgets.QFrame):
    def __init__(self, kind: str, title: str, description: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.kind = kind
        self._press_pos: Optional[QtCore.QPoint] = None
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setStyleSheet("QFrame { background:#343840; border:1px solid #565C66; border-radius:9px; } QFrame:hover { border-color:#16E0C1; background:#3A4049; }")
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        icon = QtWidgets.QLabel(ACTION_ICONS.get(kind, "•"))
        icon.setFixedWidth(26)
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet("color:#20E0C2; font-size:13pt; font-weight:900; border:none; background:transparent;")
        texts = QtWidgets.QVBoxLayout()
        name = QtWidgets.QLabel(title)
        name.setStyleSheet("font-weight:800; color:#FFFFFF; border:none; background:transparent;")
        desc = QtWidgets.QLabel(description)
        desc.setStyleSheet("font-size:8pt; color:#AEB5C0; border:none; background:transparent;")
        texts.addWidget(name)
        texts.addWidget(desc)
        row.addWidget(icon)
        row.addLayout(texts, 1)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_pos is not None and event.buttons() & QtCore.Qt.LeftButton:
            if (event.pos() - self._press_pos).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                drag = QtGui.QDrag(self)
                mime = QtCore.QMimeData()
                mime.setData("application/x-macrorelay-deck-action", self.kind.encode("utf-8"))
                drag.setMimeData(mime)
                drag.exec(QtCore.Qt.CopyAction)
                self._press_pos = None
                return
        super().mouseMoveEvent(event)


class DeckSlotButton(QtWidgets.QFrame):
    action_dropped = QtCore.Signal(int, str)
    slot_dropped = QtCore.Signal(int, int)
    image_dropped = QtCore.Signal(int, str)
    edit_requested = QtCore.Signal(int)
    clear_requested = QtCore.Signal(int)
    duplicate_requested = QtCore.Signal(int)
    selection_requested = QtCore.Signal(int)

    def __init__(self, slot_index: int, slot: Dict[str, Any], icon_config: Optional[Dict[str, Any]] = None, parent: Optional[QtWidgets.QWidget] = None, *, selection_mode: bool = False, selected: bool = False):
        super().__init__(parent)
        self.slot_index = slot_index
        self.slot = copy.deepcopy(slot)
        self.selection_mode = selection_mode
        self.selected = selected
        self._press_pos: Optional[QtCore.QPoint] = None
        self.setAcceptDrops(True)
        self.setMinimumSize(92, 82)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet("QFrame { background:#111318; border:1px solid #08090B; border-radius:9px; } QFrame:hover { border:2px solid #18DDC0; background:#171A20; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)
        action = dict(slot.get("action") or {})
        macro = str(slot.get("macro") or "").strip()
        icon_config = dict(icon_config or {})
        icon_text = str(icon_config.get("emoji") or ACTION_ICONS.get(str(action.get("kind") or ""), "＋" if not macro else "M"))
        icon = QtWidgets.QLabel(icon_text)
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet("font-size:17pt; color:#23E0C2; font-weight:900; background:transparent; border:none;")
        try:
            from macro_studio.quickslot_deck import load_pixmap_from_config

            pixmap = load_pixmap_from_config(icon_config)
            if not pixmap.isNull():
                icon.setText("")
                icon.setPixmap(pixmap.scaled(54, 54, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        except Exception:
            pass
        title = QtWidgets.QLabel(action_title(action) if action else (macro or "빈 슬롯"))
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size:8pt; color:#F4F6FA; font-weight:700; background:transparent; border:none;")
        layout.addWidget(icon, 1)
        layout.addWidget(title)
        if not macro:
            self.setStyleSheet("QFrame { background:#202329; border:1px dashed #555B66; border-radius:9px; } QFrame:hover { border:2px solid #18DDC0; }")
        if selected:
            self.setStyleSheet("QFrame { background:#173B38; border:3px solid #18DDC0; border-radius:9px; }")
            badge = QtWidgets.QLabel("✓", self)
            badge.setAlignment(QtCore.Qt.AlignCenter)
            badge.setFixedSize(22, 22)
            badge.move(5, 5)
            badge.setStyleSheet("background:#18DDC0;color:#07110F;border:none;border-radius:11px;font-weight:900;")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.selection_mode:
            return
        if self._press_pos is not None and event.buttons() & QtCore.Qt.LeftButton:
            if (event.pos() - self._press_pos).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                drag = QtGui.QDrag(self)
                mime = QtCore.QMimeData()
                mime.setData("application/x-macrorelay-deck-slot", str(self.slot_index).encode("ascii"))
                drag.setMimeData(mime)
                drag.exec(QtCore.Qt.MoveAction)
                self._press_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._press_pos is not None:
            if self.selection_mode:
                self.selection_requested.emit(self.slot_index)
            else:
                self.edit_requested.emit(self.slot_index)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        mime = event.mimeData()
        if (
            mime.hasFormat("application/x-macrorelay-deck-action")
            or mime.hasFormat("application/x-macrorelay-deck-slot")
            or self._first_local_image(mime)
        ):
            event.acceptProposedAction()

    @staticmethod
    def _first_local_image(mime: QtCore.QMimeData) -> str:
        supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}
        for url in mime.urls() if mime.hasUrls() else []:
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in supported:
                return path
        return ""

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat("application/x-macrorelay-deck-action"):
            kind = bytes(mime.data("application/x-macrorelay-deck-action")).decode("utf-8")
            self.action_dropped.emit(self.slot_index, kind)
            event.acceptProposedAction()
        elif mime.hasFormat("application/x-macrorelay-deck-slot"):
            source = int(bytes(mime.data("application/x-macrorelay-deck-slot")).decode("ascii"))
            self.slot_dropped.emit(source, self.slot_index)
            event.acceptProposedAction()
        else:
            image_path = self._first_local_image(mime)
            if image_path:
                self.image_dropped.emit(self.slot_index, image_path)
                event.acceptProposedAction()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = QtWidgets.QMenu(self)
        edit = menu.addAction("설정 편집")
        duplicate = menu.addAction("슬롯 복제")
        clear = menu.addAction("슬롯 비우기")
        selected = menu.exec(event.globalPos())
        if selected is edit:
            self.edit_requested.emit(self.slot_index)
        elif selected is duplicate:
            self.duplicate_requested.emit(self.slot_index)
        elif selected is clear:
            self.clear_requested.emit(self.slot_index)


class DeckActionConfigDialog(QtWidgets.QDialog):
    def __init__(
        self,
        kind: str,
        action: Optional[Dict[str, Any]],
        main_window: Any,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        slot_index: int = -1,
        icon_config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(parent)
        self.kind = kind
        self.action = copy.deepcopy(action or {})
        self.main_window = main_window
        self.repository = main_window.repository
        self.slot_index = slot_index
        self.icon_config = copy.deepcopy(icon_config or {})
        icon_source = str(self.icon_config.get("icon_source") or "")
        has_visual = self._icon_config_has_visual(self.icon_config)
        self._icon_manually_edited = icon_source == "manual" or (has_visual and icon_source != "program_auto")
        self.widgets: Dict[str, Any] = {}
        self.setWindowTitle(f"{ACTION_TITLES.get(kind, 'Deck 액션')} 설정")
        self.setMinimumWidth(500)
        self.setStyleSheet(deck_dock_stylesheet())
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(f"{ACTION_ICONS.get(kind, '•')}  {ACTION_TITLES.get(kind, 'Deck 액션')}")
        title.setStyleSheet("font-size:15pt; font-weight:900; color:#FFFFFF;")
        root.addWidget(title)
        form = QtWidgets.QFormLayout()
        form.setSpacing(11)
        label = QtWidgets.QLineEdit(str(self.action.get("label") or ""))
        label.setPlaceholderText("선택 사항 · 비워 두면 아이콘만 표시됩니다")
        self.widgets["label"] = label
        form.addRow("슬롯 이름", label)
        self._build_fields(form)
        root.addLayout(form)
        tools = QtWidgets.QHBoxLayout()
        self.btn_edit_icon = QtWidgets.QPushButton("🖼 아이콘 불러오기 및 편집")
        self.btn_edit_icon.clicked.connect(self._edit_icon)
        self.btn_test = QtWidgets.QPushButton("▶ 현재 설정 테스트")
        self.btn_test.setObjectName("TestAction")
        self.btn_test.clicked.connect(self._test_action)
        tools.addWidget(self.btn_edit_icon)
        tools.addStretch(1)
        tools.addWidget(self.btn_test)
        root.addLayout(tools)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_fields(self, form: QtWidgets.QFormLayout) -> None:
        kind = self.kind
        if kind == "open_target":
            edit = QtWidgets.QLineEdit(str(self.action.get("target") or ""))
            edit.setPlaceholderText("실행 파일, 폴더, 문서 또는 https:// 주소")
            browse = QtWidgets.QPushButton("찾기")
            browse.clicked.connect(lambda: self._browse_target(edit))
            edit.editingFinished.connect(lambda: self._set_icon_from_program(edit.text()))
            row = QtWidgets.QHBoxLayout(); row.addWidget(edit, 1); row.addWidget(browse)
            self.widgets["target"] = edit; form.addRow("대상", row)
        elif kind == "terminate_program":
            edit = QtWidgets.QLineEdit(str(self.action.get("process") or ""))
            edit.setPlaceholderText("예: notepad.exe")
            self.widgets["process"] = edit; form.addRow("프로세스 이름", edit)
        elif kind == "hotkey":
            edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(str(self.action.get("keys") or "Ctrl+Shift+A")))
            self.widgets["keys"] = edit
            form.addRow("키 조합", edit)
            self._add_target_fields(form)
        elif kind == "text":
            edit = QtWidgets.QPlainTextEdit(str(self.action.get("text") or "")); edit.setMaximumHeight(120)
            self.widgets["text"] = edit
            form.addRow("입력할 텍스트", edit)
            method = QtWidgets.QComboBox()
            method.addItem("자동 · 실행 방식에 맞춤", "auto")
            method.addItem("클립보드 붙여넣기 · 빠름", "clipboard")
            method.addItem("글자별 키 입력 · 실제 타이핑", "type")
            method.addItem("WM_CHAR · 비활성 문자 메시지", "wm_char")
            method.addItem("WM_SETTEXT · 입력 컨트롤 값 직접 설정", "set_text")
            method.setCurrentIndex(max(0, method.findData(str(self.action.get("text_method") or "auto"))))
            interval = QtWidgets.QSpinBox(); interval.setRange(0, 1000); interval.setSuffix(" ms"); interval.setValue(int(self.action.get("key_interval_ms") or 0))
            enter = QtWidgets.QCheckBox("입력 후 Enter 전송"); enter.setChecked(bool(self.action.get("press_enter", False)))
            self.widgets.update(text_method=method, key_interval_ms=interval, press_enter=enter)
            form.addRow("입력 엔진", method); form.addRow("글자 입력 간격", interval); form.addRow("완료 동작", enter)
            self._add_target_fields(form)
        elif kind == "mouse_click":
            button = QtWidgets.QComboBox()
            button.addItem("좌클릭", "left"); button.addItem("우클릭", "right"); button.addItem("가운데 클릭", "middle")
            button.setCurrentIndex(max(0, button.findData(str(self.action.get("button") or "left"))))
            clicks = QtWidgets.QSpinBox(); clicks.setRange(1, 3); clicks.setValue(int(self.action.get("clicks") or 1))
            self.widgets["button"] = button; self.widgets["clicks"] = clicks
            form.addRow("마우스 버튼", button); form.addRow("클릭 횟수", clicks)
            self._add_target_fields(form, include_point=True)
        elif kind == "wait":
            spin = QtWidgets.QSpinBox(); spin.setRange(10, 600000); spin.setSuffix(" ms"); spin.setValue(int(self.action.get("ms") or 1000))
            self.widgets["ms"] = spin; form.addRow("대기 시간", spin)
        elif kind == "run_macro":
            combo = QtWidgets.QComboBox(); combo.addItems([item.name for item in self.main_window.repository.list_macros()])
            current = str(self.action.get("macro") or ""); combo.setCurrentText(current)
            self.widgets["macro"] = combo; form.addRow("매크로", combo)
        elif kind == "multi_macros":
            selected_order = [str(value) for value in list(self.action.get("macros") or [])]
            selected = set(selected_order)
            listing = QtWidgets.QListWidget(); listing.setMinimumHeight(190)
            available = [summary.name for summary in self.main_window.repository.list_macros()]
            ordered = [name for name in selected_order if name in available] + [name for name in available if name not in selected]
            for name in ordered:
                item = QtWidgets.QListWidgetItem(name); item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsDragEnabled)
                item.setCheckState(QtCore.Qt.Checked if name in selected else QtCore.Qt.Unchecked); listing.addItem(item)
            listing.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
            listing.setDefaultDropAction(QtCore.Qt.MoveAction)
            listing.setToolTip("체크한 매크로를 드래그해 실행 순서를 변경할 수 있습니다.")
            mode = QtWidgets.QComboBox(); mode.addItem("순차 실행", "sequential"); mode.addItem("동시 실행", "parallel")
            mode.setCurrentIndex(max(0, mode.findData(str(self.action.get("mode") or "sequential"))))
            delay = QtWidgets.QSpinBox(); delay.setRange(0, 60000); delay.setSuffix(" ms"); delay.setValue(int(self.action.get("delay_ms") or 0))
            keep = QtWidgets.QCheckBox("한 작업이 실패해도 다음 작업 계속"); keep.setChecked(bool(self.action.get("continue_on_error", True)))
            self.widgets.update(macros=listing, mode=mode, delay_ms=delay, continue_on_error=keep)
            form.addRow("실행 매크로", listing); form.addRow("실행 방식", mode); form.addRow("작업 간격", delay); form.addRow("오류 처리", keep)
        elif kind == "switch_preset":
            combo = QtWidgets.QComboBox()
            for preset_id, preset in self.main_window.config.get("slot_presets", {}).items():
                combo.addItem(str(preset.get("name") or preset_id), preset_id)
            combo.setCurrentIndex(max(0, combo.findData(str(self.action.get("preset_id") or ""))))
            self.widgets["preset_id"] = combo; form.addRow("프리셋", combo)
        elif kind == "page_goto":
            spin = QtWidgets.QSpinBox(); spin.setRange(1, 99); spin.setValue(int(self.action.get("page") or 1))
            self.widgets["page"] = spin; form.addRow("이동할 페이지", spin)

    def _add_target_fields(self, form: QtWidgets.QFormLayout, *, include_point: bool = False) -> None:
        mode = QtWidgets.QComboBox()
        mode.addItem("비활성 · 창을 앞으로 가져오지 않음", "inactive")
        mode.addItem("활성 · 창을 앞으로 가져온 뒤 실행", "active")
        mode.setCurrentIndex(max(0, mode.findData(str(self.action.get("input_mode") or "inactive"))))
        target = QtWidgets.QLineEdit(str(self.action.get("target_title") or self.action.get("target_window") or ""))
        target.setPlaceholderText("오른쪽 버튼으로 화면에서 프로그램을 선택하세요")
        target_window = QtWidgets.QLineEdit(str(self.action.get("target_window") or "")); target_window.setVisible(False)
        target_exe = QtWidgets.QLineEdit(str(self.action.get("target_exe") or "")); target_exe.setReadOnly(True)
        pick = QtWidgets.QPushButton("⌖ 화면에서 선택")
        pick.clicked.connect(lambda: self._pick_target(include_point))
        row = QtWidgets.QHBoxLayout(); row.addWidget(target, 1); row.addWidget(pick)
        self.widgets.update(input_mode=mode, target_title=target, target_window=target_window, target_exe=target_exe)
        form.addRow("실행 방식", mode); form.addRow("대상 창", row); form.addRow("대상 프로그램", target_exe)
        if include_point:
            x = QtWidgets.QSpinBox(); x.setRange(-100000, 100000); x.setValue(int(self.action.get("x") or 0))
            y = QtWidgets.QSpinBox(); y.setRange(-100000, 100000); y.setValue(int(self.action.get("y") or 0))
            coords = QtWidgets.QHBoxLayout(); coords.addWidget(QtWidgets.QLabel("X")); coords.addWidget(x); coords.addWidget(QtWidgets.QLabel("Y")); coords.addWidget(y)
            self.widgets.update(x=x, y=y)
            form.addRow("클라이언트 좌표", coords)

    def _pick_target(self, include_point: bool) -> None:
        from macro_studio.action_editor import WindowPickerDialog

        ignored = {int(self.winId())}
        for widget in (self.parentWidget(), self.main_window):
            if widget is not None:
                try:
                    ignored.add(int(widget.winId()))
                except Exception:
                    pass
        # 현재 설정창이 exec() 모달이므로 선택기도 자식으로 연결해야 Windows가 클릭을 차단하지 않습니다.
        picker = WindowPickerDialog(self, ignored_hwnds=ignored, hint_text="대상 프로그램을 클릭하세요 · 클릭 기능은 위치까지 함께 저장됩니다 · Esc 취소")
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        self.raise_(); self.activateWindow()
        if not accepted:
            return
        title = ""
        try:
            length = int(__import__("ctypes").windll.user32.GetWindowTextLengthW(picker.window_hwnd))
            buffer = __import__("ctypes").create_unicode_buffer(length + 1)
            __import__("ctypes").windll.user32.GetWindowTextW(picker.window_hwnd, buffer, length + 1)
            title = buffer.value
        except Exception:
            pass
        self.widgets["target_title"].setText(title or picker.exe_name or picker.window_token)
        self.widgets["target_window"].setText(picker.window_token)
        self.widgets["target_exe"].setText(picker.exe_name)
        if include_point:
            point = picker.selected_client_point()
            if point is not None:
                self.widgets["x"].setValue(point.x()); self.widgets["y"].setValue(point.y())

    def _edit_icon(self) -> None:
        from macro_studio.quickslot_deck import SlotIconEditDialog

        label = self.widgets["label"].text().strip() or ACTION_TITLES.get(self.kind, "Deck 액션")
        dialog = SlotIconEditDialog(max(0, self.slot_index), label, self.icon_config, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.icon_config = dialog.get_config()
            self.icon_config["icon_source"] = "manual"
            self._icon_manually_edited = True
            self.btn_edit_icon.setText("✓ 아이콘 편집 완료 · 다시 편집")

    def _test_action(self) -> None:
        self.main_window._execute_deck_action(self.result_action())

    def result_icon_config(self) -> Dict[str, Any]:
        if self.kind == "open_target" and not self._icon_config_has_visual(self.icon_config):
            target = self.widgets.get("target")
            if isinstance(target, QtWidgets.QLineEdit):
                self._set_icon_from_program(target.text())
        return copy.deepcopy(self.icon_config)

    @staticmethod
    def _icon_config_has_visual(config: Dict[str, Any]) -> bool:
        return bool(
            str(config.get("image_data") or "").strip()
            or str(config.get("image_path") or "").strip()
            or str(config.get("emoji") or "").strip()
        )

    def _browse_target(self, edit: QtWidgets.QLineEdit) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "실행할 파일 선택")
        if path:
            edit.setText(path)
            self._set_icon_from_program(path)

    def _set_icon_from_program(self, path: str) -> None:
        target = str(path or "").strip()
        if self._icon_manually_edited or not target:
            return
        from macro_studio.quickslot_deck import extract_program_icon_config

        config = extract_program_icon_config(target)
        if not config:
            return
        self.icon_config = config
        self.btn_edit_icon.setText("✓ 프로그램 아이콘 자동 적용 · 편집")

    def result_action(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"kind": self.kind, "label": self.widgets["label"].text().strip()}
        for key, widget in self.widgets.items():
            if key == "label":
                continue
            if isinstance(widget, QtWidgets.QLineEdit): result[key] = widget.text().strip()
            elif isinstance(widget, QtWidgets.QPlainTextEdit): result[key] = widget.toPlainText()
            elif isinstance(widget, QtWidgets.QKeySequenceEdit): result[key] = widget.keySequence().toString(QtGui.QKeySequence.PortableText)
            elif isinstance(widget, QtWidgets.QSpinBox): result[key] = widget.value()
            elif isinstance(widget, QtWidgets.QComboBox): result[key] = widget.currentData() if widget.currentData() is not None else widget.currentText()
            elif isinstance(widget, QtWidgets.QCheckBox): result[key] = widget.isChecked()
            elif isinstance(widget, QtWidgets.QListWidget):
                result[key] = [widget.item(i).text() for i in range(widget.count()) if widget.item(i).checkState() == QtCore.Qt.Checked]
        return result


class DeckBulkEditDialog(QtWidgets.QDialog):
    """Edits only explicitly enabled fields across actions of one kind."""

    FIELD_SPECS = {
        "text": [("text", "입력할 텍스트", "text"), ("text_method", "입력 엔진", "text_method"), ("key_interval_ms", "글자 입력 간격", "ms"), ("press_enter", "입력 후 Enter", "bool"), ("input_mode", "실행 방식", "input_mode")],
        "wait": [("ms", "대기 시간", "ms")],
        "multi_macros": [("delay_ms", "작업 간격", "ms"), ("continue_on_error", "실패해도 계속", "bool")],
        "mouse_click": [("button", "마우스 버튼", "button"), ("clicks", "클릭 횟수", "clicks"), ("input_mode", "실행 방식", "input_mode")],
        "hotkey": [("keys", "키 조합", "line"), ("input_mode", "실행 방식", "input_mode")],
        "open_target": [("target", "실행 대상", "line")],
        "terminate_program": [("process", "프로세스 이름", "line")],
        "page_goto": [("page", "이동할 페이지", "page")],
    }

    def __init__(self, kind: str, sample: Dict[str, Any], count: int, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.kind = kind
        self.controls: Dict[str, tuple[QtWidgets.QCheckBox, QtWidgets.QWidget]] = {}
        self.setWindowTitle(f"{count}개 슬롯 일괄 편집")
        self.setMinimumWidth(520)
        self.setStyleSheet(deck_dock_stylesheet())
        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("체크한 항목만 선택한 모든 슬롯에 적용됩니다.")
        hint.setObjectName("Hint"); root.addWidget(hint)
        form = QtWidgets.QFormLayout(); form.setSpacing(10)
        specs = [("label", "슬롯 이름", "line")] + self.FIELD_SPECS.get(kind, [])
        for key, title, widget_kind in specs:
            enabled = QtWidgets.QCheckBox(title)
            widget = self._make_widget(widget_kind, sample.get(key))
            widget.setEnabled(False); enabled.toggled.connect(widget.setEnabled)
            form.addRow(enabled, widget); self.controls[key] = (enabled, widget)
        root.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _make_widget(self, kind: str, value: Any) -> QtWidgets.QWidget:
        if kind == "text":
            widget = QtWidgets.QPlainTextEdit(str(value or "")); widget.setMaximumHeight(110); return widget
        if kind in {"ms", "clicks", "page"}:
            widget = QtWidgets.QSpinBox(); widget.setRange(0 if kind == "ms" else 1, 600000 if kind == "ms" else 99); widget.setValue(int(value or (1 if kind != "ms" else 0))); return widget
        if kind == "bool":
            widget = QtWidgets.QCheckBox("사용"); widget.setChecked(bool(value)); return widget
        if kind in {"text_method", "input_mode", "button"}:
            widget = QtWidgets.QComboBox()
            choices = {
                "text_method": [("자동", "auto"), ("클립보드", "clipboard"), ("글자별 입력", "type"), ("WM_CHAR", "wm_char"), ("WM_SETTEXT", "set_text")],
                "input_mode": [("비활성", "inactive"), ("활성", "active")],
                "button": [("좌클릭", "left"), ("우클릭", "right"), ("가운데 클릭", "middle")],
            }[kind]
            for label, data in choices: widget.addItem(label, data)
            widget.setCurrentIndex(max(0, widget.findData(str(value or choices[0][1])))); return widget
        return QtWidgets.QLineEdit(str(value or ""))

    def patch(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, (enabled, widget) in self.controls.items():
            if not enabled.isChecked(): continue
            if isinstance(widget, QtWidgets.QPlainTextEdit): result[key] = widget.toPlainText()
            elif isinstance(widget, QtWidgets.QLineEdit): result[key] = widget.text()
            elif isinstance(widget, QtWidgets.QSpinBox): result[key] = widget.value()
            elif isinstance(widget, QtWidgets.QCheckBox): result[key] = widget.isChecked()
            elif isinstance(widget, QtWidgets.QComboBox): result[key] = widget.currentData()
        return result


class DeckDockWindow(QtWidgets.QMainWindow):
    """Visual standalone editor kept in sync with a QuickSlotDeckWindow."""

    def __init__(self, main_window: Any):
        super().__init__(main_window)
        self.main_window = main_window
        self.repository = main_window.repository
        self.current_page = int(main_window.current_page)
        self._saving = False
        self.selected_slots: set[int] = set()
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("MacroRelay · Deck Dock")
        self.setWindowIcon(main_window.windowIcon())
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(deck_dock_stylesheet())
        self._init_ui()
        self.reload()

    def _init_ui(self) -> None:
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root); outer.setContentsMargins(18, 16, 18, 16); outer.setSpacing(12)
        toolbar = QtWidgets.QFrame(); toolbar.setObjectName("Toolbar")
        top = QtWidgets.QHBoxLayout(toolbar); top.setContentsMargins(16, 10, 16, 10)
        title = QtWidgets.QLabel("Deck Dock"); title.setObjectName("Title")
        self.preset_combo = QtWidgets.QComboBox(); self.preset_combo.setMinimumWidth(220); self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        save = QtWidgets.QPushButton("저장 및 닫기"); save.clicked.connect(self._save_and_close)
        self.grid_rows_spin = QtWidgets.QSpinBox(); self.grid_rows_spin.setRange(1, 10); self.grid_rows_spin.setSuffix(" 행")
        self.grid_cols_spin = QtWidgets.QSpinBox(); self.grid_cols_spin.setRange(1, 10); self.grid_cols_spin.setSuffix(" 열")
        self.grid_apply_btn = QtWidgets.QPushButton("그리드 적용"); self.grid_apply_btn.clicked.connect(self._apply_grid_size)
        top.addWidget(title); top.addSpacing(16); top.addWidget(QtWidgets.QLabel("프리셋")); top.addWidget(self.preset_combo)
        top.addSpacing(14); top.addWidget(QtWidgets.QLabel("그리드")); top.addWidget(self.grid_rows_spin); top.addWidget(QtWidgets.QLabel("×")); top.addWidget(self.grid_cols_spin); top.addWidget(self.grid_apply_btn)
        top.addStretch(1); top.addWidget(save)
        outer.addWidget(toolbar)
        body = QtWidgets.QHBoxLayout(); body.setSpacing(14); outer.addLayout(body, 1)
        center = QtWidgets.QFrame(); center.setObjectName("DeckCard")
        center_layout = QtWidgets.QVBoxLayout(center); center_layout.setContentsMargins(20, 18, 20, 14)
        page_header = QtWidgets.QHBoxLayout(); page_header.addStretch(1)
        self.deck_title = QtWidgets.QLabel("QuickSlot Deck"); self.deck_title.setAlignment(QtCore.Qt.AlignCenter); self.deck_title.setStyleSheet("font-size:14pt;font-weight:800;color:#FFFFFF;")
        self.rename_page_btn = QtWidgets.QPushButton("이름 변경"); self.rename_page_btn.clicked.connect(self.rename_page)
        page_header.addWidget(self.deck_title); page_header.addWidget(self.rename_page_btn); page_header.addStretch(1)
        center_layout.addLayout(page_header)
        selection_bar = QtWidgets.QHBoxLayout()
        self.selection_toggle = QtWidgets.QPushButton("다중 선택")
        self.selection_toggle.setCheckable(True); self.selection_toggle.toggled.connect(self._selection_mode_changed)
        self.selection_label = QtWidgets.QLabel("선택 0개")
        self.select_page_btn = QtWidgets.QPushButton("현재 페이지 전체 선택"); self.select_page_btn.clicked.connect(self._select_current_page)
        self.target_page_combo = QtWidgets.QComboBox(); self.target_page_combo.setMinimumWidth(120)
        self.move_selected_btn = QtWidgets.QPushButton("선택 이동"); self.move_selected_btn.clicked.connect(self._move_selected_to_target_page)
        self.bulk_edit_btn = QtWidgets.QPushButton("일괄 편집"); self.bulk_edit_btn.clicked.connect(self._bulk_edit_selected)
        self.clear_selection_btn = QtWidgets.QPushButton("선택 해제"); self.clear_selection_btn.clicked.connect(self._clear_selection)
        selection_bar.addWidget(self.selection_toggle); selection_bar.addWidget(self.selection_label); selection_bar.addWidget(self.select_page_btn)
        selection_bar.addStretch(1); selection_bar.addWidget(QtWidgets.QLabel("이동할 페이지")); selection_bar.addWidget(self.target_page_combo)
        selection_bar.addWidget(self.move_selected_btn); selection_bar.addWidget(self.bulk_edit_btn); selection_bar.addWidget(self.clear_selection_btn)
        center_layout.addLayout(selection_bar)
        self.grid_host = QtWidgets.QWidget(); self.grid = QtWidgets.QGridLayout(self.grid_host); self.grid.setSpacing(9)
        center_layout.addWidget(self.grid_host, 1)
        pager = QtWidgets.QGridLayout(); self.prev_btn = QtWidgets.QPushButton("◀"); self.next_btn = QtWidgets.QPushButton("▶"); self.add_page_btn = QtWidgets.QPushButton("＋ 페이지"); self.delete_page_btn = QtWidgets.QPushButton("－ 페이지")
        self.page_label = QtWidgets.QLabel(); self.page_label.setAlignment(QtCore.Qt.AlignCenter); self.page_label.setMinimumWidth(120)
        self.prev_btn.clicked.connect(self.prev_page); self.next_btn.clicked.connect(self.next_page); self.add_page_btn.clicked.connect(self.add_page); self.delete_page_btn.clicked.connect(self.delete_page)
        manage = QtWidgets.QHBoxLayout(); manage.addWidget(self.add_page_btn); manage.addWidget(self.delete_page_btn); manage.addStretch(1)
        navigation = QtWidgets.QHBoxLayout(); navigation.addWidget(self.prev_btn); navigation.addWidget(self.page_label); navigation.addWidget(self.next_btn)
        pager.addLayout(manage, 0, 0); pager.addLayout(navigation, 0, 1, QtCore.Qt.AlignCenter); pager.addWidget(QtWidgets.QWidget(), 0, 2)
        pager.setColumnStretch(0, 1); pager.setColumnStretch(1, 1); pager.setColumnStretch(2, 1)
        center_layout.addLayout(pager); body.addWidget(center, 1)
        panel = QtWidgets.QFrame(); panel.setObjectName("PanelCard"); panel.setFixedWidth(330)
        panel_layout = QtWidgets.QVBoxLayout(panel); panel_layout.setContentsMargins(12, 12, 12, 12)
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText("기능 검색"); self.search.textChanged.connect(self._filter_actions); panel_layout.addWidget(self.search)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); self.palette_host = QtWidgets.QWidget(); self.palette_layout = QtWidgets.QVBoxLayout(self.palette_host); self.palette_layout.setContentsMargins(2, 4, 2, 4); self.palette_layout.setSpacing(8); scroll.setWidget(self.palette_host); panel_layout.addWidget(scroll, 1)
        self.action_buttons: list[ActionPaletteButton] = []
        for category, entries in ACTION_LIBRARY.items():
            group = QtWidgets.QGroupBox(category); group_layout = QtWidgets.QVBoxLayout(group); group_layout.setSpacing(6)
            for kind, action_name, description in entries:
                button = ActionPaletteButton(kind, action_name, description, group); group_layout.addWidget(button); self.action_buttons.append(button)
            self.palette_layout.addWidget(group)
        self.palette_layout.addStretch(1); body.addWidget(panel)

    def _filter_actions(self, text: str) -> None:
        needle = text.strip().casefold()
        for button in self.action_buttons:
            button.setVisible(not needle or needle in button.kind.casefold() or needle in button.findChildren(QtWidgets.QLabel)[1].text().casefold())

    def _page_size(self) -> int:
        return max(1, int(self.main_window.rows) * int(self.main_window.cols))

    def _ensure_payload(self) -> None:
        self.payload = self.repository.load_hotkeys()
        slots = list(self.payload.get("slots") or [])
        page_size = self._page_size()
        inferred = max(1, math.ceil(len(slots) / page_size))
        self.page_count = max(inferred, int(self.payload.get("deck_page_count") or 1))
        needed = self.page_count * page_size
        slots.extend({"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(max(0, needed - len(slots))))
        self.payload["slots"] = slots
        names = [str(value) for value in list(self.payload.get("deck_page_names") or [])]
        names.extend(f"페이지 {i + 1}" for i in range(len(names), self.page_count))
        self.payload["deck_page_names"] = names[:self.page_count]
        self.payload["deck_page_count"] = self.page_count
        self.current_page = max(0, min(self.current_page, self.page_count - 1))

    def reload(self) -> None:
        self._ensure_payload()
        self.selected_slots.intersection_update(range(len(self.payload["slots"])))
        with QtCore.QSignalBlocker(self.grid_rows_spin), QtCore.QSignalBlocker(self.grid_cols_spin):
            self.grid_rows_spin.setValue(max(1, min(10, int(self.main_window.rows))))
            self.grid_cols_spin.setValue(max(1, min(10, int(self.main_window.cols))))
        active = str(self.main_window.config.get("active_slot_preset") or "")
        with QtCore.QSignalBlocker(self.preset_combo):
            self.preset_combo.clear()
            for preset_id, preset in self.main_window.config.get("slot_presets", {}).items():
                self.preset_combo.addItem(str(preset.get("name") or preset_id), preset_id)
            self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(active)))
        self._render_page()

    def _render_page(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        page_size = self._page_size(); start = self.current_page * page_size
        rows, cols = int(self.main_window.rows), int(self.main_window.cols)
        for local in range(page_size):
            index = start + local; slot = self.payload["slots"][index]
            button = DeckSlotButton(index, slot, self.main_window.custom_icons.get(str(index), {}), self.grid_host, selection_mode=self.selection_toggle.isChecked(), selected=index in self.selected_slots)
            button.setMinimumSize(max(48, min(92, 760 // max(1, cols))), max(44, min(82, 500 // max(1, rows))))
            button.action_dropped.connect(self._configure_new_action); button.slot_dropped.connect(self._move_slot); button.image_dropped.connect(self._apply_dropped_icon); button.edit_requested.connect(self._edit_slot); button.clear_requested.connect(self._clear_slot); button.duplicate_requested.connect(self._duplicate_slot); button.selection_requested.connect(self._toggle_slot_selection)
            self.grid.addWidget(button, local // cols, local % cols)
        page_name = self.payload["deck_page_names"][self.current_page]
        self.deck_title.setText(page_name)
        self.page_label.setText(f"{self.current_page + 1} / {self.page_count}")
        self.prev_btn.setEnabled(self.current_page > 0); self.next_btn.setEnabled(self.current_page + 1 < self.page_count); self.delete_page_btn.setEnabled(self.page_count > 1)
        with QtCore.QSignalBlocker(self.target_page_combo):
            current_target = self.target_page_combo.currentData()
            self.target_page_combo.clear()
            for page, name in enumerate(self.payload["deck_page_names"]): self.target_page_combo.addItem(f"{page + 1}. {name}", page)
            target_index = self.target_page_combo.findData(current_target)
            self.target_page_combo.setCurrentIndex(target_index if target_index >= 0 else min(self.current_page, self.page_count - 1))
        self._update_selection_controls()

    def _selection_mode_changed(self, enabled: bool) -> None:
        if not enabled: self.selected_slots.clear()
        self._render_page()

    def _toggle_slot_selection(self, index: int) -> None:
        if index in self.selected_slots: self.selected_slots.remove(index)
        else: self.selected_slots.add(index)
        self._render_page()

    def _select_current_page(self) -> None:
        self.selection_toggle.setChecked(True)
        start = self.current_page * self._page_size()
        self.selected_slots.update(
            index for index in range(start, start + self._page_size())
            if self._is_filled_slot(self.payload["slots"][index])
        )
        self._render_page()

    def _clear_selection(self) -> None:
        self.selected_slots.clear(); self._render_page()

    def _update_selection_controls(self) -> None:
        count = len(self.selected_slots); self.selection_label.setText(f"선택 {count}개")
        self.move_selected_btn.setEnabled(count > 0); self.bulk_edit_btn.setEnabled(count > 0); self.clear_selection_btn.setEnabled(count > 0)

    @staticmethod
    def _is_filled_slot(slot: Dict[str, Any]) -> bool:
        return bool(str(slot.get("macro") or "").strip() or slot.get("action"))

    def _move_selected_slots(self, target_page: int) -> tuple[bool, str]:
        sources = [index for index in sorted(self.selected_slots) if self._is_filled_slot(self.payload["slots"][index])]
        if not sources: return False, "선택한 슬롯 중 이동할 항목이 없습니다."
        size = self._page_size(); start = target_page * size; end = start + size
        source_set = set(sources)
        targets = [index for index in range(start, end) if index in source_set or not self._is_filled_slot(self.payload["slots"][index])]
        if len(targets) < len(sources): return False, f"대상 페이지에 빈 슬롯이 {len(sources)}개 필요합니다."
        snapshots = [(copy.deepcopy(self.payload["slots"][index]), copy.deepcopy(self.main_window.custom_icons.get(str(index)))) for index in sources]
        for index in sources:
            self.payload["slots"][index] = {"macro": "", "hotkey": "", "mode": "hybrid"}; self.main_window.custom_icons.pop(str(index), None)
        for target, (slot, icon) in zip(targets, snapshots):
            self.payload["slots"][target] = slot
            if icon is not None: self.main_window.custom_icons[str(target)] = icon
        self.selected_slots = set(targets[:len(sources)]); self.current_page = target_page
        return True, ""

    def _move_selected_to_target_page(self) -> None:
        target = int(self.target_page_combo.currentData())
        moved, message = self._move_selected_slots(target)
        if not moved: QtWidgets.QMessageBox.information(self, "선택 이동", message); return
        self.save()

    def _apply_bulk_patch(self, indexes: list[int], patch: Dict[str, Any]) -> int:
        changed = 0
        for index in indexes:
            slot = self.payload["slots"][index]; action = copy.deepcopy(slot.get("action") or {})
            if not action: continue
            action.update(copy.deepcopy(patch)); self.payload["slots"][index] = self._slot_from_action(action); changed += 1
        return changed

    def _bulk_edit_selected(self) -> None:
        indexes = [index for index in sorted(self.selected_slots) if self.payload["slots"][index].get("action")]
        if not indexes:
            QtWidgets.QMessageBox.information(self, "일괄 편집", "Deck 액션이 설정된 슬롯을 선택해 주세요."); return
        kinds = {str(self.payload["slots"][index]["action"].get("kind") or "") for index in indexes}
        if len(kinds) != 1:
            QtWidgets.QMessageBox.information(self, "일괄 편집", "같은 종류의 액션 슬롯만 함께 편집할 수 있습니다."); return
        kind = next(iter(kinds)); sample = dict(self.payload["slots"][indexes[0]]["action"])
        dialog = DeckBulkEditDialog(kind, sample, len(indexes), self)
        if dialog.exec() != QtWidgets.QDialog.Accepted: return
        patch = dialog.patch()
        if not patch: return
        self._apply_bulk_patch(indexes, patch); self.save()

    def _apply_grid_size(self) -> None:
        new_rows, new_cols = self.grid_rows_spin.value(), self.grid_cols_spin.value()
        old_rows, old_cols = int(self.main_window.rows), int(self.main_window.cols)
        if (new_rows, new_cols) == (old_rows, old_cols):
            return
        old_size, new_size = max(1, old_rows * old_cols), max(1, new_rows * new_cols)
        old_slots = list(self.payload.get("slots") or [])
        old_names = list(self.payload.get("deck_page_names") or [])
        old_icons = copy.deepcopy(self.main_window.custom_icons)
        new_slots: list[Dict[str, Any]] = []
        new_names: list[str] = []
        new_icons: Dict[str, Any] = {}
        for page in range(self.page_count):
            chunk = old_slots[page * old_size:(page + 1) * old_size]
            chunk.extend({"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(max(0, old_size - len(chunk))))
            last_used = max((i for i, slot in enumerate(chunk) if str(slot.get("macro") or "").strip()), default=-1)
            parts = max(1, math.ceil((last_used + 1) / new_size))
            base_name = str(old_names[page] if page < len(old_names) else f"페이지 {page + 1}")
            for part in range(parts):
                segment = chunk[part * new_size:(part + 1) * new_size]
                segment.extend({"macro": "", "hotkey": "", "mode": "hybrid"} for _ in range(max(0, new_size - len(segment))))
                new_base = len(new_slots); new_slots.extend(segment)
                new_names.append(base_name if parts == 1 else f"{base_name} ({part + 1})")
                for local in range(part * new_size, min((part + 1) * new_size, old_size)):
                    source_key = str(page * old_size + local)
                    if source_key in old_icons:
                        new_icons[str(new_base + local - part * new_size)] = old_icons[source_key]
        self.main_window.rows, self.main_window.cols = new_rows, new_cols
        self.main_window.custom_icons = new_icons
        self.payload["slots"] = new_slots
        self.payload["deck_page_names"] = new_names
        self.page_count = len(new_names)
        self.current_page = min(self.current_page, self.page_count - 1)
        self.save()

    def _slot_from_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return {"macro": action_title(action), "hotkey": "", "mode": "deck_action", "action": action}

    def _configure_new_action(self, index: int, kind: str) -> None:
        dialog = DeckActionConfigDialog(
            kind, None, self.main_window, self, slot_index=index,
            icon_config=self.main_window.custom_icons.get(str(index), {}),
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            action = dialog.result_action()
            self.payload["slots"][index] = self._slot_from_action(action)
            icon_config = dialog.result_icon_config()
            if not icon_config:
                icon_config = {
                    "emoji": ACTION_ICONS.get(kind, "•"), "icon_size": 58,
                    "text_show": bool(action.get("label")), "text_position": "bottom",
                    "font_size": 11, "spacing": 3,
                }
            if icon_config: self.main_window.custom_icons[str(index)] = icon_config
            else: self.main_window.custom_icons.pop(str(index), None)
            self.save()

    def _apply_dropped_icon(self, index: int, image_path: str) -> None:
        from macro_studio.quickslot_deck import encode_image_file_to_base64

        path = Path(str(image_path or ""))
        pixmap = QtGui.QPixmap(str(path))
        image_data = encode_image_file_to_base64(str(path))
        if not path.is_file() or pixmap.isNull() or not image_data:
            QtWidgets.QMessageBox.warning(
                self,
                "아이콘 이미지 오류",
                "지원되는 10MB 이하 이미지 파일을 슬롯에 놓아 주세요.",
            )
            return

        previous = dict(self.main_window.custom_icons.get(str(index), {}) or {})
        had_visual_layout = bool(
            previous.get("image_data") or previous.get("image_path") or previous.get("emoji")
        )
        previous.update({
            "image_path": "",
            "image_data": image_data,
            "emoji": "",
            "full_stretch": bool(previous.get("full_stretch", True)),
            "text_show": bool(previous.get("text_show", False)) if had_visual_layout else False,
            "text_position": str(previous.get("text_position") or "hidden") if had_visual_layout else "hidden",
            "icon_source": "manual",
        })
        self.main_window.custom_icons[str(index)] = previous
        self.main_window._capture_active_slot_preset()
        self.main_window._save_config()
        self.main_window._preview_slot_icon(index, previous)
        self._render_page()

    def _edit_slot(self, index: int) -> None:
        slot = self.payload["slots"][index]; action = dict(slot.get("action") or {})
        if not action:
            return
        dialog = DeckActionConfigDialog(
            str(action.get("kind") or ""), action, self.main_window, self,
            slot_index=index, icon_config=self.main_window.custom_icons.get(str(index), {}),
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.payload["slots"][index] = self._slot_from_action(dialog.result_action())
            icon_config = dialog.result_icon_config()
            if icon_config: self.main_window.custom_icons[str(index)] = icon_config
            else: self.main_window.custom_icons.pop(str(index), None)
            self.save()

    def _move_slot(self, source: int, target: int) -> None:
        if source == target: return
        self.payload["slots"][source], self.payload["slots"][target] = self.payload["slots"][target], self.payload["slots"][source]
        source_icon = self.main_window.custom_icons.pop(str(source), None)
        target_icon = self.main_window.custom_icons.pop(str(target), None)
        if source_icon is not None: self.main_window.custom_icons[str(target)] = source_icon
        if target_icon is not None: self.main_window.custom_icons[str(source)] = target_icon
        self.save()

    def _clear_slot(self, index: int) -> None:
        self.payload["slots"][index] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        self.main_window.custom_icons.pop(str(index), None)
        self.save()

    def _duplicate_slot(self, index: int) -> None:
        start = self.current_page * self._page_size(); end = start + self._page_size()
        empty = next((i for i in range(start, end) if not str(self.payload["slots"][i].get("macro") or "").strip()), None)
        if empty is None:
            QtWidgets.QMessageBox.information(self, "슬롯 복제", "현재 페이지에 빈 슬롯이 없습니다."); return
        self.payload["slots"][empty] = copy.deepcopy(self.payload["slots"][index])
        if str(index) in self.main_window.custom_icons:
            self.main_window.custom_icons[str(empty)] = copy.deepcopy(self.main_window.custom_icons[str(index)])
        self.save()

    def save(self) -> bool:
        if self._saving:
            return False
        self._saving = True
        try:
            self.payload["deck_page_count"] = self.page_count
            self.main_window._save_preset_hotkeys(self.payload)
            self.main_window.current_page = min(self.main_window.current_page, self.page_count - 1)
            self.main_window.refresh_slots()
            self.main_window._capture_active_slot_preset()
            self.main_window._save_config()
            self._render_page()
            return True
        finally:
            self._saving = False

    def _save_and_close(self) -> None:
        if self.save():
            self.close()

    def _preset_changed(self, index: int) -> None:
        if index < 0: return
        preset_id = str(self.preset_combo.itemData(index) or "")
        if preset_id and preset_id != str(self.main_window.config.get("active_slot_preset") or ""):
            self.main_window._switch_slot_preset(preset_id); self.current_page = 0; self.reload()

    def prev_page(self) -> None:
        if self.current_page > 0: self.current_page -= 1; self._render_page()

    def next_page(self) -> None:
        if self.current_page + 1 < self.page_count: self.current_page += 1; self._render_page()

    def add_page(self) -> None:
        self.page_count += 1; self.payload["deck_page_names"].append(f"페이지 {self.page_count}")
        self.payload["slots"].extend({"macro":"", "hotkey":"", "mode":"hybrid"} for _ in range(self._page_size()))
        self.current_page = self.page_count - 1; self.save()

    def rename_page(self) -> None:
        current = self.payload["deck_page_names"][self.current_page]
        name, ok = QtWidgets.QInputDialog.getText(self, "페이지 이름", "새 페이지 이름", text=current)
        if ok and name.strip(): self.payload["deck_page_names"][self.current_page] = name.strip(); self.save()

    def delete_page(self) -> None:
        if self.page_count <= 1: return
        answer = QtWidgets.QMessageBox.question(self, "페이지 삭제", "현재 페이지와 포함된 슬롯을 삭제할까요?")
        if answer != QtWidgets.QMessageBox.Yes: return
        size = self._page_size(); start = self.current_page * size
        del self.payload["slots"][start:start + size]; del self.payload["deck_page_names"][self.current_page]
        shifted_icons: Dict[str, Any] = {}
        for key, value in self.main_window.custom_icons.items():
            index = int(key)
            if start <= index < start + size:
                continue
            shifted_icons[str(index - size if index >= start + size else index)] = value
        self.main_window.custom_icons = shifted_icons
        self.page_count -= 1; self.current_page = min(self.current_page, self.page_count - 1); self.save()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.main_window._deck_dock_window = None
        super().closeEvent(event)
