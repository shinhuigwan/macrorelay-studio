from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class ToleranceSpectrumBar(QtWidgets.QWidget):
    """Visual spectrum bar that displays either:
    1. A full 0°~360° Hue rainbow spectrum (for chromatic colors), or
    2. A 0~255 lightness gradient (for grayscale colors: black/gray/white).
    
    A distinct needle indicator marks the exact position of the target color on the spectrum,
    and a highlighted active tolerance window (with dimming outside) visually shows the
    acceptable color deviation.
    
    Supports:
    - Mouse wheel: fine-grained tolerance adjustment (up/down).
    - Mouse click & drag: clicking anywhere expands/contracts tolerance from target pin.
    """

    toleranceChanged = QtCore.Signal(int)

    def __init__(self, color_hex: str = "#FF0000", tolerance: int = 15, parent=None) -> None:
        super().__init__(parent)
        self._color_hex = color_hex
        self._tolerance = max(0, min(255, int(tolerance)))
        self.setFixedHeight(28)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("마우스 휠을 굴리거나 바 위를 클릭/드래그하여 허용 오차 범위를 시각적으로 조절합니다.")

    def set_color(self, hex_code: str) -> None:
        clean = (hex_code or "").strip()
        if clean and clean != self._color_hex:
            self._color_hex = clean
            self.update()

    def setColor(self, hex_code: str) -> None:
        self.set_color(hex_code)

    def set_tolerance(self, tol: int) -> None:
        new_tol = max(0, min(255, int(tol)))
        if new_tol != self._tolerance:
            self._tolerance = new_tol
            self.toleranceChanged.emit(self._tolerance)
            self.update()

    def tolerance(self) -> int:
        return self._tolerance

    def _get_base_color(self) -> QtGui.QColor:
        c = QtGui.QColor(self._color_hex)
        return c if c.isValid() else QtGui.QColor("#FF0000")

    def _get_pin_ratio(self, color: QtGui.QColor) -> tuple[float, bool]:
        """Returns (ratio [0.0..1.0], is_grayscale)."""
        sat = color.saturation()
        if sat < 25:
            return max(0.0, min(1.0, color.lightnessF())), True
        hue = max(0, color.hue())
        return max(0.0, min(1.0, hue / 360.0)), False

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        delta = event.angleDelta().y()
        step = 10 if (event.modifiers() & QtCore.Qt.ShiftModifier) else 2
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
        w = max(1.0, float(self.width()))
        base_color = self._get_base_color()
        pin_ratio, _ = self._get_pin_ratio(base_color)
        pin_x = pin_ratio * w
        dist = abs(x - pin_x)
        # 0 distance = 0 tolerance, full width distance = 255 tolerance
        new_tol = int(round((dist / w) * 255.0))
        self.set_tolerance(new_tol)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        w = float(rect.width())
        h = float(rect.height())
        if w <= 4:
            return

        base_color = self._get_base_color()
        pin_ratio, is_gray = self._get_pin_ratio(base_color)
        pin_x = pin_ratio * w

        # 1. Base Spectrum Gradient
        grad = QtGui.QLinearGradient(0, 0, w, 0)
        if is_gray:
            grad.setColorAt(0.0, QtGui.QColor(15, 15, 15))
            grad.setColorAt(0.5, QtGui.QColor(128, 128, 128))
            grad.setColorAt(1.0, QtGui.QColor(245, 245, 245))
        else:
            grad.setColorAt(0.000, QtGui.QColor.fromHsv(0, 240, 245))
            grad.setColorAt(0.167, QtGui.QColor.fromHsv(60, 240, 245))
            grad.setColorAt(0.333, QtGui.QColor.fromHsv(120, 240, 245))
            grad.setColorAt(0.500, QtGui.QColor.fromHsv(180, 240, 245))
            grad.setColorAt(0.667, QtGui.QColor.fromHsv(240, 240, 245))
            grad.setColorAt(0.833, QtGui.QColor.fromHsv(300, 240, 245))
            grad.setColorAt(1.000, QtGui.QColor.fromHsv(359, 240, 245))

        bar_path = QtGui.QPainterPath()
        bar_path.addRoundedRect(QtCore.QRectF(1, 1, w - 2, h - 2), 6, 6)
        painter.fillPath(bar_path, QtGui.QBrush(grad))

        # 2. Tolerance Range (Active vs Dimmed outside)
        tol_ratio = min(1.0, max(0.0, self._tolerance / 255.0))
        half_w = tol_ratio * w

        left_x = pin_x - half_w
        right_x = pin_x + half_w

        painter.save()
        painter.setClipPath(bar_path)

        dim_color = QtGui.QColor(10, 15, 26, 175)

        if not is_gray:
            if left_x >= 0 and right_x <= w:
                painter.fillRect(QtCore.QRectF(0, 0, left_x, h), dim_color)
                painter.fillRect(QtCore.QRectF(right_x, 0, w - right_x, h), dim_color)
            elif left_x < 0 and right_x <= w:
                wrap_left = w + left_x
                painter.fillRect(QtCore.QRectF(right_x, 0, max(0.0, wrap_left - right_x), h), dim_color)
            elif left_x >= 0 and right_x > w:
                wrap_right = right_x - w
                painter.fillRect(QtCore.QRectF(wrap_right, 0, max(0.0, left_x - wrap_right), h), dim_color)
        else:
            clamped_l = max(0.0, left_x)
            clamped_r = min(w, right_x)
            if clamped_l > 0:
                painter.fillRect(QtCore.QRectF(0, 0, clamped_l, h), dim_color)
            if clamped_r < w:
                painter.fillRect(QtCore.QRectF(clamped_r, 0, w - clamped_r, h), dim_color)

        clamped_l = max(0.0, left_x)
        clamped_r = min(w, right_x)
        active_rect = QtCore.QRectF(clamped_l, 1, max(1.0, clamped_r - clamped_l), h - 2)
        painter.fillRect(active_rect, QtGui.QColor(56, 189, 248, 45))

        pen_bracket = QtGui.QPen(QtGui.QColor("#38BDF8"), 2.0)
        pen_bracket.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen_bracket)
        if clamped_l > 1:
            painter.drawLine(QtCore.QPointF(clamped_l, 2), QtCore.QPointF(clamped_l, h - 2))
        if clamped_r < w - 1:
            painter.drawLine(QtCore.QPointF(clamped_r, 2), QtCore.QPointF(clamped_r, h - 2))

        painter.restore()

        # Outer bar border
        painter.strokePath(bar_path, QtGui.QPen(QtGui.QColor("#334155"), 1.5))

        # 3. Target Color Needle Pin Marker
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2.5))
        painter.drawLine(QtCore.QPointF(pin_x, 0), QtCore.QPointF(pin_x, h))

        # Top Pointer (▼)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
        painter.setPen(QtCore.Qt.NoPen)
        top_tri = QtGui.QPolygonF([
            QtCore.QPointF(pin_x - 5, 0),
            QtCore.QPointF(pin_x + 5, 0),
            QtCore.QPointF(pin_x, 7),
        ])
        painter.drawPolygon(top_tri)

        # Bottom Pointer (▲)
        bot_tri = QtGui.QPolygonF([
            QtCore.QPointF(pin_x - 5, h),
            QtCore.QPointF(pin_x + 5, h),
            QtCore.QPointF(pin_x, h - 7),
        ])
        painter.drawPolygon(bot_tri)

        # Target Color Badge on the Needle
        badge_r = 5.0
        badge_rect = QtCore.QRectF(pin_x - badge_r, h / 2.0 - badge_r, badge_r * 2, badge_r * 2)
        painter.setBrush(QtGui.QBrush(base_color))
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 1.5))
        painter.drawEllipse(badge_rect)


class ColorVariationBar(QtWidgets.QWidget):
    """Draws a smooth gradient bar from Min Color -> Target Color -> Max Color."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(12)
        self._min_c = QtGui.QColor("#000000")
        self._mid_c = QtGui.QColor("#22AC52")
        self._max_c = QtGui.QColor("#FFFFFF")

    def set_colors(self, min_c: QtGui.QColor, mid_c: QtGui.QColor, max_c: QtGui.QColor) -> None:
        self._min_c = min_c
        self._mid_c = mid_c
        self._max_c = max_c
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        w = rect.width()
        if w <= 2:
            return
        grad = QtGui.QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, self._min_c)
        grad.setColorAt(0.5, self._mid_c)
        grad.setColorAt(1.0, self._max_c)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(rect).adjusted(1, 1, -1, -1), 4, 4)
        painter.fillPath(path, QtGui.QBrush(grad))
        painter.strokePath(path, QtGui.QPen(QtGui.QColor("#334155"), 1.0))


class ColorToleranceBarWidget(QtWidgets.QWidget):
    """Complete visual tolerance control widget with:
    - Target color swatch & numerical spinbox
    - Full Hue/Luminance spectrum bar with target color needle & tolerance band
    - Live effective color variation preview (Min 허용 ── 기준 ── Max 허용)
    - Realistic broad-range tolerance presets (±10, ±30, ±60, ±100, ±150)
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, color_hex: str = "#FF0000", tolerance: int = 15, parent=None) -> None:
        super().__init__(parent)
        self._color_hex = color_hex
        self._tolerance = int(tolerance)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(5)

        # 1. Header: Swatch + Info Label + SpinBox
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(20, 20)
        self.swatch.setStyleSheet(f"background: {color_hex}; border: 1.5px solid #64748B; border-radius: 4px;")
        header.addWidget(self.swatch)

        self.lbl_info = QtWidgets.QLabel(f"허용 오차: ±{self._tolerance}")
        self.lbl_info.setStyleSheet("font-weight: 700; color: #70C5FF; font-size: 8.5pt;")
        header.addWidget(self.lbl_info)

        lbl_sub = QtWidgets.QLabel("(RGB 각 채널 오차)")
        lbl_sub.setStyleSheet("color: #64748B; font-size: 8pt;")
        header.addWidget(lbl_sub)

        header.addStretch(1)

        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(0, 255)
        self.spin.setValue(self._tolerance)
        self.spin.setFixedWidth(65)
        self.spin.setStyleSheet("background: #0F172A; border: 1px solid #334155; color: #F1F5F9; border-radius: 4px; padding: 2px; font-weight: bold;")
        self.spin.valueChanged.connect(self._on_spin_changed)
        header.addWidget(self.spin)
        layout.addLayout(header)

        # 2. Visual Spectrum Bar
        self.bar = ToleranceSpectrumBar(color_hex, self._tolerance)
        self.bar.toleranceChanged.connect(self._on_bar_changed)
        layout.addWidget(self.bar)

        # 3. Live Effective Color Variation Preview (Min -> Mid -> Max)
        range_row = QtWidgets.QHBoxLayout()
        range_row.setSpacing(6)

        self.chip_min = QtWidgets.QLabel()
        self.chip_min.setFixedSize(14, 14)
        range_row.addWidget(self.chip_min)

        self.lbl_min = QtWidgets.QLabel("최소 #000000")
        self.lbl_min.setStyleSheet("color: #94A3B8; font-size: 7.5pt; font-family: Consolas, monospace;")
        range_row.addWidget(self.lbl_min)

        self.var_bar = ColorVariationBar()
        range_row.addWidget(self.var_bar, 1)

        self.lbl_max = QtWidgets.QLabel("최대 #FFFFFF")
        self.lbl_max.setStyleSheet("color: #94A3B8; font-size: 7.5pt; font-family: Consolas, monospace;")
        range_row.addWidget(self.lbl_max)

        self.chip_max = QtWidgets.QLabel()
        self.chip_max.setFixedSize(14, 14)
        range_row.addWidget(self.chip_max)

        layout.addLayout(range_row)

        # 4. Preset chips (Realistic broad-range values)
        presets_box = QtWidgets.QHBoxLayout()
        presets_box.setSpacing(4)
        for label, val in [("엄격 ±10", 10), ("보통 ±30", 30), ("넓게 ±60", 60), ("광역 ±100", 100), ("초광역 ±150", 150)]:
            btn = QtWidgets.QPushButton(label)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1E293B;
                    border: 1px solid #334155;
                    color: #94A3B8;
                    font-size: 8pt;
                    padding: 2px 5px;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background: #2D3D54;
                    color: #38BDF8;
                    border-color: #38BDF8;
                }
            """)
            btn.clicked.connect(lambda _, v=val: self.setValue(v))
            presets_box.addWidget(btn)
        layout.addLayout(presets_box)

        self._update_variation_preview()

    def _get_channel_bounds(self) -> tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
        base = QtGui.QColor(self._color_hex)
        if not base.isValid():
            base = QtGui.QColor("#FF0000")
        r, g, b = base.red(), base.green(), base.blue()
        tol = self._tolerance
        min_c = QtGui.QColor(max(0, r - tol), max(0, g - tol), max(0, b - tol))
        max_c = QtGui.QColor(min(255, r + tol), min(255, g + tol), min(255, b + tol))
        return min_c, base, max_c

    def _update_variation_preview(self) -> None:
        min_c, mid_c, max_c = self._get_channel_bounds()
        min_hex = min_c.name().upper()
        max_hex = max_c.name().upper()

        self.chip_min.setStyleSheet(f"background: {min_hex}; border: 1px solid #475569; border-radius: 3px;")
        self.lbl_min.setText(f"최소 {min_hex}")

        self.chip_max.setStyleSheet(f"background: {max_hex}; border: 1px solid #475569; border-radius: 3px;")
        self.lbl_max.setText(f"최대 {max_hex}")

        self.var_bar.set_colors(min_c, mid_c, max_c)

    def _on_spin_changed(self, val: int) -> None:
        self._tolerance = val
        self.lbl_info.setText(f"허용 오차: ±{val}")
        self.bar.set_tolerance(val)
        self._update_variation_preview()
        self.valueChanged.emit(val)

    def _on_bar_changed(self, val: int) -> None:
        self._tolerance = val
        self.lbl_info.setText(f"허용 오차: ±{val}")
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self._update_variation_preview()
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
        clean = (hex_code or "").strip()
        if clean:
            self._color_hex = clean
            self.swatch.setStyleSheet(f"background: {clean}; border: 1.5px solid #64748B; border-radius: 4px;")
            self.bar.set_color(clean)
            self._update_variation_preview()

    def set_color(self, hex_code: str) -> None:
        self.setColor(hex_code)
