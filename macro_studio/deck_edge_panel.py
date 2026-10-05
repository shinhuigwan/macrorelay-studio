"""Screen-edge layout for the experimental QuickSlot panel."""

from dataclasses import dataclass
import math

from PySide6 import QtCore, QtGui


EDGE_PANEL_SIDES = {"left", "right", "top", "bottom"}
EDGE_PANEL_HEADER = 28


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
                      tile_side: float, gap: int, padding: int = 18) -> EdgePanelLayout:
    """Keep tiles square; wrap inward only after the screen's long axis fills."""
    if edge not in EDGE_PANEL_SIDES or not available.isValid():
        raise ValueError("올바른 화면 가장자리와 모니터 영역이 필요합니다.")
    count = max(1, int(count))
    side, gap = max(48, round(tile_side)), max(0, int(gap))
    if edge in {"left", "right"}:
        capacity = max(1, (available.height() - padding - EDGE_PANEL_HEADER + gap) // (side + gap))
        rows = min(count, capacity)
        cols = math.ceil(count / rows)
    else:
        capacity = max(1, (available.width() - padding + gap) // (side + gap))
        cols = min(count, capacity)
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
