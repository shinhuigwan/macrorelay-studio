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
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#D9FFFF"), 1))
        painter.setBrush(QtGui.QColor("#19D7D0"))
        rect = self.rect().adjusted(1, 1, -2, -2)
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


class ScreenCaptureDialog(QtWidgets.QDialog):
    """Full-screen region picker backed by a captured screen pixmap."""

    def __init__(self, pixmap: QtGui.QPixmap, geometry: QtCore.QRect, parent=None) -> None:
        super().__init__(parent)
        self._source = pixmap
        self._origin: QtCore.QPoint | None = None
        self._selection = QtCore.QRect()
        self._drag_mode = ""
        self._drag_start = QtCore.QPoint()
        self._drag_rect = QtCore.QRect()
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
        self.hint = QtWidgets.QLabel("드래그 지정 · 내부 드래그 이동 · 가장자리/모서리 크기 조절 · Enter 저장", self)
        self.hint.setStyleSheet(
            "background:rgba(12,14,20,220); color:white; padding:10px 16px; "
            "border:1px solid #59637A; border-radius:8px; font-weight:700;"
        )
        self.hint.adjustSize()
        self.hint.move(24, 24)

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
            if event.type() == QtCore.QEvent.MouseMove and self._origin is not None:
                self._selection = self._drag_selection(
                    self._drag_mode,
                    self._drag_rect,
                    self._drag_start,
                    event.position().toPoint(),
                    self.canvas.rect(),
                )
                self._show_selection()
                return True
            if event.type() == QtCore.QEvent.MouseMove:
                mode = self._hit_test(self._selection, event.position().toPoint())
                self.canvas.setCursor(self._cursor_for_mode(mode))
                return False
            if event.type() == QtCore.QEvent.MouseButtonRelease and self._origin is not None:
                self._selection = self.rubber.geometry().normalized().intersected(self.canvas.rect())
                self._origin = None
                self._drag_mode = ""
                mode = self._hit_test(self._selection, event.position().toPoint())
                self.canvas.setCursor(self._cursor_for_mode(mode))
                return True
            if event.type() == QtCore.QEvent.MouseButtonDblClick and self._selection.width() >= 4:
                self.accept()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and self._selection.width() >= 4:
            self.accept()
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
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
        self.eraser_size = WheelSafeSpinBox()
        self.eraser_size.setRange(1, 200)
        self.eraser_size.setValue(20)
        clear_selection = QtWidgets.QPushButton("선택 영역 투명화")
        clear_selection.clicked.connect(self.clear_selection)
        self.pick_button = QtWidgets.QPushButton("색상 찍기")
        self.pick_button.clicked.connect(self.start_pick_color)
        remove_color = QtWidgets.QPushButton("찍은 색상 제거")
        remove_color.clicked.connect(self.remove_color)
        self.tolerance = WheelSafeSpinBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setValue(20)
        second.addWidget(self.eraser)
        second.addWidget(QtWidgets.QLabel("크기"))
        second.addWidget(self.eraser_size)
        second.addWidget(clear_selection)
        second.addSpacing(12)
        second.addWidget(self.pick_button)
        second.addWidget(remove_color)
        second.addWidget(QtWidgets.QLabel("허용도"))
        second.addWidget(self.tolerance)
        second.addStretch(1)
        tool_layout.addLayout(second)
        root.addWidget(tools)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(QtCore.Qt.AlignCenter)
        self.scroll.setStyleSheet("background:#090B10; border:1px solid #303647; border-radius:10px;")
        self.view = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        self.view.setStyleSheet("background:#0D0F15;")
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
                rect = (
                    left,
                    top,
                    min(width - left - 1, selected.width()),
                    min(height - top - 1, selected.height()),
                )
                if rect[2] < 4 or rect[3] < 4:
                    return
                cv2.grabCut(bgr, mask, rect, background_model, foreground_model, 7, cv2.GC_INIT_WITH_RECT)
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
        self.view.setCursor(QtCore.Qt.CrossCursor if enabled else QtCore.Qt.ArrowCursor)

    def _erase(self, point: QtCore.QPoint) -> None:
        mapped = self._image_point(point)
        if mapped is None:
            return
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

    def start_pick_color(self) -> None:
        self.pick_mode = True
        self.eraser.setChecked(False)
        self.view.setCursor(QtCore.Qt.CrossCursor)

    def _pick(self, point: QtCore.QPoint) -> None:
        mapped = self._image_point(point)
        if mapped is None:
            return
        self.picked_color = self.image.pixelColor(mapped)
        self.pick_mode = False
        self.view.setCursor(QtCore.Qt.ArrowCursor)
        self.pick_button.setStyleSheet(f"background:rgb({self.picked_color.red()},{self.picked_color.green()},{self.picked_color.blue()});")

    def remove_color(self) -> None:
        if self.picked_color is None:
            QtWidgets.QMessageBox.information(self, "색상 제거", "먼저 '색상 찍기'로 제거할 색을 선택하세요.")
            return
        target = self.picked_color
        tolerance = self.tolerance.value()
        def convert(c: QtGui.QColor) -> QtGui.QColor:
            if max(abs(c.red() - target.red()), abs(c.green() - target.green()), abs(c.blue() - target.blue())) <= tolerance:
                return QtGui.QColor(c.red(), c.green(), c.blue(), 0)
            return c
        self._map_pixels(convert)

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
