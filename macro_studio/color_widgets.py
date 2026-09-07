from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class ToleranceSpectrumBar(QtWidgets.QWidget):
    """Visual spectrum bar that displays the target color gradient, a center marker,
    and a tolerance range overlay that responds to mouse wheel and dragging.
    """

    toleranceChanged = QtCore.Signal(int)

    def __init__(self, color_hex: str = "#FF0000", tolerance: int = 15, parent=None) -> None:
        super().__init__(parent)
        self._color_hex = color_hex
        self._tolerance = max(0, min(255, int(tolerance)))
        self.setFixedHeight(28)
        self.setCursor(QtCore.Qt.SizeHorCursor)
        self.setToolTip("마우스 휠을 굴리거나 드래그하여 허용 오차 범위를 시각적으로 조절합니다.")

    def set_color(self, hex_code: str) -> None:
        self._color_hex = hex_code
        self.update()

    def set_tolerance(self, tol: int) -> None:
        new_tol = max(0, min(255, int(tol)))
        if new_tol != self._tolerance:
            self._tolerance = new_tol
            self.toleranceChanged.emit(self._tolerance)
            self.update()

    def tolerance(self) -> int:
        return self._tolerance

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        delta = event.angleDelta().y()
        step = 5 if (event.modifiers() & QtCore.Qt.ShiftModifier) else 1
        if delta > 0:
            self.set_tolerance(self._tolerance + step)
        elif delta < 0:
            self.set_tolerance(self._tolerance - step)
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._adjust_from_pos(event.position().x())
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.LeftButton:
            self._adjust_from_pos(event.position().x())
            event.accept()

    def _adjust_from_pos(self, x: float) -> None:
        w = self.width()
        if w <= 0:
            return
        cx = w / 2.0
        dist = abs(x - cx)
        new_tol = int((dist / (cx or 1.0)) * 60.0)
        self.set_tolerance(new_tol)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()

        base_color = QtGui.QColor(self._color_hex) if QtGui.QColor(self._color_hex).isValid() else QtGui.QColor("#FF0000")
        r, g, b = base_color.red(), base_color.green(), base_color.blue()

        # Build gradient: Darker variation -> Base color -> Brighter variation
        dark_c = QtGui.QColor(max(0, r - 50), max(0, g - 50), max(0, b - 50))
        bright_c = QtGui.QColor(min(255, r + 50), min(255, g + 50), min(255, b + 50))

        grad = QtGui.QLinearGradient(0, 0, rect.width(), 0)
        grad.setColorAt(0.0, dark_c)
        grad.setColorAt(0.5, base_color)
        grad.setColorAt(1.0, bright_c)

        # Draw base bar
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect).adjusted(1, 1, -1, -1), 6, 6)
        painter.fillPath(path, QtGui.QBrush(grad))
        painter.strokePath(path, QtGui.QPen(QtGui.QColor("#334155"), 1.5))

        # Tolerance band calculation (center = 0.5)
        ratio = min(1.0, self._tolerance / 60.0)
        cx = rect.width() / 2.0
        band_w = cx * ratio
        band_rect = QtCore.QRectF(cx - band_w, 2, band_w * 2, rect.height() - 4)

        # Highlight tolerance band with semi-transparent cyan glow
        painter.fillRect(band_rect, QtGui.QColor(56, 189, 248, 60))
        painter.setPen(QtGui.QPen(QtGui.QColor("#38BDF8"), 2.0, QtCore.Qt.DashLine))
        painter.drawRect(band_rect)

        # Center marker needle for target color
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2.5))
        painter.drawLine(QtCore.QPointF(cx, 0), QtCore.QPointF(cx, rect.height()))

        # Draw small marker head
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
        tri = QtGui.QPolygonF([QtCore.QPointF(cx - 4, 0), QtCore.QPointF(cx + 4, 0), QtCore.QPointF(cx, 5)])
        painter.drawPolygon(tri)


class ColorToleranceBarWidget(QtWidgets.QWidget):
    """Complete visual tolerance control widget with color swatch, tolerance bar,
    wheel interaction, numerical spinbox, and preset quick buttons.
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, color_hex: str = "#FF0000", tolerance: int = 15, parent=None) -> None:
        super().__init__(parent)
        self._color_hex = color_hex
        self._tolerance = int(tolerance)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(4)

        # Header: Color preview + Spinbox + Label
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(20, 20)
        self.swatch.setStyleSheet(f"background: {color_hex}; border: 1px solid #64748B; border-radius: 4px;")
        header.addWidget(self.swatch)

        self.lbl_info = QtWidgets.QLabel(f"허용 오차: ±{self._tolerance}")
        self.lbl_info.setStyleSheet("font-weight: 700; color: #70C5FF; font-size: 8.5pt;")
        header.addWidget(self.lbl_info)

        header.addStretch(1)

        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(0, 255)
        self.spin.setValue(self._tolerance)
        self.spin.setFixedWidth(65)
        self.spin.setStyleSheet("background: #0F172A; border: 1px solid #334155; color: #F1F5F9; border-radius: 4px; padding: 2px;")
        self.spin.valueChanged.connect(self._on_spin_changed)
        header.addWidget(self.spin)
        layout.addLayout(header)

        # Visual spectrum bar
        self.bar = ToleranceSpectrumBar(color_hex, self._tolerance)
        self.bar.toleranceChanged.connect(self._on_bar_changed)
        layout.addWidget(self.bar)

        # Preset chips
        presets_box = QtWidgets.QHBoxLayout()
        presets_box.setSpacing(4)
        for label, val in [("엄격 ±5", 5), ("보통 ±15", 15), ("넓게 ±30", 30), ("광역 ±50", 50)]:
            btn = QtWidgets.QPushButton(label)
            btn.setStyleSheet("background: #1E293B; border: 1px solid #334155; color: #94A3B8; font-size: 8pt; padding: 2px 5px; border-radius: 3px;")
            btn.clicked.connect(lambda _, v=val: self.setValue(v))
            presets_box.addWidget(btn)
        layout.addLayout(presets_box)

    def _on_spin_changed(self, val: int) -> None:
        self._tolerance = val
        self.lbl_info.setText(f"허용 오차: ±{val}")
        self.bar.set_tolerance(val)
        self.valueChanged.emit(val)

    def _on_bar_changed(self, val: int) -> None:
        self._tolerance = val
        self.lbl_info.setText(f"허용 오차: ±{val}")
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self.valueChanged.emit(val)

    def setValue(self, val: int) -> None:
        self.spin.setValue(int(val))

    def value(self) -> int:
        return self._tolerance

    def set_tolerance(self, tol: int) -> None:
        self.setValue(tol)

    def tolerance(self) -> int:
        return self.value()

    def setColor(self, hex_code: str) -> None:
        self._color_hex = hex_code
        self.swatch.setStyleSheet(f"background: {hex_code}; border: 1px solid #64748B; border-radius: 4px;")
        self.bar.set_color(hex_code)

    def set_color(self, hex_code: str) -> None:
        self.setColor(hex_code)
