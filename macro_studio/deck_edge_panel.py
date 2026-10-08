"""Screen-edge layout for the experimental QuickSlot panel."""

from dataclasses import dataclass
import math

from PySide6 import QtCore, QtGui, QtWidgets


EDGE_PANEL_SIDES = {"left", "right", "top", "bottom"}
EDGE_PANEL_HEADER = 28
EDGE_PANEL_MIN_TILE = 48
EDGE_PANEL_MAX_TILE = 256


def slot_visual_positions(indexes: list[int], saved: object) -> dict[int, int]:
    """Keep action IDs intact; validate a separate, sparse visual placement map."""
    saved = saved if isinstance(saved, dict) else {}
    result, used = {}, set()
    limit = max(64, len(indexes) * 4)
    for index in indexes:
        value = saved.get(str(index))
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < limit and value not in used:
            result[index] = value
            used.add(value)
    next_position = 0
    for index in indexes:
        if index not in result:
            while next_position in used:
                next_position += 1
            result[index] = next_position
            used.add(next_position)
    return result


class DeckLayoutEditButton(QtWidgets.QToolButton):
    """A vector-only settings/check toggle, without platform-dependent emoji."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SlotLayoutToggle")
        self.setCheckable(True)
        self.setFixedSize(26, 27)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAccessibleName("슬롯 배치 편집")
        self.setStyleSheet(
            "QToolButton#SlotLayoutToggle { background: #182436; border: 1px solid #6A75AA; border-radius: 9px; }"
            "QToolButton#SlotLayoutToggle:hover { background: #2B3E59; }"
            "QToolButton#SlotLayoutToggle:checked { background: #133F36; border-color: #34D399; }")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#6EE7B7" if self.isChecked() else "#E2E8F0"),
                                1.7, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        if self.isChecked():
            painter.drawPolyline(QtGui.QPolygonF([
                QtCore.QPointF(6, 13), QtCore.QPointF(11, 18), QtCore.QPointF(20, 8)]))
        else:
            for y, x in ((7, 10), (13, 17), (19, 8)):
                painter.drawLine(5, y, 21, y)
                painter.setBrush(QtGui.QColor("#182436"))
                painter.drawEllipse(QtCore.QPointF(x, y), 2.2, 2.2)


class EdgePanelSizeDialog(QtWidgets.QDialog):
    """Touch-friendly, live size controls for docked tiles only."""

    size_changed = QtCore.Signal(int)

    def __init__(self, tile_side: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("가장자리 패널 · 슬롯 크기")
        self.setMinimumWidth(360)
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("조절하면 가장자리 패널에 즉시 적용됩니다.\n자유 배치의 슬롯 크기는 변경하지 않습니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        self.decrease_button = QtWidgets.QPushButton("− 축소")
        self.increase_button = QtWidgets.QPushButton("＋ 확대")
        self.size_spin = QtWidgets.QSpinBox()
        self.size_spin.setRange(EDGE_PANEL_MIN_TILE, EDGE_PANEL_MAX_TILE)
        self.size_spin.setSuffix(" px")
        self.size_spin.setKeyboardTracking(False)
        for widget in (self.decrease_button, self.size_spin, self.increase_button):
            widget.setMinimumHeight(36)
            row.addWidget(widget)
        layout.addLayout(row)
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(EDGE_PANEL_MIN_TILE, EDGE_PANEL_MAX_TILE)
        self.size_slider.setMinimumHeight(32)
        layout.addWidget(self.size_slider)
        presets = QtWidgets.QHBoxLayout()
        for label, size in (("작게", 48), ("기본", 68), ("중간", 96), ("크게", 128)):
            button = QtWidgets.QPushButton(f"{label} {size}")
            button.setMinimumHeight(34)
            button.clicked.connect(lambda checked=False, value=size: self.size_spin.setValue(value))
            presets.addWidget(button)
        layout.addLayout(presets)
        close = QtWidgets.QPushButton("닫기 · 자동 저장")
        close.setMinimumHeight(36)
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.size_spin.setValue(tile_side)
        self.size_slider.setValue(self.size_spin.value())
        self.size_spin.valueChanged.connect(self._size_changed)
        self.size_slider.valueChanged.connect(self.size_spin.setValue)
        self.decrease_button.clicked.connect(lambda: self.size_spin.setValue(self.size_spin.value() - 8))
        self.increase_button.clicked.connect(lambda: self.size_spin.setValue(self.size_spin.value() + 8))
        self._update_limits(self.size_spin.value())

    def _update_limits(self, size: int) -> None:
        self.decrease_button.setEnabled(size > EDGE_PANEL_MIN_TILE)
        self.increase_button.setEnabled(size < EDGE_PANEL_MAX_TILE)

    def _size_changed(self, size: int) -> None:
        with QtCore.QSignalBlocker(self.size_slider):
            self.size_slider.setValue(size)
        self._update_limits(size)
        self.size_changed.emit(size)


def nearest_screen_edge(point: QtCore.QPoint, available: QtCore.QRect,
                        preferred: str = "") -> str:
    distances = {
        "top": abs(point.y() - available.top()),
        "right": abs(point.x() - available.right()),
        "bottom": abs(point.y() - available.bottom()),
        "left": abs(point.x() - available.left()),
    }
    return min(distances, key=lambda edge: (distances[edge], edge != preferred))


def draw_edge_direction(painter: QtGui.QPainter, rect: QtCore.QRectF,
                        edge: str, selected: bool = False) -> None:
    """One line-icon style for all four compass directions, independent of emoji fonts."""
    painter.save()
    painter.translate(rect.center())
    scale = min(rect.width(), rect.height()) / 32.0
    painter.scale(scale, scale)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.setPen(QtGui.QPen(QtGui.QColor("#94A3B8"), 1.3))
    painter.drawRoundedRect(QtCore.QRectF(-12, -12, 24, 24), 4, 4)
    painter.rotate({"left": 0, "top": 90, "right": 180, "bottom": 270}[edge])
    painter.setPen(QtGui.QPen(QtGui.QColor("#0284C7" if selected else "#475569"),
                            2.7, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    painter.drawLine(QtCore.QPointF(-12, -8), QtCore.QPointF(-12, 8))
    painter.setPen(QtGui.QPen(QtGui.QColor("#172033"), 1.8,
                            QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
    painter.drawLine(QtCore.QPointF(6, 0), QtCore.QPointF(-5, 0))
    painter.drawPolyline(QtGui.QPolygonF([
        QtCore.QPointF(-1, -4), QtCore.QPointF(-5, 0), QtCore.QPointF(-1, 4)]))
    painter.restore()


@dataclass
class EdgePanelLayout:
    edge: str
    rows: int
    cols: int
    tile_side: int
    geometry: QtCore.QRect
    content_size: QtCore.QSize

    def cell(self, index: int) -> tuple[int, int]:
        if self.edge in {"left", "right"}:
            row, col = index % self.rows, index // self.rows
            if self.edge == "right":
                col = self.cols - 1 - col
        else:
            row, col = index // self.cols, index % self.cols
            if self.edge == "bottom":
                row = self.rows - 1 - row
        return row, col


def edge_panel_layout(edge: str, count: int, available: QtCore.QRect,
                      tile_side: float, gap: int, padding: int = 18,
                      axis_span: int | None = None) -> EdgePanelLayout:
    """Keep tiles square; wrap inward only after the screen's long axis fills."""
    if edge not in EDGE_PANEL_SIDES or not available.isValid():
        raise ValueError("올바른 화면 가장자리와 모니터 영역이 필요합니다.")
    count = max(1, int(count))
    side, gap = max(48, round(tile_side)), max(0, int(gap))
    if edge in {"left", "right"}:
        capacity = max(1, (available.height() - padding - EDGE_PANEL_HEADER + gap) // (side + gap))
        rows = min(count, capacity) if axis_span is None else max(1, min(axis_span, capacity))
        cols = math.ceil(count / rows)
    else:
        capacity = max(1, (available.width() - padding + gap) // (side + gap))
        cols = min(count, capacity) if axis_span is None else max(1, min(axis_span, capacity))
        rows = math.ceil(count / cols)
    content = QtCore.QSize(cols * side + (cols - 1) * gap,
                          rows * side + (rows - 1) * gap)
    width = min(available.width(), content.width() + padding)
    height = min(available.height(), content.height() + padding + EDGE_PANEL_HEADER)
    x = available.x() + (available.width() - width) // 2
    y = available.y() + (available.height() - height) // 2
    if edge == "left":
        x = available.left()
    elif edge == "right":
        x = available.right() - width + 1
    elif edge == "top":
        y = available.top()
    else:
        y = available.bottom() - height + 1
    return EdgePanelLayout(edge, rows, cols, side, QtCore.QRect(x, y, width, height), content)
