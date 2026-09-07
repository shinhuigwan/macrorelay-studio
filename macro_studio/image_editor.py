from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import COLORS
from .widgets import Card, WheelSafeSpinBox, primary_button


def virtual_desktop_geometry() -> QtCore.QRect:
    """Return the logical-pixel rectangle spanning every attached monitor."""
    geometry = QtCore.QRect()
    for screen in QtGui.QGuiApplication.screens():
        geometry = geometry.united(screen.geometry())
    return geometry


def capture_virtual_desktop() -> tuple[QtGui.QPixmap, QtCore.QRect]:
    """Capture all monitors into one DPR-neutral pixmap, including negative origins."""
    geometry = virtual_desktop_geometry()
    if not geometry.isValid():
        return QtGui.QPixmap(), QtCore.QRect()
    image = QtGui.QImage(geometry.size(), QtGui.QImage.Format_RGB32)
    image.fill(QtCore.Qt.black)
    painter = QtGui.QPainter(image)
    for screen in QtGui.QGuiApplication.screens():
        screen_geometry = screen.geometry()
        pixmap = screen.grabWindow(0)
        target = QtCore.QRect(screen_geometry.topLeft() - geometry.topLeft(), screen_geometry.size())
        painter.drawPixmap(target, pixmap, pixmap.rect())
    painter.end()
    return QtGui.QPixmap.fromImage(image), geometry


class SelectionRubberBand(QtWidgets.QRubberBand):
    """Rubber band with visible move/resize affordances."""

    def __init__(self, parent=None) -> None:
        super().__init__(QtWidgets.QRubberBand.Rectangle, parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self.isVisible() or self.width() < 10 or self.height() < 10:
            return
        try:
            super().paintEvent(event)
        except Exception:
            return
        painter = QtGui.QPainter()
        if not painter.begin(self):
            return
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(QtGui.QColor("#D9FFFF"), 1))
            painter.setBrush(QtGui.QColor("#19D7D0"))
            rect = self.rect().adjusted(1, 1, -2, -2)
            if not rect.isValid():
                return
            points = (
                rect.topLeft(),
                QtCore.QPoint(rect.center().x(), rect.top()),
                rect.topRight(),
                QtCore.QPoint(rect.left(), rect.center().y()),
                QtCore.QPoint(rect.right(), rect.center().y()),
                rect.bottomLeft(),
                QtCore.QPoint(rect.center().x(), rect.bottom()),
                rect.bottomRight(),
            )
            for point in points:
                painter.drawRect(QtCore.QRect(point.x() - 4, point.y() - 4, 9, 9))
        except Exception:
            pass
        finally:
            if painter.isActive():
                painter.end()


class PrecisionImageView(QtWidgets.QLabel):
    """Image label that paints a scalable precision-brush cursor."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.brush_enabled = False
        self.brush_point = QtCore.QPointF()
        self.brush_radius = 0.0

    def set_brush_preview(self, enabled: bool, point: QtCore.QPointF, radius: float) -> None:
        self.brush_enabled = enabled
        self.brush_point = QtCore.QPointF(point)
        self.brush_radius = max(1.0, float(radius))
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.brush_enabled:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 3.0))
        painter.setBrush(QtGui.QColor(16, 22, 32, 35))
        painter.drawEllipse(self.brush_point, self.brush_radius, self.brush_radius)
        painter.setPen(QtGui.QPen(QtGui.QColor("#19D7D0"), 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(self.brush_point, self.brush_radius, self.brush_radius)
        painter.drawLine(
            QtCore.QPointF(self.brush_point.x() - 5, self.brush_point.y()),
            QtCore.QPointF(self.brush_point.x() + 5, self.brush_point.y()),
        )
        painter.drawLine(
            QtCore.QPointF(self.brush_point.x(), self.brush_point.y() - 5),
            QtCore.QPointF(self.brush_point.x(), self.brush_point.y() + 5),
        )


class ScreenCaptureDialog(QtWidgets.QDialog):
    """Full-screen region picker backed by a captured screen pixmap."""

    def __init__(
        self,
        pixmap: QtGui.QPixmap,
        geometry: QtCore.QRect,
        parent=None,
        *,
        accept_on_release: bool = False,
        hint_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = pixmap
        self._accept_on_release = bool(accept_on_release)
        self._origin: QtCore.QPoint | None = None
        self._selection = QtCore.QRect()
        self._drag_mode = ""
        self._drag_start = QtCore.QPoint()
        self._drag_rect = QtCore.QRect()
        self._closing = False
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setGeometry(geometry)
        self.setCursor(QtCore.Qt.CrossCursor)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = QtWidgets.QLabel()
        self.canvas.setPixmap(pixmap.scaled(geometry.size(), QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation))
        self.canvas.setFixedSize(geometry.size())
        self.canvas.setMouseTracking(True)
        self.canvas.installEventFilter(self)
        layout.addWidget(self.canvas)
        self.rubber = SelectionRubberBand(self.canvas)
        default_hint = (
            "드래그를 놓으면 바로 저장 · Esc 취소"
            if self._accept_on_release
            else "드래그 지정 · 내부 드래그 이동 · 가장자리/모서리 크기 조절 · Enter 저장"
        )
        self.hint = QtWidgets.QLabel(hint_text or default_hint, self)
        self.hint.setStyleSheet(
            "background:rgba(12,14,20,220); color:white; padding:10px 16px; "
            "border:1px solid #59637A; border-radius:8px; font-weight:700;"
        )
        self.hint.adjustSize()
        self.hint.move(24, 24)

        self._hint_text = hint_text or default_hint
        self.cursor_hint = QtWidgets.QLabel(self)
        self.cursor_hint.setStyleSheet(
            "background: rgba(14, 18, 28, 230); color: #E2E8F0; padding: 6px 12px; "
            "border: 1.5px solid #4D9FFF; border-radius: 6px; font-size: 9.5pt; font-weight: 700;"
        )
        self.cursor_hint.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.cursor_hint.setText("⬚ 검색할 영역을 마우스로 드래그하세요\n(Enter: 확정, Esc: 취소)")
        self.cursor_hint.adjustSize()
        self.cursor_hint.show()

    @staticmethod
    def _hit_test(rect: QtCore.QRect, point: QtCore.QPoint, margin: int = 8) -> str:
        if not rect.isValid() or not rect.adjusted(-margin, -margin, margin, margin).contains(point):
            return ""
        near_left = abs(point.x() - rect.left()) <= margin
        near_right = abs(point.x() - rect.right()) <= margin
        near_top = abs(point.y() - rect.top()) <= margin
        near_bottom = abs(point.y() - rect.bottom()) <= margin
        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        if near_top:
            return "n"
        if near_bottom:
            return "s"
        if near_left:
            return "w"
        if near_right:
            return "e"
        return "move" if rect.contains(point) else ""

    @staticmethod
    def _cursor_for_mode(mode: str) -> QtCore.Qt.CursorShape:
        if mode in {"nw", "se"}:
            return QtCore.Qt.SizeFDiagCursor
        if mode in {"ne", "sw"}:
            return QtCore.Qt.SizeBDiagCursor
        if mode in {"w", "e"}:
            return QtCore.Qt.SizeHorCursor
        if mode in {"n", "s"}:
            return QtCore.Qt.SizeVerCursor
        if mode == "move":
            return QtCore.Qt.SizeAllCursor
        return QtCore.Qt.CrossCursor

    @staticmethod
    def _drag_selection(
        mode: str,
        start_rect: QtCore.QRect,
        start: QtCore.QPoint,
        current: QtCore.QPoint,
        bounds: QtCore.QRect,
        minimum: int = 12,
    ) -> QtCore.QRect:
        if mode == "new":
            return QtCore.QRect(start, current).normalized().intersected(bounds)
        dx, dy = current.x() - start.x(), current.y() - start.y()
        if mode == "move":
            moved = QtCore.QRect(start_rect).translated(dx, dy)
            if moved.left() < bounds.left():
                moved.moveLeft(bounds.left())
            if moved.top() < bounds.top():
                moved.moveTop(bounds.top())
            if moved.right() > bounds.right():
                moved.moveRight(bounds.right())
            if moved.bottom() > bounds.bottom():
                moved.moveBottom(bounds.bottom())
            return moved
        left, top, right, bottom = start_rect.left(), start_rect.top(), start_rect.right(), start_rect.bottom()
        if "w" in mode:
            left = max(bounds.left(), min(right - minimum + 1, left + dx))
        if "e" in mode:
            right = min(bounds.right(), max(left + minimum - 1, right + dx))
        if "n" in mode:
            top = max(bounds.top(), min(bottom - minimum + 1, top + dy))
        if "s" in mode:
            bottom = min(bounds.bottom(), max(top + minimum - 1, bottom + dy))
        return QtCore.QRect(QtCore.QPoint(left, top), QtCore.QPoint(right, bottom))

    def _show_selection(self) -> None:
        self.rubber.setGeometry(self._selection.normalized())
        self.rubber.show()

    def eventFilter(self, obj, event):
        if obj is self.canvas:
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                point = event.position().toPoint()
                self._drag_mode = self._hit_test(self._selection, point) or "new"
                self._drag_start = point
                self._drag_rect = QtCore.QRect(self._selection)
                self._origin = point
                if self._drag_mode == "new":
                    self._selection = QtCore.QRect(point, QtCore.QSize())
                self._show_selection()
                self.canvas.setCursor(self._cursor_for_mode(self._drag_mode))
                return True
            if event.type() == QtCore.QEvent.MouseMove:
                pos = event.position().toPoint()
                hx = min(pos.x() + 16, self.width() - self.cursor_hint.width() - 10)
                hy = min(pos.y() + 16, self.height() - self.cursor_hint.height() - 10)
                self.cursor_hint.move(max(10, hx), max(10, hy))
                self.cursor_hint.raise_()
                if self._origin is not None:
                    self._selection = self._drag_selection(
                        self._drag_mode,
                        self._drag_rect,
                        self._drag_start,
                        pos,
                        self.canvas.rect(),
                    )
                    self._show_selection()
                    w, h = self._selection.width(), self._selection.height()
                    self.cursor_hint.setText(f"⬚ 영역: {w} × {h} px\n(마우스 놓기 또는 Enter: 확정)")
                    self.cursor_hint.adjustSize()
                    return True
                else:
                    mode = self._hit_test(self._selection, pos)
                    self.canvas.setCursor(self._cursor_for_mode(mode))
                    if self._selection.isValid() and self._selection.width() >= 4:
                        w, h = self._selection.width(), self._selection.height()
                        self.cursor_hint.setText(f"⬚ 지정된 영역: {w} × {h} px\n(Enter: 확정, 드래그: 재지정)")
                    else:
                        self.cursor_hint.setText(self._hint_text or "⬚ 검색할 영역을 마우스로 드래그하세요\n(Enter: 확정, Esc: 취소)")
                    self.cursor_hint.adjustSize()
                    return False
            if event.type() == QtCore.QEvent.MouseButtonRelease and self._origin is not None:
                self._selection = self.rubber.geometry().normalized().intersected(self.canvas.rect())
                self._origin = None
                self._drag_mode = ""
                mode = self._hit_test(self._selection, event.position().toPoint())
                self.canvas.setCursor(self._cursor_for_mode(mode))
                if self._accept_on_release and self._selection.width() >= 4 and self._selection.height() >= 4:
                    if not getattr(self, "_closing", False):
                        QtCore.QTimer.singleShot(0, self.accept)
                return True
            if event.type() == QtCore.QEvent.MouseButtonDblClick and self._selection.width() >= 4:
                self.accept()
                return True
            if event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Escape:
                    event.accept()
                    self._cancel_and_reject()
                    return True
                if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    if self._selection.width() >= 4 and self._selection.height() >= 4:
                        event.accept()
                        self.accept()
                        return True
        return super().eventFilter(obj, event)

    def _cancel_and_reject(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._origin = None
        self._selection = QtCore.QRect()
        try:
            if hasattr(self, "rubber") and self.rubber is not None:
                self.rubber.hide()
        except Exception:
            pass
        self.reject()

    def accept(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        super().accept()

    def reject(self) -> None:
        self._closing = True
        self._origin = None
        self._selection = QtCore.QRect()
        try:
            if hasattr(self, "rubber") and self.rubber is not None:
                self.rubber.hide()
        except Exception:
            pass
        super().reject()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self._selection.width() >= 4 and self._selection.height() >= 4:
                event.accept()
                self.accept()
                return
        if event.key() == QtCore.Qt.Key_Escape:
            event.accept()
            self._cancel_and_reject()
            return
        super().keyPressEvent(event)

    def captured_image(self) -> QtGui.QImage:
        if self._selection.width() < 4 or self._selection.height() < 4:
            return QtGui.QImage()
        sx = self._source.width() / max(1, self.canvas.width())
        sy = self._source.height() / max(1, self.canvas.height())
        rect = QtCore.QRect(
            round(self._selection.x() * sx),
            round(self._selection.y() * sy),
            round(self._selection.width() * sx),
            round(self._selection.height() * sy),
        ).intersected(self._source.rect())
        return self._source.toImage().copy(rect)

    def selected_screen_rect(self) -> QtCore.QRect:
        """선택한 범위를 현재 화면의 전역 좌표로 반환합니다."""
        if self._selection.width() < 4 or self._selection.height() < 4:
            return QtCore.QRect()
        return self._selection.translated(self.geometry().topLeft())

    @property
    def selected_rect(self) -> QtCore.QRect:
        """Property alias for selected_screen_rect."""
        return self.selected_screen_rect()


def select_region_on_snapshot(
    snapshot_pixmap: QtGui.QPixmap,
    parent=None,
    hint_text: str | None = None,
) -> list[int] | None:
    """Open snapshot in a focused overlay allowing the user to drag an ROI directly on the snapshot frame.
    Returns [left, top, right, bottom] relative to the snapshot (0, 0) or None if cancelled.
    """
    if snapshot_pixmap.isNull() or snapshot_pixmap.width() < 4 or snapshot_pixmap.height() < 4:
        return None
    geometry = virtual_desktop_geometry()
    if not geometry.isValid():
        geometry = QtCore.QRect(0, 0, 1920, 1080)

    # Create dark dimmed overlay matching virtual desktop
    overlay = QtGui.QPixmap(geometry.size())
    overlay.fill(QtGui.QColor(12, 16, 24, 235))

    sw = snapshot_pixmap.width()
    sh = snapshot_pixmap.height()

    # Center the snapshot on the screen (or primary monitor)
    primary_screen = QtGui.QGuiApplication.primaryScreen()
    if primary_screen:
        p_geo = primary_screen.geometry()
        cx = (p_geo.left() - geometry.left()) + max(0, (p_geo.width() - sw) // 2)
        cy = (p_geo.top() - geometry.top()) + max(0, (p_geo.height() - sh) // 2)
    else:
        cx = max(0, (geometry.width() - sw) // 2)
        cy = max(0, (geometry.height() - sh) // 2)

    snap_target = QtCore.QRect(cx, cy, sw, sh)

    painter = QtGui.QPainter(overlay)
    painter.drawPixmap(cx, cy, snapshot_pixmap)

    # Draw border and title banner
    painter.setPen(QtGui.QPen(QtGui.QColor("#4D9FFF"), 2))
    painter.drawRect(cx - 1, cy - 1, sw + 2, sh + 2)

    painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0")))
    font = QtGui.QFont("Segoe UI", 10)
    font.setBold(True)
    painter.setFont(font)
    banner_text = f"📸 녹화 당시 화면 (스냅샷: {sw}×{sh} px) · 틀 안쪽에서 검색할 영역을 마우스로 드래그하세요"
    painter.drawText(cx, max(18, cy - 8), banner_text)
    painter.end()

    hint = hint_text or "📸 [녹화 당시 화면] 파란 틀 안쪽에서 검색할 영역을 드래그하세요 (Enter: 확정, Esc: 취소)"
    picker = ScreenCaptureDialog(overlay, geometry, parent=parent, hint_text=hint)
    try:
        if picker.exec() != QtWidgets.QDialog.Accepted:
            return None
        selected = picker.selected_screen_rect()
        local_sel = selected.translated(-geometry.topLeft())
        intersected = local_sel.intersected(snap_target)
        if not intersected.isValid() or intersected.width() < 4 or intersected.height() < 4:
            return None
        rel_left = intersected.left() - cx
        rel_top = intersected.top() - cy
        rel_right = intersected.right() - cx
        rel_bottom = intersected.bottom() - cy
        return [max(0, rel_left), max(0, rel_top), min(sw, rel_right), min(sh, rel_bottom)]
    finally:
        try:
            picker.deleteLater()
        except Exception:
            pass


class ImageEditorDialog(QtWidgets.QDialog):
    saved = QtCore.Signal(str)

    def __init__(self, path: Path, alias: str, history_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.path = path.resolve()
        self.alias = alias
        self.history_root = history_root
        loaded = QtGui.QImage(str(self.path))
        self.image = loaded.convertToFormat(QtGui.QImage.Format_ARGB32)
        self.history: list[QtGui.QImage] = [self.image.copy()]
        self.history_index = 0
        self.zoom = 1.0
        self.fit_to_view = True
        self.selection = QtCore.QRect()
        self.origin: QtCore.QPoint | None = None
        self.erase_mode = False
        self.erasing = False
        self.pick_mode = False
        self.picked_color: QtGui.QColor | None = None
        self.picked_point: QtCore.QPoint | None = None
        self.picked_colors: list[QtGui.QColor] = []
        self.picked_points: list[QtCore.QPoint] = []
        self.brush_point = QtCore.QPoint(max(0, self.image.width() // 2), max(0, self.image.height() // 2))
        self.setWindowTitle(f"이미지 편집 · {alias}")
        self.setMinimumSize(940, 700)
        self.resize(1180, 820)
        self._build_ui()
        self._update_view()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(self.alias)
        title.setStyleSheet("font-size:16pt; font-weight:800;")
        self.info = QtWidgets.QLabel()
        self.info.setObjectName("Muted")
        top.addWidget(title)
        top.addWidget(self.info)
        top.addStretch(1)
        self.undo_button = QtWidgets.QPushButton("↶ 실행 취소")
        self.redo_button = QtWidgets.QPushButton("↷ 다시 실행")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        top.addWidget(self.undo_button)
        top.addWidget(self.redo_button)
        root.addLayout(top)

        tools = Card()
        tool_layout = QtWidgets.QVBoxLayout(tools)
        tool_layout.setContentsMargins(12, 10, 12, 10)
        first = QtWidgets.QHBoxLayout()
        for label, callback in (
            ("✨ 자동 누끼", self.auto_cutout),
            ("자르기", self.crop),
            ("↶ 왼쪽 회전", lambda: self.rotate(-90)),
            ("↷ 오른쪽 회전", lambda: self.rotate(90)),
            ("↔ 좌우 반전", lambda: self.flip(True)),
            ("↕ 상하 반전", lambda: self.flip(False)),
            ("흑백", self.grayscale),
            ("색상 반전", self.invert),
            ("이진화", self.threshold),
            ("밝게", lambda: self.brightness(18)),
            ("어둡게", lambda: self.brightness(-18)),
            ("대비 +", lambda: self.contrast(18)),
            ("대비 -", lambda: self.contrast(-18)),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            first.addWidget(button)
        first.addStretch(1)
        tool_layout.addLayout(first)

        second = QtWidgets.QHBoxLayout()
        self.eraser = QtWidgets.QToolButton()
        self.eraser.setText("지우개")
        self.eraser.setCheckable(True)
        self.eraser.toggled.connect(self._toggle_eraser)
        settings = QtCore.QSettings("MacroRelay", "Studio")
        saved_eraser = int(settings.value("image_editor/eraser_size", 20) or 20)
        self.eraser_size = WheelSafeSpinBox()
        self.eraser_size.setRange(1, 200)
        self.eraser_size.setValue(saved_eraser)
        self.eraser_size.setFocusPolicy(QtCore.Qt.NoFocus)
        self.eraser_size.valueChanged.connect(self._update_brush_preview)
        self.eraser_size.valueChanged.connect(lambda val: QtCore.QSettings("MacroRelay", "Studio").setValue("image_editor/eraser_size", int(val)))
        clear_selection = QtWidgets.QPushButton("선택 영역 투명화")
        clear_selection.clicked.connect(self.clear_selection)
        self.pick_button = QtWidgets.QPushButton("🎨 색상 찍기 (다중 추가)")
        self.pick_button.clicked.connect(self.start_pick_color)
        remove_color = QtWidgets.QPushButton("찍은 색상 제거")
        remove_color.clicked.connect(self.remove_color)
        remove_connected = QtWidgets.QPushButton("연결된 유사색만 제거")
        remove_connected.setToolTip("찍은 지점에서 이어진 비슷한 색만 투명하게 만들어 전경의 같은 색은 보호합니다.")
        remove_connected.clicked.connect(self.remove_connected_color)
        keep_only_color = QtWidgets.QPushButton("✨ 선택 색상만 남기기 (반전)")
        keep_only_color.setToolTip("찍은 색상 범위만 남기고, 나머지 모든 배경/색상을 투명화합니다.")
        keep_only_color.setStyleSheet("background: #1F384C; color: #70C5FF; font-weight: bold;")
        keep_only_color.clicked.connect(self.keep_only_color)
        clear_outside = QtWidgets.QPushButton("선택 밖 투명화")
        clear_outside.setToolTip("드래그한 전경 영역만 남기고 바깥을 투명하게 만듭니다.")
        clear_outside.clicked.connect(self.clear_outside_selection)
        self.tolerance = WheelSafeSpinBox()
        self.tolerance.setRange(0, 100)
        self.tolerance.setSuffix("%")
        saved_tolerance = int(settings.value("image_editor/tolerance_pct", 8) or 8)
        self.tolerance.setValue(saved_tolerance)
        self.tolerance.valueChanged.connect(lambda val: QtCore.QSettings("MacroRelay", "Studio").setValue("image_editor/tolerance_pct", int(val)))
        second.addWidget(self.eraser)
        second.addWidget(QtWidgets.QLabel("크기"))
        second.addWidget(self.eraser_size)
        second.addWidget(clear_selection)
        second.addWidget(clear_outside)
        second.addSpacing(12)
        second.addWidget(self.pick_button)
        second.addWidget(remove_color)
        second.addWidget(remove_connected)
        second.addWidget(keep_only_color)
        second.addWidget(QtWidgets.QLabel("유사도"))
        second.addWidget(self.tolerance)
        second.addStretch(1)
        tool_layout.addLayout(second)

        palette_row = QtWidgets.QHBoxLayout()
        palette_row.addWidget(QtWidgets.QLabel("찍은 색상 목록:"))
        self.palette_container = QtWidgets.QWidget()
        self.palette_layout = QtWidgets.QHBoxLayout(self.palette_container)
        self.palette_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_layout.setSpacing(6)
        self.btn_clear_colors = QtWidgets.QPushButton("비우기")
        self.btn_clear_colors.setStyleSheet("padding: 3px 8px; font-size: 8.5pt;")
        self.btn_clear_colors.clicked.connect(self.clear_picked_colors)
        palette_row.addWidget(self.palette_container)
        palette_row.addWidget(self.btn_clear_colors)
        palette_row.addStretch(1)
        tool_layout.addLayout(palette_row)
        self._refresh_palette_ui()

        precision = QtWidgets.QHBoxLayout()
        precision_hint = QtWidgets.QLabel(
            "정밀 붓: 마우스 이동/드래그 · +/− 크기 · 방향키 1px · Shift+방향키 10px · 지정 키로 한 번 투명화"
        )
        precision_hint.setObjectName("Muted")
        self.stamp_key_edit = QtWidgets.QKeySequenceEdit()
        settings = QtCore.QSettings("MacroRelay", "Studio")
        saved_stamp_key = str(settings.value("image_editor/erase_stamp_shortcut", "Space") or "Space")
        self.stamp_key_edit.setKeySequence(QtGui.QKeySequence(saved_stamp_key))
        self.stamp_key_edit.setMaximumWidth(130)
        self.stamp_key_edit.editingFinished.connect(self._update_stamp_shortcut)
        precision.addWidget(precision_hint, 1)
        precision.addWidget(QtWidgets.QLabel("투명화 키"))
        precision.addWidget(self.stamp_key_edit)
        tool_layout.addLayout(precision)

        colour_shortcuts = QtWidgets.QHBoxLayout()
        colour_shortcuts.addWidget(QtWidgets.QLabel("색상 도구 단축키"))
        settings = QtCore.QSettings("MacroRelay", "Studio")
        self.pick_key_edit = QtWidgets.QKeySequenceEdit(
            QtGui.QKeySequence(str(settings.value("image_editor/pick_colour_shortcut", "P") or "P"))
        )
        self.remove_colour_key_edit = QtWidgets.QKeySequenceEdit(
            QtGui.QKeySequence(str(settings.value("image_editor/remove_colour_shortcut", "R") or "R"))
        )
        self.remove_connected_key_edit = QtWidgets.QKeySequenceEdit(
            QtGui.QKeySequence(str(settings.value("image_editor/remove_connected_shortcut", "Shift+R") or "Shift+R"))
        )
        self.keep_only_colour_key_edit = QtWidgets.QKeySequenceEdit(
            QtGui.QKeySequence(str(settings.value("image_editor/keep_only_colour_shortcut", "Ctrl+R") or "Ctrl+R"))
        )
        for editor in (self.pick_key_edit, self.remove_colour_key_edit, self.remove_connected_key_edit, self.keep_only_colour_key_edit):
            editor.setMaximumWidth(115)
            editor.editingFinished.connect(self._update_colour_shortcuts)
        colour_shortcuts.addWidget(QtWidgets.QLabel("색상 찍기"))
        colour_shortcuts.addWidget(self.pick_key_edit)
        colour_shortcuts.addWidget(QtWidgets.QLabel("유사색 제거"))
        colour_shortcuts.addWidget(self.remove_colour_key_edit)
        colour_shortcuts.addWidget(QtWidgets.QLabel("연결 유사색 제거"))
        colour_shortcuts.addWidget(self.remove_connected_key_edit)
        colour_shortcuts.addWidget(QtWidgets.QLabel("선택 색상만 남기기 (반전)"))
        colour_shortcuts.addWidget(self.keep_only_colour_key_edit)
        colour_shortcuts.addStretch(1)
        tool_layout.addLayout(colour_shortcuts)
        root.addWidget(tools)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(QtCore.Qt.AlignCenter)
        self.scroll.setStyleSheet("background:#090B10; border:1px solid #303647; border-radius:10px;")
        self.view = PrecisionImageView(alignment=QtCore.Qt.AlignCenter)
        self.view.setStyleSheet("background:#0D0F15;")
        self.view.setMouseTracking(True)
        self.view.installEventFilter(self)
        self.scroll.setWidget(self.view)
        self.rubber = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self.view)
        root.addWidget(self.scroll, 1)

        bottom = QtWidgets.QHBoxLayout()
        zoom_out = QtWidgets.QPushButton("−")
        zoom_in = QtWidgets.QPushButton("＋")
        fit = QtWidgets.QPushButton("화면 맞춤")
        actual = QtWidgets.QPushButton("100%")
        zoom_out.clicked.connect(lambda: self.set_zoom(self.zoom - 0.1))
        zoom_in.clicked.connect(lambda: self.set_zoom(self.zoom + 0.1))
        fit.clicked.connect(self.fit)
        actual.clicked.connect(lambda: self.set_zoom(1.0))
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        save_copy = QtWidgets.QPushButton("복사본 저장")
        save_copy.clicked.connect(self.save_copy)
        save = primary_button("원본에 저장")
        save.clicked.connect(self.save)
        bottom.addWidget(zoom_out)
        bottom.addWidget(zoom_in)
        bottom.addWidget(actual)
        bottom.addWidget(fit)
        bottom.addWidget(self.zoom_label)
        bottom.addStretch(1)
        bottom.addWidget(save_copy)
        bottom.addWidget(save)
        root.addLayout(bottom)

        self.undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Undo), self)
        self.redo_shortcut = QtGui.QShortcut(QtGui.QKeySequence(QtGui.QKeySequence.Redo), self)
        self.undo_shortcut.activated.connect(self.undo)
        self.redo_shortcut.activated.connect(self.redo)
        self.erase_stamp_shortcut = QtGui.QShortcut(self.stamp_key_edit.keySequence(), self)
        self.erase_stamp_shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.erase_stamp_shortcut.activated.connect(self._stamp_brush)
        self.pick_colour_shortcut = QtGui.QShortcut(self.pick_key_edit.keySequence(), self)
        self.remove_colour_shortcut = QtGui.QShortcut(self.remove_colour_key_edit.keySequence(), self)
        self.remove_connected_shortcut = QtGui.QShortcut(self.remove_connected_key_edit.keySequence(), self)
        self.keep_only_colour_shortcut = QtGui.QShortcut(self.keep_only_colour_key_edit.keySequence(), self)
        for shortcut in (
            self.pick_colour_shortcut,
            self.remove_colour_shortcut,
            self.remove_connected_shortcut,
            self.keep_only_colour_shortcut,
        ):
            shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.pick_colour_shortcut.activated.connect(self.start_pick_color)
        self.remove_colour_shortcut.activated.connect(self.remove_color)
        self.remove_connected_shortcut.activated.connect(self.remove_connected_color)
        self.keep_only_colour_shortcut.activated.connect(self.keep_only_color)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.fit_to_view and hasattr(self, "scroll"):
            self._update_view()

    def eventFilter(self, obj, event):
        if obj is self.view:
            point = event.position().toPoint() if hasattr(event, "position") else QtCore.QPoint()
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                if self.pick_mode:
                    self._pick(point)
                    return True
                if self.erase_mode:
                    self.erasing = True
                    self._erase(point)
                    return True
                self.origin = point
                self.rubber.setGeometry(QtCore.QRect(point, QtCore.QSize()))
                self.rubber.show()
                return True
            if event.type() == QtCore.QEvent.MouseMove:
                if self.erase_mode:
                    mapped = self._image_point(point)
                    if mapped is not None:
                        self.brush_point = mapped
                        self._update_brush_preview()
                if self.erasing and event.buttons() & QtCore.Qt.LeftButton:
                    self._erase(point)
                    return True
                if self.origin is not None:
                    self.selection = QtCore.QRect(self.origin, point).normalized()
                    self.rubber.setGeometry(self.selection)
                    return True
            if event.type() == QtCore.QEvent.MouseButtonRelease:
                if self.erasing:
                    self.erasing = False
                    self._push_history()
                    return True
                if self.origin is not None:
                    self.selection = self.rubber.geometry().normalized()
                    self.origin = None
                    return True
            if event.type() == QtCore.QEvent.Wheel and event.modifiers() & QtCore.Qt.ControlModifier:
                self.set_zoom(self.zoom + (0.1 if event.angleDelta().y() > 0 else -0.1))
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self.erase_mode and event.key() in (
            QtCore.Qt.Key_Left,
            QtCore.Qt.Key_Right,
            QtCore.Qt.Key_Up,
            QtCore.Qt.Key_Down,
        ):
            amount = 10 if event.modifiers() & QtCore.Qt.ShiftModifier else 1
            dx = -amount if event.key() == QtCore.Qt.Key_Left else amount if event.key() == QtCore.Qt.Key_Right else 0
            dy = -amount if event.key() == QtCore.Qt.Key_Up else amount if event.key() == QtCore.Qt.Key_Down else 0
            self.brush_point = QtCore.QPoint(
                max(0, min(self.image.width() - 1, self.brush_point.x() + dx)),
                max(0, min(self.image.height() - 1, self.brush_point.y() + dy)),
            )
            self._update_brush_preview()
            event.accept()
            return
        if self.erase_mode and event.key() in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.eraser_size.setValue(min(self.eraser_size.maximum(), self.eraser_size.value() + 2))
            event.accept()
            return
        if self.erase_mode and event.key() in (QtCore.Qt.Key_Minus, QtCore.Qt.Key_Underscore):
            self.eraser_size.setValue(max(self.eraser_size.minimum(), self.eraser_size.value() - 2))
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_view(self) -> None:
        pixmap = QtGui.QPixmap.fromImage(self.image)
        if self.fit_to_view:
            target = self.scroll.viewport().size() - QtCore.QSize(24, 24)
            shown = pixmap.scaled(target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            self.zoom = min(shown.width() / max(1, pixmap.width()), shown.height() / max(1, pixmap.height()))
        else:
            shown = pixmap.scaled(
                max(1, round(pixmap.width() * self.zoom)),
                max(1, round(pixmap.height() * self.zoom)),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        self.view.setPixmap(shown)
        self.view.setFixedSize(shown.size())
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")
        self.info.setText(f"{self.image.width()} × {self.image.height()} px · {self.path.name}")
        self.undo_button.setEnabled(self.history_index > 0)
        self.redo_button.setEnabled(self.history_index < len(self.history) - 1)
        self._update_brush_preview()

    def set_zoom(self, value: float) -> None:
        self.fit_to_view = False
        self.zoom = max(0.1, min(8.0, value))
        self._update_view()

    def fit(self) -> None:
        self.fit_to_view = True
        self._update_view()

    def _image_point(self, point: QtCore.QPoint) -> QtCore.QPoint | None:
        pixmap = self.view.pixmap()
        if not pixmap or pixmap.isNull():
            return None
        x = int(point.x() * self.image.width() / max(1, pixmap.width()))
        y = int(point.y() * self.image.height() / max(1, pixmap.height()))
        if 0 <= x < self.image.width() and 0 <= y < self.image.height():
            return QtCore.QPoint(x, y)
        return None

    def _image_rect(self, rect: QtCore.QRect) -> QtCore.QRect:
        pixmap = self.view.pixmap()
        if not pixmap or pixmap.isNull():
            return QtCore.QRect()
        sx = self.image.width() / max(1, pixmap.width())
        sy = self.image.height() / max(1, pixmap.height())
        return QtCore.QRect(round(rect.x() * sx), round(rect.y() * sy), round(rect.width() * sx), round(rect.height() * sy)).intersected(self.image.rect())

    def _replace(self, image: QtGui.QImage, push: bool = True) -> None:
        self.image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
        if push:
            self._push_history()
        self._update_view()

    def _push_history(self) -> None:
        current = self.image.copy()
        if self.history and self.history[self.history_index] == current:
            return
        self.history = self.history[: self.history_index + 1]
        self.history.append(current)
        if len(self.history) > 30:
            self.history.pop(0)
        self.history_index = len(self.history) - 1
        self._update_view()

    def undo(self) -> None:
        if self.history_index <= 0:
            return
        self.history_index -= 1
        self.image = self.history[self.history_index].copy()
        self._update_view()

    def redo(self) -> None:
        if self.history_index >= len(self.history) - 1:
            return
        self.history_index += 1
        self.image = self.history[self.history_index].copy()
        self._update_view()

    def crop(self) -> None:
        rect = self._image_rect(self.selection)
        if rect.width() >= 4 and rect.height() >= 4:
            self._replace(self.image.copy(rect))
            self.selection = QtCore.QRect()
            self.rubber.hide()

    def auto_cutout(self) -> None:
        """Extract a selected or central foreground subject and preserve it as PNG alpha."""
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            QtWidgets.QMessageBox.warning(
                self,
                "자동 누끼",
                "자동 누끼에는 OpenCV 구성요소가 필요합니다. 설정의 구성요소 관리에서 OpenCV를 설치해 주세요.",
            )
            return
        payload = QtCore.QByteArray()
        buffer = QtCore.QBuffer(payload)
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.image.save(buffer, "PNG")
        buffer.close()
        encoded = np.frombuffer(bytes(payload), dtype=np.uint8)
        source = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if source is None or source.shape[0] < 8 or source.shape[1] < 8:
            QtWidgets.QMessageBox.warning(self, "자동 누끼", "이미지가 너무 작거나 읽을 수 없습니다.")
            return
        if source.ndim == 2:
            bgr = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
        elif source.shape[2] == 4:
            bgr = source[:, :, :3].copy()
        else:
            bgr = source[:, :, :3].copy()
        height, width = bgr.shape[:2]
        selected = self._image_rect(self.selection)
        mask = np.full((height, width), cv2.GC_BGD, dtype=np.uint8)
        background_model = np.zeros((1, 65), np.float64)
        foreground_model = np.zeros((1, 65), np.float64)
        try:
            if selected.width() >= 8 and selected.height() >= 8:
                left = max(1, selected.x())
                top = max(1, selected.y())
                selected_width = min(width - left - 1, selected.width())
                selected_height = min(height - top - 1, selected.height())
                if selected_width < 4 or selected_height < 4:
                    return
                mask[top : top + selected_height, left : left + selected_width] = cv2.GC_PR_FGD
                inset_x = max(1, selected_width // 8)
                inset_y = max(1, selected_height // 8)
                mask[
                    top + inset_y : top + selected_height - inset_y,
                    left + inset_x : left + selected_width - inset_x,
                ] = cv2.GC_FGD
                # A manually sampled background colour is a strong exclusion
                # hint.  It prevents GrabCut from keeping same-colour scenery
                # around the selected foreground subject.
                if self.picked_color is not None:
                    sampled = np.array(
                        [self.picked_color.blue(), self.picked_color.green(), self.picked_color.red()],
                        dtype=np.int16,
                    )
                    distance = np.max(np.abs(bgr.astype(np.int16) - sampled), axis=2)
                    mask[distance <= self._rgb_tolerance()] = cv2.GC_BGD
                cv2.grabCut(bgr, mask, None, background_model, foreground_model, 8, cv2.GC_INIT_WITH_MASK)
            else:
                # The common smart-recording case has one wanted icon or hand
                # around the middle of the capture.  Seed GrabCut from the
                # centre colour instead of declaring the entire inner canvas a
                # probable foreground; that prevents nearby text/background
                # from being merged into the subject.
                center_x, center_y = width // 2, height // 2
                radius = max(4, min(width, height) // 24)
                lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
                sample = lab[
                    max(0, center_y - radius) : min(height, center_y + radius + 1),
                    max(0, center_x - radius) : min(width, center_x + radius + 1),
                ]
                median = np.median(sample.reshape(-1, 3), axis=0)
                distance = np.linalg.norm(lab - median, axis=2)
                yy, xx = np.indices((height, width))
                central = (
                    (xx > width * 0.12)
                    & (xx < width * 0.88)
                    & (yy > height * 0.02)
                    & (yy < height * 0.98)
                )
                mask[central] = cv2.GC_PR_BGD
                mask[(distance < 58) & central] = cv2.GC_PR_FGD
                mask[(distance < 27) & central] = cv2.GC_FGD
                cv2.circle(mask, (center_x, center_y), radius, cv2.GC_FGD, -1)
                cv2.grabCut(bgr, mask, None, background_model, foreground_model, 8, cv2.GC_INIT_WITH_MASK)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "자동 누끼 실패", str(exc))
            return
        foreground = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(foreground, 8)
        if count > 1:
            center_x = max(0, min(width - 1, selected.center().x() if not selected.isNull() else width // 2))
            center_y = max(0, min(height - 1, selected.center().y() if not selected.isNull() else height // 2))
            center_label = int(labels[center_y, center_x])
            if center_label <= 0:
                center_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            foreground = np.where(labels == center_label, 255, 0).astype(np.uint8)
        kernel = np.ones((3, 3), np.uint8)
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, kernel, iterations=2)
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, kernel, iterations=1)
        ratio = float(np.count_nonzero(foreground)) / float(max(1, width * height))
        if ratio < 0.01 or ratio > 0.94:
            QtWidgets.QMessageBox.warning(
                self,
                "자동 누끼 확인",
                "전경과 배경을 안정적으로 분리하지 못했습니다. 먼저 손처럼 남길 대상을 드래그한 뒤 다시 실행해 주세요.",
            )
            return
        alpha = cv2.GaussianBlur(foreground, (3, 3), 0)
        result = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        result[:, :, 3] = alpha
        ok, png = cv2.imencode(".png", result)
        edited = QtGui.QImage.fromData(bytes(png)) if ok else QtGui.QImage()
        if edited.isNull():
            QtWidgets.QMessageBox.warning(self, "자동 누끼 실패", "투명 배경 이미지를 만들지 못했습니다.")
            return
        self._replace(edited.convertToFormat(QtGui.QImage.Format_ARGB32))
        self.selection = QtCore.QRect()
        self.rubber.hide()

    def rotate(self, degrees: int) -> None:
        self._replace(self.image.transformed(QtGui.QTransform().rotate(degrees), QtCore.Qt.SmoothTransformation))

    def flip(self, horizontal: bool) -> None:
        self._replace(self.image.mirrored(horizontal, not horizontal))

    def _map_pixels(self, operation) -> None:
        image = self.image.copy()
        for y in range(image.height()):
            for x in range(image.width()):
                image.setPixelColor(x, y, operation(image.pixelColor(x, y)))
        self._replace(image)

    def grayscale(self) -> None:
        self._map_pixels(lambda c: QtGui.QColor(round(c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114), round(c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114), round(c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114), c.alpha()))

    def invert(self) -> None:
        self._map_pixels(lambda c: QtGui.QColor(255 - c.red(), 255 - c.green(), 255 - c.blue(), c.alpha()))

    def threshold(self) -> None:
        def convert(c: QtGui.QColor) -> QtGui.QColor:
            value = 255 if c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114 >= 128 else 0
            return QtGui.QColor(value, value, value, c.alpha())
        self._map_pixels(convert)

    def brightness(self, delta: int) -> None:
        self._map_pixels(lambda c: QtGui.QColor(max(0, min(255, c.red() + delta)), max(0, min(255, c.green() + delta)), max(0, min(255, c.blue() + delta)), c.alpha()))

    def contrast(self, delta: int) -> None:
        factor = (259 * (delta + 255)) / (255 * (259 - delta))
        adjust = lambda value: max(0, min(255, round(factor * (value - 128) + 128)))
        self._map_pixels(lambda c: QtGui.QColor(adjust(c.red()), adjust(c.green()), adjust(c.blue()), c.alpha()))

    def _toggle_eraser(self, enabled: bool) -> None:
        self.erase_mode = enabled
        self.pick_mode = False
        self.view.setCursor(QtCore.Qt.BlankCursor if enabled else QtCore.Qt.ArrowCursor)
        self._update_brush_preview()

    def _update_stamp_shortcut(self) -> None:
        sequence = self.stamp_key_edit.keySequence()
        if sequence.isEmpty():
            sequence = QtGui.QKeySequence("Space")
            self.stamp_key_edit.setKeySequence(sequence)
        self.erase_stamp_shortcut.setKey(sequence)
        QtCore.QSettings("MacroRelay", "Studio").setValue(
            "image_editor/erase_stamp_shortcut", sequence.toString()
        )

    def _update_colour_shortcuts(self) -> None:
        defaults = (
            (self.pick_key_edit, self.pick_colour_shortcut, "image_editor/pick_colour_shortcut", "P"),
            (self.remove_colour_key_edit, self.remove_colour_shortcut, "image_editor/remove_colour_shortcut", "R"),
            (
                self.remove_connected_key_edit,
                self.remove_connected_shortcut,
                "image_editor/remove_connected_shortcut",
                "Shift+R",
            ),
            (
                self.keep_only_colour_key_edit,
                self.keep_only_colour_shortcut,
                "image_editor/keep_only_colour_shortcut",
                "Ctrl+R",
            ),
        )
        settings = QtCore.QSettings("MacroRelay", "Studio")
        for editor, shortcut, key, fallback in defaults:
            sequence = editor.keySequence()
            if sequence.isEmpty():
                sequence = QtGui.QKeySequence(fallback)
                editor.setKeySequence(sequence)
            shortcut.setKey(sequence)
            settings.setValue(key, sequence.toString())

    def _update_brush_preview(self) -> None:
        if not hasattr(self, "view"):
            return
        pixmap = self.view.pixmap()
        if not self.erase_mode or not pixmap or pixmap.isNull() or self.image.isNull():
            self.view.set_brush_preview(False, QtCore.QPointF(), 1.0)
            return
        scale_x = pixmap.width() / max(1, self.image.width())
        scale_y = pixmap.height() / max(1, self.image.height())
        widget_point = QtCore.QPointF(self.brush_point.x() * scale_x, self.brush_point.y() * scale_y)
        radius = self.eraser_size.value() * 0.25 * (scale_x + scale_y)
        self.view.set_brush_preview(True, widget_point, radius)

    def _stamp_brush(self) -> None:
        if not self.erase_mode:
            return
        self._erase_image_point(self.brush_point)
        self._push_history()

    def _erase(self, point: QtCore.QPoint) -> None:
        mapped = self._image_point(point)
        if mapped is None:
            return
        self.brush_point = mapped
        self._erase_image_point(mapped)

    def _erase_image_point(self, mapped: QtCore.QPoint) -> None:
        radius = max(1, self.eraser_size.value() // 2)
        painter = QtGui.QPainter(self.image)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtCore.Qt.transparent)
        painter.drawEllipse(mapped, radius, radius)
        painter.end()
        self._update_view()

    def clear_selection(self) -> None:
        rect = self._image_rect(self.selection)
        if rect.width() < 1 or rect.height() < 1:
            return
        image = self.image.copy()
        painter = QtGui.QPainter(image)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
        painter.fillRect(rect, QtCore.Qt.transparent)
        painter.end()
        self._replace(image)

    def clear_outside_selection(self) -> None:
        rect = self._image_rect(self.selection)
        if rect.width() < 1 or rect.height() < 1:
            return
        result = QtGui.QImage(self.image.size(), QtGui.QImage.Format_ARGB32)
        result.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(result)
        painter.drawImage(rect, self.image, rect)
        painter.end()
        self._replace(result)

    def _rgb_tolerance(self) -> int:
        pct = max(0, min(100, int(self.tolerance.value())))
        return int(round(pct * 255.0 / 100.0))

    def _refresh_palette_ui(self) -> None:
        if not hasattr(self, "palette_layout"):
            return
        while self.palette_layout.count():
            child = self.palette_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if not self.picked_colors:
            empty_lbl = QtWidgets.QLabel("(선택된 색상 없음 - '색상 찍기' 클릭 후 이미지 클릭)")
            empty_lbl.setStyleSheet("color: #718096; font-size: 8.5pt;")
            self.palette_layout.addWidget(empty_lbl)
            return
        for i, clr in enumerate(self.picked_colors):
            chip = QtWidgets.QLabel()
            chip.setFixedSize(22, 22)
            hex_code = f"#{clr.red():02X}{clr.green():02X}{clr.blue():02X}"
            chip.setStyleSheet(f"background-color: {hex_code}; border: 1.5px solid #CBD5E1; border-radius: 4px;")
            chip.setToolTip(f"색상 {i+1}: {hex_code}\n클릭하여 이 색상 제거")
            chip.setCursor(QtCore.Qt.PointingHandCursor)
            chip.mousePressEvent = lambda ev, target_color=clr: self._remove_picked_color(target_color)
            self.palette_layout.addWidget(chip)

    def _remove_picked_color(self, clr: QtGui.QColor) -> None:
        self.picked_colors = [c for c in self.picked_colors if c != clr]
        if self.picked_colors:
            self.picked_color = self.picked_colors[-1]
            last = self.picked_color
            self.pick_button.setStyleSheet(f"background:rgb({last.red()},{last.green()},{last.blue()}); color: {'#000' if last.lightness() > 128 else '#fff'};")
        else:
            self.picked_color = None
            self.pick_button.setStyleSheet("")
        self._refresh_palette_ui()

    def clear_picked_colors(self) -> None:
        self.picked_colors.clear()
        self.picked_points.clear()
        self.picked_color = None
        self.pick_button.setStyleSheet("")
        self._refresh_palette_ui()

    def start_pick_color(self) -> None:
        self.pick_mode = True
        self.eraser.setChecked(False)
        self.view.setCursor(QtCore.Qt.CrossCursor)

    def _pick(self, point: QtCore.QPoint) -> None:
        mapped = self._image_point(point)
        if mapped is None:
            return
        color = self.image.pixelColor(mapped)
        self.picked_color = color
        self.picked_point = mapped
        if not any(abs(c.red() - color.red()) < 3 and abs(c.green() - color.green()) < 3 and abs(c.blue() - color.blue()) < 3 for c in self.picked_colors):
            if len(self.picked_colors) >= 12:
                self.picked_colors.pop(0)
            self.picked_colors.append(color)
            self.picked_points.append(mapped)
        self.pick_mode = False
        self.view.setCursor(QtCore.Qt.ArrowCursor)
        self.pick_button.setStyleSheet(f"background:rgb({color.red()},{color.green()},{color.blue()}); color: {'#000' if color.lightness() > 128 else '#fff'};")
        self._refresh_palette_ui()

    def remove_color(self) -> None:
        if not self.picked_colors and self.picked_color is None:
            QtWidgets.QMessageBox.information(self, "색상 제거", "먼저 '색상 찍기'로 제거할 색을 하나 이상 선택하세요.")
            return
        colors = list(self.picked_colors) if self.picked_colors else [self.picked_color]
        tolerance = self._rgb_tolerance()
        def convert(c: QtGui.QColor) -> QtGui.QColor:
            for target in colors:
                if max(abs(c.red() - target.red()), abs(c.green() - target.green()), abs(c.blue() - target.blue())) <= tolerance:
                    return QtGui.QColor(c.red(), c.green(), c.blue(), 0)
            return c
        self._map_pixels(convert)
        self.clear_picked_colors()

    def keep_only_color(self) -> None:
        if not self.picked_colors and self.picked_color is None:
            QtWidgets.QMessageBox.information(self, "선택 색상만 남기기", "먼저 '색상 찍기'로 남겨둘 색을 하나 이상 선택하세요.")
            return
        colors = list(self.picked_colors) if self.picked_colors else [self.picked_color]
        tolerance = self._rgb_tolerance()
        def convert(c: QtGui.QColor) -> QtGui.QColor:
            matches_any = any(
                max(abs(c.red() - target.red()), abs(c.green() - target.green()), abs(c.blue() - target.blue())) <= tolerance
                for target in colors
            )
            if not matches_any:
                return QtGui.QColor(c.red(), c.green(), c.blue(), 0)
            return c
        self._map_pixels(convert)
        self.clear_picked_colors()

    def remove_connected_color(self) -> None:
        if not self.picked_colors or not self.picked_points:
            if self.picked_color is not None and self.picked_point is not None:
                targets = [(self.picked_point, self.picked_color)]
            else:
                QtWidgets.QMessageBox.information(self, "연결 유사색 제거", "먼저 '색상 찍기'로 시작점을 선택하세요.")
                return
        else:
            targets = list(zip(self.picked_points, self.picked_colors))
        width, height = self.image.width(), self.image.height()
        tolerance = self._rgb_tolerance()
        visited = bytearray(width * height)
        pixels: list[tuple[int, int]] = []
        for pt, target in targets:
            stack = [(pt.x(), pt.y())]
            while stack:
                x, y = stack.pop()
                offset = y * width + x
                if visited[offset]:
                    continue
                visited[offset] = 1
                color = self.image.pixelColor(x, y)
                if max(
                    abs(color.red() - target.red()),
                    abs(color.green() - target.green()),
                    abs(color.blue() - target.blue()),
                ) > tolerance:
                    continue
                pixels.append((x, y))
                if x > 0:
                    stack.append((x - 1, y))
                if x + 1 < width:
                    stack.append((x + 1, y))
                if y > 0:
                    stack.append((x, y - 1))
                if y + 1 < height:
                    stack.append((x, y + 1))
        if not pixels:
            return
        image = self.image.copy()
        for x, y in pixels:
            color = image.pixelColor(x, y)
            color.setAlpha(0)
            image.setPixelColor(x, y, color)
        self._replace(image)
        self.clear_picked_colors()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.isVisible() and self.history_index > 0:
            reply = QtWidgets.QMessageBox.question(
                self,
                "변경사항 저장",
                "편집한 이미지가 아직 저장되지 않았습니다. 저장하시겠습니까?",
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Save,
            )
            if reply == QtWidgets.QMessageBox.Save:
                self.save()
                event.accept()
            elif reply == QtWidgets.QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
                return
        super().closeEvent(event)

    def _backup_original(self) -> None:
        if not self.path.exists():
            return
        target_dir = self.history_root / "assets" / self.alias
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(self.path, target_dir / f"{stamp}{self.path.suffix}")

    def save(self) -> None:
        self._backup_original()
        if not self.image.save(str(self.path)):
            QtWidgets.QMessageBox.warning(self, "저장 실패", "이미지를 저장하지 못했습니다.")
            return
        self.saved.emit(str(self.path))
        self.accept()

    def save_copy(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "복사본 저장", str(self.path.with_name(f"{self.path.stem}-copy.png")), "PNG (*.png);;JPEG (*.jpg *.jpeg);;Bitmap (*.bmp)")
        if filename and not self.image.save(filename):
            QtWidgets.QMessageBox.warning(self, "저장 실패", "복사본을 저장하지 못했습니다.")
