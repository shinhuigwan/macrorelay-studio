"""Standalone visual editor for QuickSlot deck pages and executable actions."""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets


ACTION_LIBRARY: Dict[str, list[tuple[str, str, str]]] = {
    "시스템": [
        ("open_target", "열기", "프로그램·파일·폴더·웹사이트"),
        ("terminate_program", "프로그램 종료", "지정 프로세스 종료"),
        ("hotkey", "단축키", "키 조합 보내기"),
        ("text", "텍스트 입력", "클립보드 기반 텍스트 입력"),
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
    "open_target": "↗", "terminate_program": "■", "hotkey": "⌨", "text": "T",
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
    edit_requested = QtCore.Signal(int)
    clear_requested = QtCore.Signal(int)
    duplicate_requested = QtCore.Signal(int)

    def __init__(self, slot_index: int, slot: Dict[str, Any], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.slot_index = slot_index
        self.slot = copy.deepcopy(slot)
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
        icon_text = ACTION_ICONS.get(str(action.get("kind") or ""), "＋" if not macro else "M")
        icon = QtWidgets.QLabel(icon_text)
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setStyleSheet("font-size:17pt; color:#23E0C2; font-weight:900; background:transparent; border:none;")
        title = QtWidgets.QLabel(action_title(action) if action else (macro or "빈 슬롯"))
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size:8pt; color:#F4F6FA; font-weight:700; background:transparent; border:none;")
        layout.addWidget(icon, 1)
        layout.addWidget(title)
        if not macro:
            self.setStyleSheet("QFrame { background:#202329; border:1px dashed #555B66; border-radius:9px; } QFrame:hover { border:2px solid #18DDC0; }")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
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
            self.edit_requested.emit(self.slot_index)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat("application/x-macrorelay-deck-action") or mime.hasFormat("application/x-macrorelay-deck-slot"):
            event.acceptProposedAction()

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
    def __init__(self, kind: str, action: Optional[Dict[str, Any]], main_window: Any, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.kind = kind
        self.action = copy.deepcopy(action or {})
        self.main_window = main_window
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
        label = QtWidgets.QLineEdit(str(self.action.get("label") or ACTION_TITLES.get(kind, "Deck 액션")))
        self.widgets["label"] = label
        form.addRow("슬롯 이름", label)
        self._build_fields(form)
        root.addLayout(form)
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
            row = QtWidgets.QHBoxLayout(); row.addWidget(edit, 1); row.addWidget(browse)
            self.widgets["target"] = edit; form.addRow("대상", row)
        elif kind == "terminate_program":
            edit = QtWidgets.QLineEdit(str(self.action.get("process") or ""))
            edit.setPlaceholderText("예: notepad.exe")
            self.widgets["process"] = edit; form.addRow("프로세스 이름", edit)
        elif kind == "hotkey":
            edit = QtWidgets.QKeySequenceEdit(QtGui.QKeySequence(str(self.action.get("keys") or "Ctrl+Shift+A")))
            target = QtWidgets.QLineEdit(str(self.action.get("target_title") or "")); target.setPlaceholderText("비우면 현재 활성 창")
            self.widgets["keys"] = edit; self.widgets["target_title"] = target
            form.addRow("키 조합", edit); form.addRow("대상 창 제목", target)
        elif kind == "text":
            edit = QtWidgets.QPlainTextEdit(str(self.action.get("text") or "")); edit.setMaximumHeight(120)
            target = QtWidgets.QLineEdit(str(self.action.get("target_title") or "")); target.setPlaceholderText("비우면 현재 활성 창")
            self.widgets["text"] = edit; self.widgets["target_title"] = target
            form.addRow("입력할 텍스트", edit); form.addRow("대상 창 제목", target)
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

    def _browse_target(self, edit: QtWidgets.QLineEdit) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "실행할 파일 선택")
        if path:
            edit.setText(path)

    def result_action(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"kind": self.kind, "label": self.widgets["label"].text().strip() or ACTION_TITLES.get(self.kind, "Deck 액션")}
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


class DeckDockWindow(QtWidgets.QMainWindow):
    """Visual standalone editor kept in sync with a QuickSlotDeckWindow."""

    def __init__(self, main_window: Any):
        super().__init__(main_window)
        self.main_window = main_window
        self.repository = main_window.repository
        self.current_page = int(main_window.current_page)
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
        save = QtWidgets.QPushButton("저장 및 덱 적용"); save.clicked.connect(self.save)
        top.addWidget(title); top.addSpacing(16); top.addWidget(QtWidgets.QLabel("프리셋")); top.addWidget(self.preset_combo); top.addStretch(1); top.addWidget(save)
        outer.addWidget(toolbar)
        body = QtWidgets.QHBoxLayout(); body.setSpacing(14); outer.addLayout(body, 1)
        center = QtWidgets.QFrame(); center.setObjectName("DeckCard")
        center_layout = QtWidgets.QVBoxLayout(center); center_layout.setContentsMargins(20, 18, 20, 14)
        self.deck_title = QtWidgets.QLabel("QuickSlot Deck"); self.deck_title.setAlignment(QtCore.Qt.AlignCenter); self.deck_title.setStyleSheet("font-size:14pt;font-weight:800;color:#FFFFFF;")
        center_layout.addWidget(self.deck_title)
        self.grid_host = QtWidgets.QWidget(); self.grid = QtWidgets.QGridLayout(self.grid_host); self.grid.setSpacing(9)
        center_layout.addWidget(self.grid_host, 1)
        pager = QtWidgets.QHBoxLayout(); self.prev_btn = QtWidgets.QPushButton("◀"); self.next_btn = QtWidgets.QPushButton("▶"); self.add_page_btn = QtWidgets.QPushButton("＋ 페이지"); self.rename_page_btn = QtWidgets.QPushButton("이름 변경"); self.delete_page_btn = QtWidgets.QPushButton("페이지 삭제")
        self.page_label = QtWidgets.QLabel(); self.page_label.setAlignment(QtCore.Qt.AlignCenter); self.page_label.setMinimumWidth(120)
        self.prev_btn.clicked.connect(self.prev_page); self.next_btn.clicked.connect(self.next_page); self.add_page_btn.clicked.connect(self.add_page); self.rename_page_btn.clicked.connect(self.rename_page); self.delete_page_btn.clicked.connect(self.delete_page)
        pager.addStretch(1); pager.addWidget(self.prev_btn); pager.addWidget(self.page_label); pager.addWidget(self.next_btn); pager.addWidget(self.add_page_btn); pager.addWidget(self.rename_page_btn); pager.addWidget(self.delete_page_btn); pager.addStretch(1)
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
            button = DeckSlotButton(index, slot, self.grid_host)
            button.action_dropped.connect(self._configure_new_action); button.slot_dropped.connect(self._move_slot); button.edit_requested.connect(self._edit_slot); button.clear_requested.connect(self._clear_slot); button.duplicate_requested.connect(self._duplicate_slot)
            self.grid.addWidget(button, local // cols, local % cols)
        page_name = self.payload["deck_page_names"][self.current_page]
        self.deck_title.setText(page_name)
        self.page_label.setText(f"{self.current_page + 1} / {self.page_count}")
        self.prev_btn.setEnabled(self.current_page > 0); self.next_btn.setEnabled(self.current_page + 1 < self.page_count); self.delete_page_btn.setEnabled(self.page_count > 1)

    def _slot_from_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return {"macro": action_title(action), "hotkey": "", "mode": "deck_action", "action": action}

    def _configure_new_action(self, index: int, kind: str) -> None:
        dialog = DeckActionConfigDialog(kind, None, self.main_window, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.payload["slots"][index] = self._slot_from_action(dialog.result_action())
            self.main_window.custom_icons.pop(str(index), None)
            self.save()

    def _edit_slot(self, index: int) -> None:
        slot = self.payload["slots"][index]; action = dict(slot.get("action") or {})
        if not action:
            return
        dialog = DeckActionConfigDialog(str(action.get("kind") or ""), action, self.main_window, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.payload["slots"][index] = self._slot_from_action(dialog.result_action()); self.save()

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

    def save(self) -> None:
        self.payload["deck_page_count"] = self.page_count
        self.main_window._save_preset_hotkeys(self.payload)
        self.main_window.current_page = min(self.main_window.current_page, self.page_count - 1)
        self.main_window.refresh_slots(); self.main_window._capture_active_slot_preset(); self.main_window._save_config(); self._render_page()

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
