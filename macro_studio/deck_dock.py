"""Standalone visual editor for QuickSlot deck pages and executable actions."""

from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import macro_entry_points


ACTION_LIBRARY: Dict[str, list[tuple[str, str, str]]] = {
    "시스템": [
        ("open_target", "실행", "프로그램·파일·폴더·웹사이트 실행"),
        ("activate_browser_tab", "브라우저 사이트 탭", "선택한 브라우저의 탭 선택·프리셋 전환"),
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
ACTION_TITLES["navigate_browser_tab"] = "브라우저 홈 탭"

ACTION_ICONS = {
    "open_target": "↗", "activate_browser_tab": "◉", "navigate_browser_tab": "⌂", "terminate_program": "■", "hotkey": "⌨", "text": "T", "mouse_click": "🖱",
    "wait": "◷", "studio": "M", "stop_all": "⬛", "run_macro": "▶",
    "multi_macros": "≡", "switch_preset": "★", "page_prev": "◀",
    "page_next": "▶", "page_goto": "▦", "page_first": "Ⅰ",
}

BROWSER_EXECUTABLES = (
    ("네이버 웨일", "whale.exe"),
    ("Microsoft Edge", "msedge.exe"),
    ("Google Chrome", "chrome.exe"),
)


def browser_choice_button(on_selected, parent: Optional[QtWidgets.QWidget] = None) -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton("브라우저 선택 ▾", parent)
    menu = QtWidgets.QMenu(button)
    for title, executable in BROWSER_EXECUTABLES:
        item = menu.addAction(title)
        item.triggered.connect(lambda _checked=False, name=executable: on_selected(name))
    button.setMenu(menu)
    return button


def browse_executable_name(parent: QtWidgets.QWidget) -> str:
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent, "실행 파일 찾기", "", "실행 파일 (*.exe);;모든 파일 (*)"
    )
    # Window matching compares the process name, not its installation path.
    return Path(path).name if path else ""


def action_title(action: Dict[str, Any]) -> str:
    fallback = str(action.get("entry_name") or "").strip() if action.get("kind") == "run_macro" else ""
    return str(action.get("label") or fallback or ACTION_TITLES.get(str(action.get("kind") or ""), "Deck 액션"))


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
    selection_drag_started = QtCore.Signal(QtCore.QPoint, QtCore.QPoint, bool)
    selection_drag_moved = QtCore.Signal(QtCore.QPoint)
    selection_drag_released = QtCore.Signal(QtCore.QPoint)
    pin_requested = QtCore.Signal(int)
    unpin_requested = QtCore.Signal(int)

    def __init__(self, slot_index: int, slot: Dict[str, Any], icon_config: Optional[Dict[str, Any]] = None, parent: Optional[QtWidgets.QWidget] = None, *, selection_mode: bool = False, selected: bool = False, pinned: bool = False):
        super().__init__(parent)
        self.slot_index = slot_index
        self.slot = copy.deepcopy(slot)
        self.selection_mode = selection_mode
        self.selected: Optional[bool] = None
        self.pinned = pinned
        self._press_pos: Optional[QtCore.QPoint] = None
        self._press_global: Optional[QtCore.QPoint] = None
        self._selection_dragging = False
        self._selection_additive = False
        self.setAcceptDrops(True)
        self.setMinimumSize(92, 82)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._base_style = "QFrame { background:#111318; border:1px solid #08090B; border-radius:9px; } QFrame:hover { border:2px solid #18DDC0; background:#171A20; }"
        self.setStyleSheet(self._base_style)
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
            self._base_style = "QFrame { background:#202329; border:1px dashed #555B66; border-radius:9px; } QFrame:hover { border:2px solid #18DDC0; }"
            self.setStyleSheet(self._base_style)
        self.selection_badge = QtWidgets.QLabel("✓", self)
        self.selection_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.selection_badge.setFixedSize(22, 22)
        self.selection_badge.move(5, 5)
        self.selection_badge.setStyleSheet("background:#18DDC0;color:#07110F;border:none;border-radius:11px;font-weight:900;")
        self.set_selected(selected)
        if pinned:
            pin_badge = QtWidgets.QLabel("📌", self)
            pin_badge.setToolTip("모든 페이지에 고정된 슬롯")
            pin_badge.move(6, 5)
            pin_badge.setStyleSheet("background:transparent;border:none;")

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if self.selected == selected:
            return
        self.selected = selected
        self.setStyleSheet(
            "QFrame { background:#173B38; border:3px solid #18DDC0; border-radius:9px; }"
            if selected else self._base_style
        )
        self.selection_badge.setVisible(selected)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
            self._press_global = event.globalPosition().toPoint()
            self._selection_dragging = False
            self._selection_additive = bool(event.modifiers() & QtCore.Qt.ControlModifier)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.selection_mode:
            if self._press_global is not None and event.buttons() & QtCore.Qt.LeftButton:
                current = event.globalPosition().toPoint()
                if not self._selection_dragging and (current - self._press_global).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                    self._selection_dragging = True
                    self.selection_drag_started.emit(self._press_global, current, self._selection_additive)
                elif self._selection_dragging:
                    self.selection_drag_moved.emit(current)
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
                if self._selection_dragging:
                    self.selection_drag_released.emit(event.globalPosition().toPoint())
                else:
                    self.selection_requested.emit(self.slot_index)
            else:
                self.edit_requested.emit(self.slot_index)
        self._press_pos = None
        self._press_global = None
        self._selection_dragging = False
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
        pin_action = menu.addAction("모든 페이지 고정 해제" if self.pinned else "모든 페이지에 고정")
        pin_action.setEnabled(bool(self.slot.get("macro") or self.slot.get("action")) or self.pinned)
        clear = menu.addAction("슬롯 비우기")
        selected = menu.exec(event.globalPos())
        if selected is edit:
            self.edit_requested.emit(self.slot_index)
        elif selected is duplicate:
            self.duplicate_requested.emit(self.slot_index)
        elif selected is pin_action:
            (self.unpin_requested if self.pinned else self.pin_requested).emit(self.slot_index)
        elif selected is clear:
            self.clear_requested.emit(self.slot_index)


class DeckSelectionGrid(QtWidgets.QWidget):
    """Allow a marquee to start in the space between slot cards."""

    selection_drag_started = QtCore.Signal(QtCore.QPoint, QtCore.QPoint, bool)
    selection_drag_moved = QtCore.Signal(QtCore.QPoint)
    selection_drag_released = QtCore.Signal(QtCore.QPoint)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.selection_mode = False
        self._press_global: Optional[QtCore.QPoint] = None
        self._dragging = False
        self._additive = False

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.selection_mode and event.button() == QtCore.Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._dragging = False
            self._additive = bool(event.modifiers() & QtCore.Qt.ControlModifier)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_global is not None and event.buttons() & QtCore.Qt.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragging and (current - self._press_global).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                self._dragging = True
                self.selection_drag_started.emit(self._press_global, current, self._additive)
            elif self._dragging:
                self.selection_drag_moved.emit(current)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_global is not None and event.button() == QtCore.Qt.LeftButton:
            if self._dragging:
                self.selection_drag_released.emit(event.globalPosition().toPoint())
            self._press_global = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DeckMacroMiniCanvas(QtWidgets.QGraphicsView):
    """Compact, read-only map of a macro's saved node layout and branches."""

    node_selected = QtCore.Signal(int)
    NODE_WIDTH = 138
    NODE_HEIGHT = 48

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.scene().setBackgroundBrush(QtGui.QColor("#171C25"))
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setStyleSheet("QGraphicsView { border:1px solid #4B5563; border-radius:8px; background:#171C25; }")
        self.setMinimumWidth(290)
        self.nodes: dict[int, QtWidgets.QGraphicsRectItem] = {}
        self.selected_index = 0
        self.last_click_shift = False

    @staticmethod
    def _targets(step: dict[str, Any], index: int, total: int) -> list[tuple[int, str]]:
        def valid(raw: Any) -> list[int]:
            values = raw if isinstance(raw, list) else []
            return [int(value) for value in values if str(value).isdigit() and 0 < int(value) <= total]

        edges: list[tuple[int, str]] = []
        success = valid(step.get("success_candidates"))
        if not success:
            value = step.get("on_success")
            if str(value).isdigit() and 0 < int(value) <= total:
                success = [int(value)]
            elif index < total and not step.get("stop_on_success"):
                success = [index + 1]
        edges.extend((target, "success") for target in success)
        failure = valid(step.get("fail_candidates"))
        if not failure:
            value = step.get("on_fail")
            if str(value).isdigit() and 0 < int(value) <= total:
                failure = [int(value)]
        edges.extend((target, "fail") for target in failure)
        for condition in step.get("edge_conditions") or []:
            if isinstance(condition, dict):
                value = condition.get("target")
                if str(value).isdigit() and 0 < int(value) <= total:
                    edges.append((int(value), "fail" if condition.get("kind") == "fail" else "success"))
        return list(dict.fromkeys(edges))

    def set_macro(self, macro: dict[str, Any]) -> None:
        from .action_editor import ACTION_LABELS

        scene = self.scene()
        scene.clear()
        self.nodes.clear()
        steps = macro.get("steps") or []
        saved = macro.get("graph_positions") or {}
        positions: dict[int, QtCore.QPointF] = {}
        for index in range(1, len(steps) + 1):
            raw = saved.get(str(index)) if isinstance(saved, dict) else None
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                try:
                    positions[index] = QtCore.QPointF(float(raw[0]) * 0.62, float(raw[1]) * 0.40)
                except (TypeError, ValueError):
                    pass
            if index not in positions:
                positions[index] = QtCore.QPointF(20, 18 + (index - 1) * 88)
        font = QtGui.QFont("Malgun Gothic", 9)
        metrics = QtGui.QFontMetrics(font)
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            position = positions[index]
            node = scene.addRect(0, 0, self.NODE_WIDTH, self.NODE_HEIGHT)
            node.setPos(position)
            node.setBrush(QtGui.QColor("#293445"))
            node.setPen(QtGui.QPen(QtGui.QColor("#708198"), 1.4))
            node.setData(0, index)
            node.setZValue(2)
            title = str(step.get("label") or step.get("entry_name") or ACTION_LABELS.get(str(step.get("action") or ""), "단계"))
            number = scene.addSimpleText(f"{index}. {metrics.elidedText(title, QtCore.Qt.ElideRight, self.NODE_WIDTH - 15)}", font)
            number.setBrush(QtGui.QColor("#F4F6FA"))
            number.setParentItem(node)
            number.setPos(7, 5)
            action_name = ACTION_LABELS.get(str(step.get("action") or ""), str(step.get("action") or ""))
            type_text = scene.addSimpleText(metrics.elidedText(action_name, QtCore.Qt.ElideRight, self.NODE_WIDTH - 15), font)
            type_text.setBrush(QtGui.QColor("#AFC1D8"))
            type_text.setParentItem(node)
            type_text.setPos(7, 25)
            node.setToolTip(f"{index}. {title}\n{action_name}")
            self.nodes[index] = node
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or index not in self.nodes:
                continue
            source = self.nodes[index].sceneBoundingRect().center()
            for target, kind in self._targets(step, index, len(steps)):
                if target not in self.nodes:
                    continue
                destination = self.nodes[target].sceneBoundingRect().center()
                path = QtGui.QPainterPath(source)
                distance = max(30.0, abs(destination.x() - source.x()) / 2)
                path.cubicTo(source.x() + distance, source.y(), destination.x() - distance, destination.y(), destination.x(), destination.y())
                edge = scene.addPath(path, QtGui.QPen(QtGui.QColor("#48CDA4" if kind == "success" else "#F28C8C"), 2))
                edge.setZValue(0)
                edge.setToolTip("성공 연결" if kind == "success" else "실패 연결")
        bounds = scene.itemsBoundingRect()
        scene.setSceneRect(bounds.adjusted(-30, -30, 30, 30))
        self.select_step(self.selected_index if self.selected_index in self.nodes else 1, center=False)
        self.fit_all()

    def select_step(self, index: int, *, center: bool = True) -> None:
        self.selected_index = index
        for number, node in self.nodes.items():
            selected = number == index
            node.setBrush(QtGui.QColor("#15566B" if selected else "#293445"))
            node.setPen(QtGui.QPen(QtGui.QColor("#26E0C3" if selected else "#708198"), 2.5 if selected else 1.4))
        if center and index in self.nodes:
            if self.transform().m11() < 0.75:
                self.resetTransform()
                self.scale(0.85, 0.85)
            self.centerOn(self.nodes[index])

    def fit_all(self) -> None:
        if self.nodes and self.viewport().width() > 0 and self.viewport().height() > 0:
            self.fitInView(self.scene().sceneRect(), QtCore.Qt.KeepAspectRatio)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        item = self.itemAt(event.position().toPoint())
        while item is not None:
            index = item.data(0)
            if isinstance(index, int) and index in self.nodes:
                self.last_click_shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                self.node_selected.emit(index)
                break
            item = item.parentItem()
        super().mousePressEvent(event)


class DeckMacroStepsTree(QtWidgets.QTreeWidget):
    """Single click replaces selection; only Shift or a drag can extend it."""

    def selectionCommand(self, index: QtCore.QModelIndex, event: Optional[QtCore.QEvent] = None):
        if (event is not None and event.type() in (
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonRelease,
            QtCore.QEvent.MouseButtonDblClick,
        ) and event.modifiers() & QtCore.Qt.ControlModifier
                and not event.modifiers() & QtCore.Qt.ShiftModifier):
            return QtCore.QItemSelectionModel.ClearAndSelect
        return super().selectionCommand(index, event)


MACRO_IMAGE_ACTIONS = {"image_search", "screen_condition", "multi_image_search", "animation_search"}
MACRO_REGION_ACTIONS = MACRO_IMAGE_ACTIONS | {"pixel_search", "ocr", "ocr_tracking", "wait_color", "color_ratio", "multi_pixel_check"}
MACRO_WINDOW_ACTIONS = {"mouse_click", "inactive_click", "type_text"}


def set_macro_step_target_program(step: dict[str, Any], executable: str) -> bool:
    """Change only process bindings, discarding stale window identities."""
    action = str(step.get("action") or "")
    changed = False
    if action in MACRO_REGION_ACTIONS or "region_window_exe" in step:
        if str(step.get("region_window_exe") or "").casefold() != executable.casefold():
            step["region_window_exe"] = executable
            step.pop("region_window", None)
            step.pop("region_window_hwnd", None)
            changed = True
    if action in MACRO_WINDOW_ACTIONS or "window_exe" in step:
        if str(step.get("window_exe") or "").casefold() != executable.casefold():
            step["window_exe"] = executable
            step.pop("window", None)
            step.pop("window_hwnd", None)
            changed = True
    if "target_exe" in step and str(step.get("target_exe") or "").casefold() != executable.casefold():
        step["target_exe"] = executable
        step.pop("target_window", None)
        changed = True
    click = step.get("click")
    if isinstance(click, dict) and ("window_exe" in click or action in MACRO_IMAGE_ACTIONS):
        if str(click.get("window_exe") or "").casefold() != executable.casefold():
            click["window_exe"] = executable
            click.pop("window", None)
            click.pop("window_hwnd", None)
            changed = True
    return changed


def merge_macro_visual_edit(step: dict[str, Any], visual: Any) -> dict[str, Any]:
    """Apply the Studio visual editor's image and region result to one step."""
    updated = copy.deepcopy(visual.step)
    aliases = visual.get_aliases()
    updated["assets"] = aliases
    if aliases:
        updated["asset"] = aliases[0]
    else:
        updated.pop("asset", None)
    for key, value in (("asset_regions", visual.get_asset_regions()),
                       ("asset_offsets", visual.get_asset_offsets())):
        if value:
            updated[key] = value
        else:
            updated.pop(key, None)
    click_target = visual.get_click_target()
    if click_target:
        updated["click_target"] = click_target
        updated["click_enabled"] = click_target != "none"
        if click_target == "custom_coord":
            updated["custom_click_x"], updated["custom_click_y"] = visual.get_custom_click_coords()
    condition = visual.get_match_condition()
    if condition:
        updated["match_condition"] = condition
    required = visual.get_required_count()
    if required >= 0:
        updated["required_count"] = required
    bounding = visual.get_bounding_region()
    if bounding:
        updated["search_region"] = bounding
        updated["region"] = bounding
        updated["regions"] = [bounding]
    elif not aliases:
        for key in ("search_region", "region", "regions"):
            updated.pop(key, None)
    for key in ("asset_confidences", "asset_routes"):
        mapping = updated.get(key)
        if isinstance(mapping, dict):
            remaining = {alias: value for alias, value in mapping.items() if alias in aliases}
            if remaining:
                updated[key] = remaining
            else:
                updated.pop(key, None)
    automation = updated.get("_automation")
    if isinstance(automation, dict):
        automation["image_count"] = len(aliases)
    return updated


class DeckTargetProgramDialog(QtWidgets.QDialog):
    def __init__(self, count: int, current: str = "", parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"{count}개 단계 · 대상 프로그램 변경")
        self.setMinimumWidth(510)
        self.setStyleSheet(deck_dock_stylesheet())
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("브라우저를 고르거나, 화면의 대상 창을 마우스로 클릭하세요. 선택한 단계에 한 번에 적용됩니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.executable = QtWidgets.QLineEdit(current)
        self.executable.setPlaceholderText("예: msedge.exe")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.executable, 1)
        row.addWidget(browser_choice_button(self.executable.setText, self))
        browse = QtWidgets.QPushButton("파일 찾기…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)
        pick = QtWidgets.QPushButton("⌖ 화면의 프로그램을 클릭해 선택")
        pick.clicked.connect(self._pick_on_screen)
        layout.addWidget(pick)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("선택 단계에 적용")
        buttons.accepted.connect(self._accept_executable)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        name = browse_executable_name(self)
        if name:
            self.executable.setText(name)

    def _pick_on_screen(self) -> None:
        from .action_editor import WindowPickerDialog

        ignored = {int(self.winId())}
        parent = self.parentWidget()
        if parent is not None:
            ignored.add(int(parent.winId()))
        picker = WindowPickerDialog(self, ignored_hwnds=ignored, hint_text="대상 프로그램 창을 클릭하세요 · Esc 취소")
        if picker.exec() == QtWidgets.QDialog.Accepted and picker.exe_name:
            self.executable.setText(picker.exe_name)
        self.raise_()
        self.activateWindow()

    def _accept_executable(self) -> None:
        if not self.executable.text().strip():
            QtWidgets.QMessageBox.information(self, "대상 프로그램", "대상 프로그램을 먼저 선택해 주세요.")
            return
        self.accept()

    def selected_executable(self) -> str:
        return Path(self.executable.text().strip()).name


class DeckMacroStepsDialog(QtWidgets.QDialog):
    """Edit a referenced macro's steps without leaving the Deck."""

    def __init__(self, repository: Any, macro_name: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.repository = repository
        self.macro_name = macro_name
        self.current_macro: Dict[str, Any] = {}
        self.setWindowTitle(f"{macro_name} · 매크로 상세 편집")
        self.setMinimumSize(850, 500)
        self.setStyleSheet(deck_dock_stylesheet())
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("클릭은 한 단계만 선택합니다. 여러 단계는 Shift+클릭 또는 드래그로 선택하세요. 저장한 내용은 이 매크로를 사용하는 모든 슬롯에 적용됩니다.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        layout.addWidget(hint)
        content = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        preview = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_title = QtWidgets.QLabel("매크로 흐름 미리보기")
        preview_title.setStyleSheet("font-weight:800; color:#F4F6FA;")
        preview_layout.addWidget(preview_title)
        self.mini_canvas = DeckMacroMiniCanvas(preview)
        self.mini_canvas.node_selected.connect(self._select_step_from_canvas)
        preview_layout.addWidget(self.mini_canvas, 1)
        preview_controls = QtWidgets.QHBoxLayout()
        legend = QtWidgets.QLabel("● 성공   ● 실패")
        legend.setStyleSheet("color:#AFC1D8;")
        preview_controls.addWidget(legend)
        preview_controls.addStretch(1)
        fit_button = QtWidgets.QPushButton("전체 보기")
        fit_button.clicked.connect(self.mini_canvas.fit_all)
        preview_controls.addWidget(fit_button)
        preview_layout.addLayout(preview_controls)
        content.addWidget(preview)
        self.steps_list = DeckMacroStepsTree()
        self.steps_list.setObjectName("MacroStepsList")
        self.steps_list.setStyleSheet("""
            QTreeWidget#MacroStepsList { background:#202329; color:#F4F6FA; border:1px solid #555B66;
                alternate-background-color:#202329; selection-background-color:#15566B; selection-color:#FFFFFF; }
            QTreeWidget#MacroStepsList::item { color:#F4F6FA; padding:4px 3px; }
            QTreeWidget#MacroStepsList::item:selected { background:#15566B; color:#FFFFFF; }
            QTreeWidget#MacroStepsList QHeaderView::section { background:#30343B; color:#F4F6FA;
                border:1px solid #555B66; padding:5px; }
        """)
        self.steps_list.setHeaderLabels(["번호", "단계", "대상 프로그램"])
        self.steps_list.setRootIsDecorated(False)
        self.steps_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.steps_list.setAlternatingRowColors(False)
        self.steps_list.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.steps_list.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.steps_list.header().setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        self.steps_list.header().resizeSection(2, 145)
        self.steps_list.itemDoubleClicked.connect(lambda item, _column: self._edit_double_clicked_step(item))
        self.steps_list.itemSelectionChanged.connect(self._sync_canvas_selection)
        content.addWidget(self.steps_list)
        content.setSizes([350, 500])
        layout.addWidget(content, 1)
        quick_actions = QtWidgets.QHBoxLayout()
        self.target_button = QtWidgets.QPushButton("선택 단계 대상 프로그램 변경…")
        self.target_button.clicked.connect(self._change_selected_targets)
        quick_actions.addWidget(self.target_button)
        self.visual_button = QtWidgets.QPushButton("이미지·영역 미리보기…")
        self.visual_button.clicked.connect(self._edit_selected_image_visually)
        quick_actions.addWidget(self.visual_button)
        self.replace_image_button = QtWidgets.QPushButton("이미지만 교체…")
        self.replace_image_button.clicked.connect(self._replace_selected_image)
        quick_actions.addWidget(self.replace_image_button)
        self.click_button = QtWidgets.QPushButton("클릭 위치 다시 찍기…")
        self.click_button.clicked.connect(self._repick_selected_click)
        quick_actions.addWidget(self.click_button)
        quick_actions.addStretch(1)
        layout.addLayout(quick_actions)
        buttons = QtWidgets.QHBoxLayout()
        self.edit_button = QtWidgets.QPushButton("선택 단계 상세 설정…")
        self.edit_button.clicked.connect(self._edit_selected_step)
        self.steps_list.itemSelectionChanged.connect(self._update_edit_buttons)
        buttons.addWidget(self.edit_button)
        buttons.addStretch(1)
        close_button = QtWidgets.QPushButton("닫기")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self._reload_steps()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self.mini_canvas.fit_all)

    def _select_step_from_canvas(self, index: int) -> None:
        for row in range(self.steps_list.topLevelItemCount()):
            item = self.steps_list.topLevelItem(row)
            if item.data(0, QtCore.Qt.UserRole) == index - 1:
                command = (QtCore.QItemSelectionModel.Select if self.mini_canvas.last_click_shift
                           else QtCore.QItemSelectionModel.ClearAndSelect)
                self.steps_list.setCurrentItem(item, 0, command)
                self.mini_canvas.last_click_shift = False
                self.steps_list.scrollToItem(item)
                return

    def _sync_canvas_selection(self) -> None:
        selected = self.steps_list.selectedItems()
        if selected:
            current = self.steps_list.currentItem()
            item = current if current in selected else selected[-1]
            self.mini_canvas.select_step(int(item.data(0, QtCore.Qt.UserRole)) + 1)

    def _selected_indexes(self) -> list[int]:
        return sorted(int(item.data(0, QtCore.Qt.UserRole)) for item in self.steps_list.selectedItems())

    def _update_edit_buttons(self) -> None:
        indexes = self._selected_indexes()
        steps = self.current_macro.get("steps") or []
        self.target_button.setEnabled(any(
            0 <= index < len(steps) and isinstance(steps[index], dict)
            and (str(steps[index].get("action") or "") in MACRO_REGION_ACTIONS | MACRO_WINDOW_ACTIONS
                 or any(key in steps[index] for key in ("region_window_exe", "window_exe", "target_exe")))
            for index in indexes
        ))
        self.edit_button.setEnabled(len(indexes) == 1)
        action = str(steps[indexes[0]].get("action") or "") if len(indexes) == 1 and isinstance(steps[indexes[0]], dict) else ""
        self.visual_button.setEnabled(action in MACRO_IMAGE_ACTIONS)
        image_step = steps[indexes[0]] if action in MACRO_IMAGE_ACTIONS else {}
        aliases = image_step.get("assets") if isinstance(image_step.get("assets"), list) else []
        self.replace_image_button.setEnabled(action in MACRO_IMAGE_ACTIONS and len(aliases) <= 1)
        self.click_button.setEnabled(action in {"mouse_click", "inactive_click"})

    def _edit_double_clicked_step(self, item: QtWidgets.QTreeWidgetItem) -> None:
        self.steps_list.clearSelection()
        item.setSelected(True)
        self._edit_selected_step()

    def _save_one_step(self, index: int, original: dict[str, Any], updated: dict[str, Any]) -> bool:
        try:
            latest = self.repository.load_macro(self.macro_name)
            latest_steps = latest.get("steps") or []
            if not 0 <= index < len(latest_steps) or latest_steps[index] != original:
                QtWidgets.QMessageBox.warning(self, "매크로가 변경됨", "다른 창에서 이 단계가 변경되었습니다. 목록을 다시 불러온 뒤 편집해 주세요.")
                self._reload_steps(index)
                return False
            if updated != original:
                latest_steps[index] = updated
                self.repository.save_macro(self.macro_name, latest)
            self._reload_steps(index, [index])
            return True
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "상세 설정 저장 실패", str(exc))
            return False

    def _change_selected_targets(self) -> None:
        indexes = self._selected_indexes()
        if not indexes:
            return
        steps = self.current_macro.get("steps") or []
        existing = {self.steps_list.topLevelItem(index).text(2) for index in indexes}
        current = next(iter(existing)) if len(existing) == 1 and "," not in next(iter(existing)) else ""
        dialog = DeckTargetProgramDialog(len(indexes), current, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        executable = dialog.selected_executable()
        try:
            latest = self.repository.load_macro(self.macro_name)
            latest_steps = latest.get("steps") or []
            if any(not 0 <= index < len(latest_steps) or latest_steps[index] != steps[index] for index in indexes):
                QtWidgets.QMessageBox.warning(self, "매크로가 변경됨", "다른 창에서 선택 단계가 변경되었습니다. 목록을 다시 불러온 뒤 다시 선택해 주세요.")
                self._reload_steps()
                return
            changed = 0
            for index in indexes:
                if isinstance(latest_steps[index], dict) and set_macro_step_target_program(latest_steps[index], executable):
                    changed += 1
            if changed:
                self.repository.save_macro(self.macro_name, latest)
            self._reload_steps(indexes[0], indexes)
            if changed < len(indexes):
                QtWidgets.QMessageBox.information(self, "대상 프로그램 변경", f"{changed}개 단계에 적용했습니다. 대상 프로그램 설정이 없는 단계는 건너뛰었습니다.")
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "대상 프로그램 변경 실패", str(exc))

    def _edit_selected_image_visually(self) -> None:
        from .region_visual_test import RegionVisualTestDialog

        indexes = self._selected_indexes()
        if len(indexes) != 1:
            return
        index = indexes[0]
        original = copy.deepcopy(self.current_macro["steps"][index])
        if str(original.get("action") or "") not in MACRO_IMAGE_ACTIONS:
            return
        visual = RegionVisualTestDialog(original, self.repository, parent=self)
        try:
            if visual.exec() == QtWidgets.QDialog.Accepted:
                self._save_one_step(index, original, merge_macro_visual_edit(original, visual))
        finally:
            visual.deleteLater()
            self.setEnabled(True)
            self.raise_()
            self.activateWindow()

    def _replace_selected_image(self) -> None:
        indexes = self._selected_indexes()
        if len(indexes) != 1:
            return
        index = indexes[0]
        original = copy.deepcopy(self.current_macro["steps"][index])
        if str(original.get("action") or "") not in MACRO_IMAGE_ACTIONS:
            return
        aliases = original.get("assets") if isinstance(original.get("assets"), list) else []
        if len(aliases) > 1:
            QtWidgets.QMessageBox.information(self, "이미지 교체", "이미지가 여러 개인 단계는 '이미지·영역 미리보기'에서 원하는 이미지를 선택해 주세요.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "검색 이미지 교체", "", "이미지 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        if QtGui.QImage(path).isNull():
            QtWidgets.QMessageBox.warning(self, "이미지 교체", "선택한 이미지 파일을 열 수 없습니다.")
            return
        try:
            latest = self.repository.load_macro(self.macro_name)
            latest_steps = latest.get("steps") or []
            if not 0 <= index < len(latest_steps) or latest_steps[index] != original:
                QtWidgets.QMessageBox.warning(self, "매크로가 변경됨", "다른 창에서 이 단계가 변경되었습니다. 다시 불러온 뒤 교체해 주세요.")
                self._reload_steps(index)
                return
            source = Path(path)
            alias = self.repository.add_asset(source, f"{source.stem}-deck-{uuid.uuid4().hex[:8]}")
            updated = copy.deepcopy(original)
            old_alias = str(updated.get("asset") or (aliases[0] if aliases else ""))
            updated["asset"] = alias
            updated["assets"] = [alias]
            for key in ("asset_regions", "asset_offsets", "asset_confidences", "asset_routes"):
                mapping = updated.get(key)
                if isinstance(mapping, dict):
                    old_value = mapping.get(old_alias)
                    if old_value is not None:
                        updated[key] = {alias: old_value}
                    else:
                        updated.pop(key, None)
            if str(updated.get("label") or "").endswith(f"({old_alias})") and old_alias:
                updated["label"] = str(updated["label"]).removesuffix(f"({old_alias})") + f"({alias})"
            if "required_count" in updated:
                updated["required_count"] = 1
            latest_steps[index] = updated
            self.repository.save_macro(self.macro_name, latest)
            self._reload_steps(index, [index])
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "이미지 교체 실패", str(exc))

    def _repick_selected_click(self) -> None:
        from .action_editor import WindowPickerDialog

        indexes = self._selected_indexes()
        if len(indexes) != 1:
            return
        index = indexes[0]
        original = copy.deepcopy(self.current_macro["steps"][index])
        action = str(original.get("action") or "")
        if action not in {"mouse_click", "inactive_click"}:
            return
        ignored = {int(self.winId())}
        if self.parentWidget() is not None:
            ignored.add(int(self.parentWidget().winId()))
        picker = WindowPickerDialog(self, ignored_hwnds=ignored,
                                    hint_text="클릭할 위치를 대상 프로그램에서 클릭하세요 · Esc 취소")
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        point = picker.selected_client_point() if accepted else None
        self.raise_()
        self.activateWindow()
        if not accepted or point is None:
            return
        updated = copy.deepcopy(original)
        if picker.exe_name:
            set_macro_step_target_program(updated, picker.exe_name)
        updated["window"] = picker.window_token
        updated["x"], updated["y"] = point.x(), point.y()
        updated["coordinate_scope"] = "client"
        if action == "mouse_click":
            updated["window_hwnd"] = picker.window_hwnd
        else:
            updated["coordinate_source"] = "fixed"
        self._save_one_step(index, original, updated)

    def _reload_steps(self, selected_index: int = 0, selected_indexes: Optional[list[int]] = None) -> None:
        from .action_editor import ACTION_LABELS

        self.current_macro = self.repository.load_macro(self.macro_name)
        steps = self.current_macro.get("steps") or []
        self.steps_list.clear()
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "wait")
            title = str(step.get("label") or step.get("entry_name") or ACTION_LABELS.get(action, action))
            targets = [str(step.get(key) or "").strip() for key in ("window_exe", "region_window_exe", "target_exe")]
            click = step.get("click")
            if isinstance(click, dict):
                targets.append(str(click.get("window_exe") or "").strip())
            target_summary = ", ".join(dict.fromkeys(value for value in targets if value))
            item = QtWidgets.QTreeWidgetItem([str(index + 1), title, target_summary])
            item.setData(0, QtCore.Qt.UserRole, index)
            for column in range(3):
                item.setForeground(column, QtGui.QBrush(QtGui.QColor("#F4F6FA")))
            item.setToolTip(1, ACTION_LABELS.get(action, action))
            self.steps_list.addTopLevelItem(item)
        self.mini_canvas.set_macro(self.current_macro)
        self.steps_list.resizeColumnToContents(0)
        if self.steps_list.topLevelItemCount():
            self.steps_list.setCurrentItem(
                self.steps_list.topLevelItem(min(selected_index, self.steps_list.topLevelItemCount() - 1)),
                0, QtCore.QItemSelectionModel.NoUpdate,
            )
        for row in selected_indexes or []:
            if 0 <= row < self.steps_list.topLevelItemCount():
                self.steps_list.topLevelItem(row).setSelected(True)
        self._update_edit_buttons()

    def _edit_selected_step(self) -> None:
        from .action_editor import ActionEditorDialog

        selected = self.steps_list.selectedItems()
        if len(selected) != 1:
            return
        index = int(selected[0].data(0, QtCore.Qt.UserRole))
        steps = self.current_macro.get("steps") or []
        if not 0 <= index < len(steps) or not isinstance(steps[index], dict):
            return
        original = copy.deepcopy(steps[index])
        editor = ActionEditorDialog(self.repository, original, self)
        if editor.exec() != QtWidgets.QDialog.Accepted:
            return
        self._save_one_step(index, original, editor.payload())


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
        default_macro_placeholder = (
            kind == "run_macro" and not icon_source
            and not self.icon_config.get("image_data") and not self.icon_config.get("image_path")
            and self.icon_config.get("emoji") == ACTION_ICONS["run_macro"]
            and int(self.icon_config.get("icon_size") or 0) == 58
        )
        self._icon_manually_edited = icon_source == "manual" or (
            has_visual and icon_source != "program_auto" and not default_macro_placeholder
        )
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
        elif kind in {"activate_browser_tab", "navigate_browser_tab"}:
            browser = QtWidgets.QComboBox()
            for title, name in (("네이버 웨일", "whale"), ("Microsoft Edge", "edge"), ("Google Chrome", "chrome")):
                browser.addItem(title, name)
            browser.setCurrentIndex(max(0, browser.findData(str(self.action.get("browser") or "whale"))))
            url = QtWidgets.QLineEdit(str(self.action.get("url") or ""))
            url.setPlaceholderText("https://www.example.com/")
            match = QtWidgets.QComboBox()
            match.addItem("같은 도메인의 열린 탭", "domain")
            match.addItem("같은 주소 경로의 열린 탭", "origin_path")
            match.setCurrentIndex(max(0, match.findData(str(self.action.get("match") or "domain"))))
            preset = QtWidgets.QComboBox()
            for preset_id, entry in self.main_window.config.get("slot_presets", {}).items():
                preset.addItem(str(entry.get("name") or preset_id), preset_id)
            preset.setCurrentIndex(max(0, preset.findData(str(self.action.get("preset_id") or ""))))
            self.widgets.update(browser=browser, url=url, match=match, preset_id=preset)
            form.addRow("브라우저", browser)
            form.addRow("사이트 주소", url)
            form.addRow("열린 탭 찾기", match)
            form.addRow("전환할 프리셋", preset)
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
            entry_combo = QtWidgets.QComboBox()
            saved_entry = str(self.action.get("entry_name") or "").strip()

            def refresh_entries(selected_entry: str = "") -> None:
                entry_combo.clear()
                entry_combo.addItem("기본 시작 지점", "")
                macro_name = combo.currentText().strip()
                if macro_name:
                    try:
                        for name, index in macro_entry_points(self.repository.load_macro(macro_name)):
                            entry_combo.addItem(f"▶ {name} · {index}번 노드", name)
                    except (OSError, ValueError, KeyError):
                        pass
                if selected_entry and entry_combo.findData(selected_entry) < 0:
                    entry_combo.addItem(f"⚠ 찾을 수 없는 시작 지점: {selected_entry}", selected_entry)
                entry_combo.setCurrentIndex(max(0, entry_combo.findData(selected_entry)))

            combo.currentTextChanged.connect(lambda _text: refresh_entries())
            refresh_entries(saved_entry)
            self.widgets["macro"] = combo
            self.widgets["entry_name"] = entry_combo
            form.addRow("매크로", combo)
            form.addRow("시작 지점", entry_combo)
            edit_macro = QtWidgets.QPushButton("매크로 내부 단계 상세 편집…")
            edit_macro.setToolTip("Studio의 단계 상세 설정을 덕덱에서 열어 원본 매크로를 편집합니다.")
            edit_macro.clicked.connect(lambda: self._edit_macro_steps(refresh_entries))
            form.addRow("상세 설정", edit_macro)
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

    def _edit_macro_steps(self, refresh_entries: Any) -> None:
        macro_name = self.widgets["macro"].currentText().strip()
        if not macro_name or not self.repository.macro_path(macro_name).exists():
            QtWidgets.QMessageBox.information(self, "매크로 선택", "편집할 매크로를 먼저 선택해 주세요.")
            return
        try:
            dialog = DeckMacroStepsDialog(self.repository, macro_name, self)
            dialog.exec()
            refresh_entries(str(self.widgets["entry_name"].currentData() or ""))
        except (OSError, ValueError, KeyError) as exc:
            QtWidgets.QMessageBox.warning(self, "매크로 열기 실패", str(exc))

    def _add_target_fields(self, form: QtWidgets.QFormLayout, *, include_point: bool = False) -> None:
        mode = QtWidgets.QComboBox()
        mode.addItem("비활성 · 창을 앞으로 가져오지 않음", "inactive")
        mode.addItem("활성 · 창을 앞으로 가져온 뒤 실행", "active")
        mode.setCurrentIndex(max(0, mode.findData(str(self.action.get("input_mode") or "inactive"))))
        target = QtWidgets.QLineEdit(str(self.action.get("target_title") or self.action.get("target_window") or ""))
        target.setPlaceholderText("오른쪽 버튼으로 화면에서 프로그램을 선택하세요")
        target_window = QtWidgets.QLineEdit(str(self.action.get("target_window") or "")); target_window.setVisible(False)
        target_exe = QtWidgets.QLineEdit(str(self.action.get("target_exe") or ""))
        target_exe.setPlaceholderText("예: msedge.exe")
        target_exe.textEdited.connect(lambda _text: (target.clear(), target_window.clear()))
        def set_executable(name: str) -> None:
            if name:
                target_exe.setText(name)
                target.clear()
                target_window.clear()
        browser_button = browser_choice_button(set_executable, self)
        find_button = QtWidgets.QPushButton("찾기…")
        find_button.clicked.connect(lambda: set_executable(browse_executable_name(self)))
        exe_row = QtWidgets.QHBoxLayout()
        exe_row.addWidget(target_exe, 1)
        exe_row.addWidget(browser_button)
        exe_row.addWidget(find_button)
        pick = QtWidgets.QPushButton("⌖ 화면에서 선택")
        pick.clicked.connect(lambda: self._pick_target(include_point))
        row = QtWidgets.QHBoxLayout(); row.addWidget(target, 1); row.addWidget(pick)
        self.widgets.update(input_mode=mode, target_title=target, target_window=target_window, target_exe=target_exe)
        form.addRow("실행 방식", mode); form.addRow("대상 창", row); form.addRow("대상 프로그램", exe_row)
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
        elif self.kind == "run_macro" and not self._icon_manually_edited:
            from macro_studio.quickslot_deck import extract_program_icon_config, macro_program_icon_target

            macro_widget = self.widgets.get("macro")
            entry_widget = self.widgets.get("entry_name")
            macro_name = macro_widget.currentText().strip() if isinstance(macro_widget, QtWidgets.QComboBox) else ""
            entry_name = str(entry_widget.currentData() or "") if isinstance(entry_widget, QtWidgets.QComboBox) else ""
            target = macro_program_icon_target(self.repository, macro_name, entry_name)
            if target:
                extracted = extract_program_icon_config(target)
                if extracted:
                    self.icon_config = extracted
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
        "run_macro": [("macro", "매크로", "macro"), ("entry_name", "시작 지점", "entry_name")],
        "text": [("text", "입력할 텍스트", "text"), ("text_method", "입력 엔진", "text_method"), ("key_interval_ms", "글자 입력 간격", "ms"), ("press_enter", "입력 후 Enter", "bool"), ("input_mode", "실행 방식", "input_mode"), ("target_exe", "대상 프로그램 / 브라우저", "target_exe")],
        "wait": [("ms", "대기 시간", "ms")],
        "multi_macros": [("delay_ms", "작업 간격", "ms"), ("continue_on_error", "실패해도 계속", "bool")],
        "mouse_click": [("button", "마우스 버튼", "button"), ("clicks", "클릭 횟수", "clicks"), ("input_mode", "실행 방식", "input_mode"), ("target_exe", "대상 프로그램 / 브라우저", "target_exe")],
        "hotkey": [("keys", "키 조합", "line"), ("input_mode", "실행 방식", "input_mode"), ("target_exe", "대상 프로그램 / 브라우저", "target_exe")],
        "activate_browser_tab": [("browser", "브라우저", "browser")],
        "navigate_browser_tab": [("browser", "브라우저", "browser")],
        "open_target": [("target", "실행 대상", "line")],
        "terminate_program": [("process", "프로세스 이름", "line")],
        "page_goto": [("page", "이동할 페이지", "page")],
    }

    def __init__(self, kind: str, sample: Dict[str, Any], count: int, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.kind = kind
        self.repository = getattr(parent, "repository", None)
        self.controls: Dict[str, tuple[QtWidgets.QCheckBox, QtWidgets.QWidget]] = {}
        self._macro_combo: Optional[QtWidgets.QComboBox] = None
        self._entry_combo: Optional[QtWidgets.QComboBox] = None
        self.setWindowTitle(f"{count}개 슬롯 일괄 편집")
        self.setMinimumWidth(520)
        self.setStyleSheet(deck_dock_stylesheet())
        root = QtWidgets.QVBoxLayout(self)
        hint_text = "체크한 항목만 선택한 모든 슬롯에 적용됩니다."
        if kind == "run_macro":
            hint_text += " 매크로를 바꾸고 시작 지점을 체크하지 않으면 기본 시작 지점으로 돌아갑니다."
        hint = QtWidgets.QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setObjectName("Hint"); root.addWidget(hint)
        form = QtWidgets.QFormLayout(); form.setSpacing(10)
        specs = [("label", "슬롯 이름", "line")] + self.FIELD_SPECS.get(kind, [])
        for key, title, widget_kind in specs:
            enabled = QtWidgets.QCheckBox(title)
            widget = self._make_widget(widget_kind, sample.get(key))
            widget.setEnabled(False); enabled.toggled.connect(widget.setEnabled)
            if key == "target_exe":
                def set_executable(name: str, *, combo=widget, check=enabled) -> None:
                    if not name:
                        return
                    index = combo.findData(name)
                    if index >= 0:
                        combo.setCurrentIndex(index)
                    else:
                        combo.setEditText(name)
                    check.setChecked(True)
                browser_button = browser_choice_button(set_executable, self)
                find_button = QtWidgets.QPushButton("찾기…")
                find_button.clicked.connect(lambda _checked=False, setter=set_executable: setter(browse_executable_name(self)))
                row = QtWidgets.QHBoxLayout()
                row.addWidget(widget, 1)
                row.addWidget(browser_button)
                row.addWidget(find_button)
                form.addRow(enabled, row)
            else:
                form.addRow(enabled, widget)
            self.controls[key] = (enabled, widget)
        if self._macro_combo is not None:
            self._macro_combo.currentIndexChanged.connect(lambda _index: self._refresh_entry_choices(""))
        root.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("저장")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def _make_widget(self, kind: str, value: Any) -> QtWidgets.QWidget:
        if kind == "macro":
            combo = QtWidgets.QComboBox()
            names = [summary.name for summary in self.repository.list_macros()] if self.repository else []
            current = str(value or "").strip()
            if current and current not in names:
                names.insert(0, current)
            for name in names:
                combo.addItem(name, name)
            combo.setCurrentIndex(combo.findData(current))
            self._macro_combo = combo
            return combo
        if kind == "entry_name":
            self._entry_combo = QtWidgets.QComboBox()
            self._refresh_entry_choices(str(value or ""))
            return self._entry_combo
        if kind == "text":
            widget = QtWidgets.QPlainTextEdit(str(value or "")); widget.setMaximumHeight(110); return widget
        if kind in {"ms", "clicks", "page"}:
            widget = QtWidgets.QSpinBox(); widget.setRange(0 if kind == "ms" else 1, 600000 if kind == "ms" else 99); widget.setValue(int(value or (1 if kind != "ms" else 0))); return widget
        if kind == "bool":
            widget = QtWidgets.QCheckBox("사용"); widget.setChecked(bool(value)); return widget
        if kind == "target_exe":
            widget = QtWidgets.QComboBox()
            widget.setEditable(True)
            for title, executable in BROWSER_EXECUTABLES:
                widget.addItem(f"{title} · {executable}", executable)
            current = str(value or "").strip()
            index = widget.findData(current)
            widget.setCurrentIndex(index if index >= 0 else -1)
            if index < 0:
                widget.setEditText(current)
            return widget
        if kind in {"text_method", "input_mode", "button", "browser"}:
            widget = QtWidgets.QComboBox()
            choices = {
                "text_method": [("자동", "auto"), ("클립보드", "clipboard"), ("글자별 입력", "type"), ("WM_CHAR", "wm_char"), ("WM_SETTEXT", "set_text")],
                "input_mode": [("비활성", "inactive"), ("활성", "active")],
                "button": [("좌클릭", "left"), ("우클릭", "right"), ("가운데 클릭", "middle")],
                "browser": [("네이버 웨일", "whale"), ("Microsoft Edge", "edge"), ("Google Chrome", "chrome")],
            }[kind]
            for label, data in choices: widget.addItem(label, data)
            widget.setCurrentIndex(max(0, widget.findData(str(value or choices[0][1])))); return widget
        return QtWidgets.QLineEdit(str(value or ""))

    def _refresh_entry_choices(self, selected_entry: str) -> None:
        combo = self._entry_combo
        if combo is None:
            return
        macro_name = str(self._macro_combo.currentData() or "") if self._macro_combo else ""
        with QtCore.QSignalBlocker(combo):
            combo.clear()
            combo.addItem("기본 시작 지점", "")
            if self.repository and macro_name:
                try:
                    for name, index in macro_entry_points(self.repository.load_macro(macro_name)):
                        combo.addItem(f"▶ {name} · {index}번 노드", name)
                except (OSError, ValueError, KeyError):
                    pass
            if selected_entry and combo.findData(selected_entry) < 0:
                combo.addItem(f"⚠ 찾을 수 없는 시작 지점: {selected_entry}", selected_entry)
            combo.setCurrentIndex(max(0, combo.findData(selected_entry)))

    def patch(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, (enabled, widget) in self.controls.items():
            if not enabled.isChecked(): continue
            if isinstance(widget, QtWidgets.QPlainTextEdit): result[key] = widget.toPlainText()
            elif isinstance(widget, QtWidgets.QLineEdit): result[key] = widget.text()
            elif isinstance(widget, QtWidgets.QSpinBox): result[key] = widget.value()
            elif isinstance(widget, QtWidgets.QCheckBox): result[key] = widget.isChecked()
            elif isinstance(widget, QtWidgets.QComboBox):
                if key == "target_exe":
                    index = widget.currentIndex()
                    result[key] = (widget.itemData(index) if index >= 0 and widget.currentText() == widget.itemText(index)
                                   else widget.currentText().strip())
                else:
                    result[key] = widget.currentData()
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
        self._selection_drag_origin: Optional[QtCore.QPoint] = None
        self._selection_drag_base: set[int] = set()
        self._undo_stack: list[Dict[str, Any]] = []
        self._redo_stack: list[Dict[str, Any]] = []
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("MacroRelay · Deck Dock")
        self.setWindowIcon(main_window.windowIcon())
        self.resize(1240, 760)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(deck_dock_stylesheet())
        self._init_ui()
        QtGui.QShortcut(QtGui.QKeySequence.Undo, self, activated=self.undo)
        QtGui.QShortcut(QtGui.QKeySequence.Redo, self, activated=self.redo)
        self.reload()

    def _init_ui(self) -> None:
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root); outer.setContentsMargins(18, 16, 18, 16); outer.setSpacing(12)
        toolbar = QtWidgets.QFrame(); toolbar.setObjectName("Toolbar")
        top = QtWidgets.QHBoxLayout(toolbar); top.setContentsMargins(16, 10, 16, 10)
        title = QtWidgets.QLabel("Deck Dock"); title.setObjectName("Title")
        self.preset_combo = QtWidgets.QComboBox(); self.preset_combo.setMinimumWidth(220); self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        save = QtWidgets.QPushButton("저장 및 닫기"); save.clicked.connect(self._save_and_close)
        bundled = QtWidgets.QPushButton("내장 구성 불러오기"); bundled.clicked.connect(self._load_bundled_backup)
        self.grid_rows_spin = QtWidgets.QSpinBox(); self.grid_rows_spin.setRange(1, 10); self.grid_rows_spin.setSuffix(" 행")
        self.grid_cols_spin = QtWidgets.QSpinBox(); self.grid_cols_spin.setRange(1, 10); self.grid_cols_spin.setSuffix(" 열")
        self.grid_apply_btn = QtWidgets.QPushButton("그리드 적용"); self.grid_apply_btn.clicked.connect(self._apply_grid_size)
        self.pin_selected_btn = QtWidgets.QPushButton("📌 선택 슬롯 고정/해제")
        self.pin_selected_btn.clicked.connect(self._toggle_selected_pin)
        self.undo_btn = QtWidgets.QPushButton("↶ 실행 취소"); self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QtWidgets.QPushButton("↷ 다시 실행"); self.redo_btn.clicked.connect(self.redo)
        history = QtWidgets.QPushButton("실행 기록"); history.clicked.connect(self._show_execution_history)
        top.addWidget(title); top.addSpacing(16); top.addWidget(QtWidgets.QLabel("프리셋")); top.addWidget(self.preset_combo)
        top.addSpacing(14); top.addWidget(QtWidgets.QLabel("그리드")); top.addWidget(self.grid_rows_spin); top.addWidget(QtWidgets.QLabel("×")); top.addWidget(self.grid_cols_spin); top.addWidget(self.grid_apply_btn)
        top.addStretch(1); top.addWidget(self.pin_selected_btn); top.addWidget(bundled); top.addWidget(save)
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
        self.selection_toggle.setToolTip("켜면 슬롯이나 빈 바닥에서 드래그해 여러 슬롯을 선택할 수 있습니다. Ctrl+드래그는 기존 선택에 추가합니다.")
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
        self.grid_host = DeckSelectionGrid(); self.grid = QtWidgets.QGridLayout(self.grid_host); self.grid.setSpacing(9)
        self.grid_host.selection_drag_started.connect(self._start_selection_drag)
        self.grid_host.selection_drag_moved.connect(self._move_selection_drag)
        self.grid_host.selection_drag_released.connect(self._finish_selection_drag)
        self.selection_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self.grid_host)
        center_layout.addWidget(self.grid_host, 1)
        pager = QtWidgets.QGridLayout(); self.prev_btn = QtWidgets.QPushButton("◀"); self.next_btn = QtWidgets.QPushButton("▶"); self.add_page_btn = QtWidgets.QPushButton("＋ 페이지"); self.delete_page_btn = QtWidgets.QPushButton("－ 페이지")
        self.page_label = QtWidgets.QLabel(); self.page_label.setAlignment(QtCore.Qt.AlignCenter); self.page_label.setMinimumWidth(120)
        self.prev_btn.clicked.connect(self.prev_page); self.next_btn.clicked.connect(self.next_page); self.add_page_btn.clicked.connect(self.add_page); self.delete_page_btn.clicked.connect(self.delete_page)
        manage = QtWidgets.QHBoxLayout(); manage.addWidget(self.add_page_btn); manage.addWidget(self.delete_page_btn); manage.addStretch(1)
        navigation = QtWidgets.QHBoxLayout(); navigation.addWidget(self.prev_btn); navigation.addWidget(self.page_label); navigation.addWidget(self.next_btn)
        history_controls = QtWidgets.QHBoxLayout()
        history_controls.addStretch(1)
        history_controls.addWidget(history)
        history_controls.addWidget(self.undo_btn)
        history_controls.addWidget(self.redo_btn)
        pager.addLayout(manage, 0, 0); pager.addLayout(navigation, 0, 1, QtCore.Qt.AlignCenter); pager.addLayout(history_controls, 0, 2)
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

    def _show_execution_history(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Deck 실행 기록")
        dialog.resize(650, 430)
        dialog.setStyleSheet(deck_dock_stylesheet())
        layout = QtWidgets.QVBoxLayout(dialog)
        listing = QtWidgets.QPlainTextEdit()
        listing.setReadOnly(True)
        entries = list(reversed(self.main_window.execution_history))
        listing.setPlainText("\n".join(
            f"{item['time']}  [{item['preset']}] {item['slot']}번  {item['state']}  {item['detail']}"
            for item in entries
        ) or "이번 실행 중 기록이 없습니다.")
        layout.addWidget(listing)
        close = QtWidgets.QPushButton("닫기")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _load_bundled_backup(self) -> None:
        from macro_studio.quickslot_deck import bundled_deck_path

        bundle_path = bundled_deck_path(self.main_window.repository.root)
        if not bundle_path.is_file():
            QtWidgets.QMessageBox.warning(self, "내장 구성", "내장 Deck JSON 파일을 찾을 수 없습니다.")
            return
        answer = QtWidgets.QMessageBox.question(self, "내장 구성", "현재 Deck 구성을 GitHub 내장 구성으로 교체할까요?")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            payload = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.main_window.restore_deck_backup_payload(payload)
            self.current_page = 0
            self.selected_slots.clear()
            self.reload()
            report = getattr(self.main_window, "last_portability_report", {}) or {}
            caution = (
                f"\n다시 확인할 항목: 실행 파일 {report.get('unresolved_programs', 0)}개, "
                f"화면 절대 좌표 {report.get('screen_coordinates', 0)}개."
                if report.get("unresolved_programs") or report.get("screen_coordinates") else ""
            )
            QtWidgets.QMessageBox.information(self, "내장 구성", "페이지·슬롯 액션·아이콘을 모두 불러왔습니다." + caution)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "내장 구성 오류", str(exc))

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
        self._undo_stack.clear(); self._redo_stack.clear()
        self._update_history_buttons()
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

    def _snapshot(self) -> Dict[str, Any]:
        return {
            "payload": copy.deepcopy(self.payload),
            "icons": copy.deepcopy(self.main_window.custom_icons),
            "pins": copy.deepcopy(self.main_window._active_pinned_slots()),
            "rows": self.main_window.rows,
            "cols": self.main_window.cols,
            "page": self.current_page,
            "selection": set(self.selected_slots),
        }

    def _record_change(self) -> None:
        self._undo_stack.append(self._snapshot())
        self._undo_stack = self._undo_stack[-30:]
        self._redo_stack.clear()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.undo_btn.setEnabled(bool(self._undo_stack))
        self.redo_btn.setEnabled(bool(self._redo_stack))

    def _restore_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.payload = copy.deepcopy(snapshot["payload"])
        self.main_window.custom_icons = copy.deepcopy(snapshot["icons"])
        self.main_window.rows = int(snapshot["rows"])
        self.main_window.cols = int(snapshot["cols"])
        pins = self.main_window._active_pinned_slots()
        pins.clear(); pins.update(copy.deepcopy(snapshot["pins"]))
        self.page_count = int(self.payload.get("deck_page_count") or 1)
        self.current_page = int(snapshot["page"])
        self.selected_slots = set(snapshot["selection"])
        self.save()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._undo_stack.pop())
        self._update_history_buttons()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())
        self._update_history_buttons()

    def _render_page(self) -> None:
        self.grid_host.selection_mode = self.selection_toggle.isChecked()
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        page_size = self._page_size(); start = self.current_page * page_size
        rows, cols = int(self.main_window.rows), int(self.main_window.cols)
        pins = self.main_window._active_pinned_slots()
        for local in range(page_size):
            index = start + local
            pin = pins.get(str(local))
            pinned = isinstance(pin, dict) and isinstance(pin.get("slot"), dict)
            slot = pin["slot"] if pinned else self.payload["slots"][index]
            icon = pin.get("icon", {}) if pinned else self.main_window.custom_icons.get(str(index), {})
            button = DeckSlotButton(index, slot, icon, self.grid_host, selection_mode=self.selection_toggle.isChecked(), selected=index in self.selected_slots, pinned=pinned)
            button.setMinimumSize(max(48, min(92, 760 // max(1, cols))), max(44, min(82, 500 // max(1, rows))))
            button.action_dropped.connect(self._configure_new_action); button.slot_dropped.connect(self._move_slot); button.image_dropped.connect(self._apply_dropped_icon); button.edit_requested.connect(self._edit_slot); button.clear_requested.connect(self._clear_slot); button.duplicate_requested.connect(self._duplicate_slot); button.selection_requested.connect(self._toggle_slot_selection); button.pin_requested.connect(self._pin_slot); button.unpin_requested.connect(self._unpin_slot)
            button.selection_drag_started.connect(self._start_selection_drag)
            button.selection_drag_moved.connect(self._move_selection_drag)
            button.selection_drag_released.connect(self._finish_selection_drag)
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
        self._selection_drag_origin = None
        self.selection_band.hide()
        self.grid_host.selection_mode = enabled
        for button in self._visible_slot_buttons():
            button.selection_mode = enabled
        if not enabled:
            self.selected_slots.clear()
        self._update_selection_visuals()

    def _visible_slot_buttons(self) -> list[DeckSlotButton]:
        return [item.widget() for index in range(self.grid.count())
                if (item := self.grid.itemAt(index)) is not None
                and isinstance(item.widget(), DeckSlotButton)]

    def _update_selection_visuals(self) -> None:
        for button in self._visible_slot_buttons():
            button.set_selected(button.slot_index in self.selected_slots)
        self._update_selection_controls()

    def _start_selection_drag(self, origin: QtCore.QPoint, current: QtCore.QPoint, additive: bool) -> None:
        if not self.selection_toggle.isChecked():
            return
        self._selection_drag_origin = self.grid_host.mapFromGlobal(origin)
        self._selection_drag_base = set(self.selected_slots) if additive else set()
        self._move_selection_drag(current)
        self.selection_band.show()

    def _move_selection_drag(self, current: QtCore.QPoint) -> None:
        if self._selection_drag_origin is None:
            return
        end = self.grid_host.mapFromGlobal(current)
        self.selection_band.setGeometry(QtCore.QRect(self._selection_drag_origin, end).normalized())
        self.selection_band.raise_()

    def _finish_selection_drag(self, current: QtCore.QPoint) -> None:
        if self._selection_drag_origin is None:
            return
        self._move_selection_drag(current)
        rect = self.selection_band.geometry()
        self.selection_band.hide()
        self._selection_drag_origin = None
        selected = set(self._selection_drag_base)
        for local in range(self._page_size()):
            item = self.grid.itemAt(local)
            button = item.widget() if item is not None else None
            if button is not None and rect.intersects(button.geometry()):
                selected.add(button.slot_index)
        self.selected_slots = selected
        self._update_selection_visuals()

    def _toggle_slot_selection(self, index: int) -> None:
        if index in self.selected_slots: self.selected_slots.remove(index)
        else: self.selected_slots.add(index)
        self._update_selection_visuals()

    def _select_current_page(self) -> None:
        self.selection_toggle.setChecked(True)
        start = self.current_page * self._page_size()
        self.selected_slots.update(
            index for index in range(start, start + self._page_size())
            if self._is_filled_slot(self.payload["slots"][index])
        )
        self._update_selection_visuals()

    def _clear_selection(self) -> None:
        self.selected_slots.clear(); self._update_selection_visuals()

    def _update_selection_controls(self) -> None:
        count = len(self.selected_slots); self.selection_label.setText(f"선택 {count}개")
        self.move_selected_btn.setEnabled(count > 0); self.bulk_edit_btn.setEnabled(count > 0); self.clear_selection_btn.setEnabled(count > 0)
        self.pin_selected_btn.setEnabled(count == 1)

    def _toggle_selected_pin(self) -> None:
        if len(self.selected_slots) != 1:
            return
        index = next(iter(self.selected_slots))
        if self._pin_entry(index):
            self._unpin_slot(index)
        else:
            self._pin_slot(index)

    @staticmethod
    def _is_filled_slot(slot: Dict[str, Any]) -> bool:
        return bool(str(slot.get("macro") or "").strip() or slot.get("action"))

    def _pin_entry(self, index: int) -> Optional[Dict[str, Any]]:
        return self.main_window._active_pinned_slots().get(str(index % self._page_size()))

    def _pin_slot(self, index: int) -> None:
        local = index % self._page_size()
        if self._pin_entry(index) or not self._is_filled_slot(self.payload["slots"][index]):
            return
        conflicting = [page + 1 for page in range(self.page_count)
                       if page * self._page_size() + local != index and
                       self._is_filled_slot(self.payload["slots"][page * self._page_size() + local])]
        if conflicting:
            QtWidgets.QMessageBox.information(self, "공통 슬롯", f"{', '.join(map(str, conflicting))}페이지의 같은 위치에 슬롯이 있습니다. 먼저 빈 위치로 옮겨 주세요.")
            return
        self._record_change()
        self.main_window._active_pinned_slots()[str(local)] = {
            "slot": copy.deepcopy(self.payload["slots"][index]),
            "icon": copy.deepcopy(self.main_window.custom_icons.get(str(index), {})),
        }
        self.payload["slots"][index] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        self.main_window.custom_icons.pop(str(index), None)
        self.save()

    def _unpin_slot(self, index: int) -> None:
        pin = self._pin_entry(index)
        if not pin:
            return
        if self._is_filled_slot(self.payload["slots"][index]):
            QtWidgets.QMessageBox.information(self, "공통 슬롯", "현재 페이지의 같은 위치가 사용 중입니다. 빈 페이지에서 고정을 해제해 주세요.")
            return
        self._record_change()
        self.payload["slots"][index] = copy.deepcopy(pin["slot"])
        if pin.get("icon"):
            self.main_window.custom_icons[str(index)] = copy.deepcopy(pin["icon"])
        self.main_window._active_pinned_slots().pop(str(index % self._page_size()), None)
        self.save()

    def _move_selected_slots(self, target_page: int) -> tuple[bool, str]:
        if any(self._pin_entry(index) for index in self.selected_slots):
            return False, "고정 슬롯은 먼저 고정을 해제한 뒤 이동해 주세요."
        sources = [index for index in sorted(self.selected_slots) if self._is_filled_slot(self.payload["slots"][index])]
        if not sources: return False, "선택한 슬롯 중 이동할 항목이 없습니다."
        size = self._page_size(); start = target_page * size; end = start + size
        source_set = set(sources)
        targets = [index for index in range(start, end) if not self._pin_entry(index)
                   and (index in source_set or not self._is_filled_slot(self.payload["slots"][index]))]
        if len(targets) < len(sources): return False, f"대상 페이지에 빈 슬롯이 {len(sources)}개 필요합니다."
        self._record_change()
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
            if "target_exe" in patch and str(action.get("target_exe") or "").casefold() != str(patch["target_exe"] or "").casefold():
                action.pop("target_window", None)
                action.pop("target_title", None)
            if ("macro" in patch and "entry_name" not in patch
                    and str(action.get("macro") or "") != str(patch["macro"] or "")):
                action["entry_name"] = ""
            action.update(copy.deepcopy(patch)); self.payload["slots"][index] = self._slot_from_action(action); changed += 1
        return changed

    def _bulk_macro_patch_error(self, indexes: list[int], patch: Dict[str, Any]) -> str:
        if not ({"macro", "entry_name"} & patch.keys()):
            return ""
        known_macros = {summary.name for summary in self.repository.list_macros()}
        for index in indexes:
            action = self.payload["slots"][index].get("action") or {}
            macro_name = str(patch.get("macro") or action.get("macro") or "")
            if macro_name not in known_macros:
                return f"매크로를 찾을 수 없습니다: {macro_name or '(없음)'}"
            if "entry_name" not in patch:
                continue
            entry_name = str(patch["entry_name"] or "")
            if entry_name:
                try:
                    entries = {name for name, _number in macro_entry_points(self.repository.load_macro(macro_name))}
                except (OSError, ValueError, KeyError):
                    return f"시작 지점을 확인할 수 없습니다: {macro_name}"
                if entry_name not in entries:
                    return f"'{macro_name}' 매크로에는 '{entry_name}' 시작 지점이 없습니다."
        return ""

    def _bulk_edit_selected(self) -> None:
        if any(self._pin_entry(index) for index in self.selected_slots):
            QtWidgets.QMessageBox.information(self, "일괄 편집", "고정 슬롯은 개별 편집하거나 먼저 고정을 해제해 주세요.")
            return
        indexes = [index for index in sorted(self.selected_slots) if self.payload["slots"][index].get("action")]
        if not indexes:
            QtWidgets.QMessageBox.information(self, "일괄 편집", "Deck 액션이 설정된 슬롯을 선택해 주세요."); return
        kinds = {str(self.payload["slots"][index]["action"].get("kind") or "") for index in indexes}
        if len(kinds) != 1:
            QtWidgets.QMessageBox.information(self, "일괄 편집", "같은 종류의 액션 슬롯만 함께 편집할 수 있습니다."); return
        kind = next(iter(kinds)); sample = dict(self.payload["slots"][indexes[0]]["action"])
        dialog = DeckBulkEditDialog(kind, sample, len(indexes), self)
        while True:
            if dialog.exec() != QtWidgets.QDialog.Accepted: return
            patch = dialog.patch()
            if not patch: return
            error = self._bulk_macro_patch_error(indexes, patch) if kind == "run_macro" else ""
            if not error:
                break
            QtWidgets.QMessageBox.information(self, "일괄 편집", error)
        self._record_change()
        self._apply_bulk_patch(indexes, patch); self.save()

    def _apply_grid_size(self) -> None:
        new_rows, new_cols = self.grid_rows_spin.value(), self.grid_cols_spin.value()
        old_rows, old_cols = int(self.main_window.rows), int(self.main_window.cols)
        if (new_rows, new_cols) == (old_rows, old_cols):
            return
        old_size, new_size = max(1, old_rows * old_cols), max(1, new_rows * new_cols)
        old_pins = self.main_window._active_pinned_slots()
        remapped_pins: Dict[str, Any] = {}
        for local, entry in old_pins.items():
            old_row, old_col = divmod(int(local), old_cols)
            if old_row < new_rows and old_col < new_cols:
                remapped_pins[str(old_row * new_cols + old_col)] = entry
        if len(remapped_pins) != len(old_pins):
            QtWidgets.QMessageBox.information(self, "그리드", "새 그리드에 들어가지 않는 고정 슬롯이 있습니다. 고정을 해제하거나 더 큰 그리드를 선택해 주세요.")
            return
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
        if any(self._is_filled_slot(new_slots[page * new_size + int(local)])
               for page in range(len(new_names)) for local in remapped_pins):
            QtWidgets.QMessageBox.information(self, "그리드", "새 그리드에서 고정 슬롯과 일반 슬롯이 겹칩니다. 일반 슬롯을 먼저 옮겨 주세요.")
            return
        self._record_change()
        self.main_window.rows, self.main_window.cols = new_rows, new_cols
        old_pins.clear(); old_pins.update(remapped_pins)
        self.main_window.custom_icons = new_icons
        self.payload["slots"] = new_slots
        self.payload["deck_page_names"] = new_names
        self.page_count = len(new_names)
        self.current_page = min(self.current_page, self.page_count - 1)
        self.save()

    def _slot_from_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return {"macro": action_title(action), "hotkey": "", "mode": "deck_action", "action": action}

    def _configure_new_action(self, index: int, kind: str) -> None:
        if self._pin_entry(index):
            QtWidgets.QMessageBox.information(self, "공통 슬롯", "고정 슬롯은 클릭하여 편집하거나 먼저 고정을 해제해 주세요.")
            return
        dialog = DeckActionConfigDialog(
            kind, None, self.main_window, self, slot_index=index,
            icon_config=self.main_window.custom_icons.get(str(index), {}),
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._record_change()
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

        pin = self._pin_entry(index)
        previous = dict((pin.get("icon") if pin else self.main_window.custom_icons.get(str(index))) or {})
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
        self._record_change()
        if pin:
            pin["icon"] = previous
        else:
            self.main_window.custom_icons[str(index)] = previous
        self.main_window._capture_active_slot_preset()
        self.main_window._save_config()
        if pin:
            self.main_window.refresh_slots()
        else:
            self.main_window._preview_slot_icon(index, previous)
        self._render_page()

    def _edit_slot(self, index: int) -> None:
        pin = self._pin_entry(index)
        slot = pin["slot"] if pin else self.payload["slots"][index]
        action = dict(slot.get("action") or {})
        if not action:
            return
        dialog = DeckActionConfigDialog(
            str(action.get("kind") or ""), action, self.main_window, self,
            slot_index=index, icon_config=pin.get("icon", {}) if pin else self.main_window.custom_icons.get(str(index), {}),
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._record_change()
            updated_slot = self._slot_from_action(dialog.result_action())
            icon_config = dialog.result_icon_config()
            if pin:
                pin["slot"] = updated_slot
                pin["icon"] = icon_config or {}
            else:
                self.payload["slots"][index] = updated_slot
                if icon_config: self.main_window.custom_icons[str(index)] = icon_config
                else: self.main_window.custom_icons.pop(str(index), None)
            self.save()

    def _move_slot(self, source: int, target: int) -> None:
        if source == target: return
        if self._pin_entry(source) or self._pin_entry(target):
            QtWidgets.QMessageBox.information(self, "공통 슬롯", "고정 슬롯은 먼저 고정을 해제한 뒤 이동해 주세요.")
            return
        self._record_change()
        self.payload["slots"][source], self.payload["slots"][target] = self.payload["slots"][target], self.payload["slots"][source]
        source_icon = self.main_window.custom_icons.pop(str(source), None)
        target_icon = self.main_window.custom_icons.pop(str(target), None)
        if source_icon is not None: self.main_window.custom_icons[str(target)] = source_icon
        if target_icon is not None: self.main_window.custom_icons[str(source)] = target_icon
        self.save()

    def _clear_slot(self, index: int) -> None:
        self._record_change()
        if self._pin_entry(index):
            self.main_window._active_pinned_slots().pop(str(index % self._page_size()), None)
            self.save()
            return
        self.payload["slots"][index] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        self.main_window.custom_icons.pop(str(index), None)
        self.save()

    def _duplicate_slot(self, index: int) -> None:
        start = self.current_page * self._page_size(); end = start + self._page_size()
        empty = next((i for i in range(start, end)
                      if not self._pin_entry(i) and not self._is_filled_slot(self.payload["slots"][i])), None)
        if empty is None:
            QtWidgets.QMessageBox.information(self, "슬롯 복제", "현재 페이지에 빈 슬롯이 없습니다."); return
        self._record_change()
        pin = self._pin_entry(index)
        self.payload["slots"][empty] = copy.deepcopy(pin["slot"] if pin else self.payload["slots"][index])
        icon = pin.get("icon") if pin else self.main_window.custom_icons.get(str(index))
        if icon:
            self.main_window.custom_icons[str(empty)] = copy.deepcopy(icon)
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
        self._record_change()
        self.page_count += 1; self.payload["deck_page_names"].append(f"페이지 {self.page_count}")
        self.payload["slots"].extend({"macro":"", "hotkey":"", "mode":"hybrid"} for _ in range(self._page_size()))
        self.current_page = self.page_count - 1; self.save()

    def rename_page(self) -> None:
        current = self.payload["deck_page_names"][self.current_page]
        name, ok = QtWidgets.QInputDialog.getText(self, "페이지 이름", "새 페이지 이름", text=current)
        if ok and name.strip() and name.strip() != current:
            self._record_change()
            self.payload["deck_page_names"][self.current_page] = name.strip(); self.save()

    def delete_page(self) -> None:
        if self.page_count <= 1: return
        answer = QtWidgets.QMessageBox.question(self, "페이지 삭제", "현재 페이지와 포함된 슬롯을 삭제할까요?")
        if answer != QtWidgets.QMessageBox.Yes: return
        self._record_change()
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
