"""Floating Mini HUD widget for MacroRelay Studio.

An always-on-top, frameless, translucent mini control bar that floats on the screen
corner when Studio is minimized or running behind full-screen games/emulators.
Provides real-time node progress, instant controls (pause/resume/stop), and
latest click/search screenshot thumbnail preview.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:
    from .main_window import MainWindow


class ClickPreviewPopup(QtWidgets.QDialog):
    """Compact popover window showing full click/search preview when thumbnail is clicked."""

    def __init__(self, pixmap: QtGui.QPixmap, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        card = QtWidgets.QFrame(self)
        card.setStyleSheet(
            "QFrame {"
            "  background-color: #0f172a;"
            "  border: 1px solid #38bdf8;"
            "  border-radius: 8px;"
            "}"
        )
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("최근 인식/클릭 화면 미리보기", card)
        title.setStyleSheet("color: #38bdf8; font-weight: 700; font-size: 11px;")
        btn_close = QtWidgets.QPushButton("✕", card)
        btn_close.setFixedSize(20, 20)
        btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #94a3b8; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #f8fafc; background: #334155; border-radius: 3px; }"
        )
        btn_close.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(btn_close)
        card_layout.addLayout(header)

        img_label = QtWidgets.QLabel(card)
        max_w, max_h = 360, 260
        scaled = pixmap.scaled(
            max_w, max_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        img_label.setPixmap(scaled)
        img_label.setAlignment(QtCore.Qt.AlignCenter)
        card_layout.addWidget(img_label)

        layout.addWidget(card)


class FloatingMiniHUD(QtWidgets.QWidget):
    """Always-on-top, translucent mini HUD bar for hands-free automation monitoring."""

    def __init__(self, main_window: MainWindow) -> None:
        super().__init__(
            None,
            QtCore.Qt.Window
            | QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool,
        )
        self.main_window = main_window
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("MacroRelay Mini HUD")

        self._drag_pos: QtCore.QPoint | None = None
        self._is_running: bool = False
        self._is_paused: bool = False
        self._last_pixmap: QtGui.QPixmap | None = None
        self._preview_popup: ClickPreviewPopup | None = None

        self._build_ui()
        self._load_saved_position()

    def _build_ui(self) -> None:
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)

        # Outer card frame
        self.card = QtWidgets.QFrame(self)
        self.card.setObjectName("hudCard")
        self.card.setStyleSheet(
            "#hudCard {"
            "  background-color: rgba(15, 23, 42, 235);"
            "  border: 1px solid #2563eb;"
            "  border-radius: 10px;"
            "  font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;"
            "}"
        )
        card_layout = QtWidgets.QHBoxLayout(self.card)
        card_layout.setContentsMargins(10, 6, 10, 6)
        card_layout.setSpacing(8)

        # 1. Drag grip / App indicator
        grip_label = QtWidgets.QLabel("⋮⋮", self.card)
        grip_label.setStyleSheet("color: #64748b; font-size: 14px; font-weight: bold;")
        grip_label.setToolTip("마우스로 드래그하여 원하는 위치로 이동하세요")
        card_layout.addWidget(grip_label)

        # Status Dot
        self.status_dot = QtWidgets.QLabel(self.card)
        self.status_dot.setFixedSize(10, 10)
        self._set_status_dot_color("#64748b")
        card_layout.addWidget(self.status_dot)

        # 2. Text Info (Macro title + Running node description)
        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_label = QtWidgets.QLabel("대기 중", self.card)
        self.title_label.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #38bdf8; letter-spacing: 0.3px;"
        )

        self.step_label = QtWidgets.QLabel("매크로가 실행되지 않았습니다.", self.card)
        self.step_label.setStyleSheet("font-size: 11px; color: #e2e8f0; font-weight: 500;")
        self.step_label.setMaximumWidth(220)

        text_col.addWidget(self.title_label)
        text_col.addWidget(self.step_label)
        card_layout.addLayout(text_col)

        # 3. Screenshot Thumbnail Preview Button
        self.btn_thumb = QtWidgets.QPushButton(self.card)
        self.btn_thumb.setFixedSize(34, 34)
        self.btn_thumb.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_thumb.setStyleSheet(
            "QPushButton {"
            "  background-color: #1e293b;"
            "  border: 1px solid #475569;"
            "  border-radius: 6px;"
            "  color: #64748b;"
            "  font-size: 9px;"
            "}"
            "QPushButton:hover {"
            "  border-color: #38bdf8;"
            "}"
        )
        self.btn_thumb.setText("📷")
        self.btn_thumb.setToolTip("최근 인식/클릭 화면 썸네일 (클릭 시 확대)")
        self.btn_thumb.clicked.connect(self._show_thumb_preview)
        card_layout.addWidget(self.btn_thumb)

        # Separator line
        sep = QtWidgets.QFrame(self.card)
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        sep.setStyleSheet("color: #334155; margin: 2px 2px;")
        card_layout.addWidget(sep)

        # 4. Control Buttons (Pause/Resume, Stop, Restore Studio, Close HUD)
        btn_style = (
            "QPushButton {"
            "  background-color: #1e293b;"
            "  color: #f1f5f9;"
            "  border: 1px solid #334155;"
            "  border-radius: 5px;"
            "  font-size: 12px;"
            "  font-weight: bold;"
            "  min-width: 26px;"
            "  min-height: 26px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #334155;"
            "  border-color: #38bdf8;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #0f172a;"
            "}"
        )

        self.btn_pause_resume = QtWidgets.QPushButton("⏸", self.card)
        self.btn_pause_resume.setStyleSheet(btn_style)
        self.btn_pause_resume.setToolTip("일시정지 (Pause)")
        self.btn_pause_resume.clicked.connect(self._on_pause_resume_clicked)
        card_layout.addWidget(self.btn_pause_resume)

        self.btn_stop = QtWidgets.QPushButton("⏹", self.card)
        stop_style = (
            btn_style.replace("#334155;", "#450a0a;")
            .replace("#38bdf8;", "#ef4444;")
            .replace("#f1f5f9;", "#fca5a5;")
        )
        self.btn_stop.setStyleSheet(stop_style)
        self.btn_stop.setToolTip("즉시 정지 (Stop)")
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        card_layout.addWidget(self.btn_stop)

        self.btn_restore = QtWidgets.QPushButton("🪟", self.card)
        self.btn_restore.setStyleSheet(btn_style)
        self.btn_restore.setToolTip("스튜디오 창 복원 및 열기")
        self.btn_restore.clicked.connect(self._restore_studio)
        card_layout.addWidget(self.btn_restore)

        self.btn_close = QtWidgets.QPushButton("✕", self.card)
        self.btn_close.setStyleSheet(
            "QPushButton {"
            "  background: transparent; color: #64748b; border: none; font-size: 11px;"
            "}"
            "QPushButton:hover { color: #f8fafc; background: #334155; border-radius: 4px; }"
        )
        self.btn_close.setToolTip("미니 HUD 닫기")
        self.btn_close.clicked.connect(self.hide)
        card_layout.addWidget(self.btn_close)

        root_layout.addWidget(self.card)
        self.adjustSize()

    def _set_status_dot_color(self, color_hex: str) -> None:
        self.status_dot.setStyleSheet(
            f"background-color: {color_hex}; border-radius: 5px;"
        )

    def update_status(
        self,
        macro_name: str,
        step_index: int,
        total_steps: int,
        step_label: str,
        is_running: bool,
        is_paused: bool,
    ) -> None:
        """Update HUD UI reflecting latest macro state."""
        self._is_running = is_running
        self._is_paused = is_paused

        if not is_running:
            self.title_label.setText("대기 중")
            self.step_label.setText("매크로가 실행되지 않았습니다.")
            self._set_status_dot_color("#64748b")
            self.card.setStyleSheet(
                "#hudCard {"
                "  background-color: rgba(15, 23, 42, 235);"
                "  border: 1px solid #334155;"
                "  border-radius: 10px;"
                "}"
            )
            self.btn_pause_resume.setText("▶")
            self.btn_pause_resume.setToolTip("매크로 실행")
            self.btn_pause_resume.setEnabled(False)
            self.btn_stop.setEnabled(False)
            return

        self.btn_pause_resume.setEnabled(True)
        self.btn_stop.setEnabled(True)

        clean_name = macro_name or "매크로"
        if is_paused:
            self.title_label.setText(f"{clean_name} · 일시정지됨")
            self._set_status_dot_color("#eab308")
            self.card.setStyleSheet(
                "#hudCard {"
                "  background-color: rgba(28, 25, 23, 240);"
                "  border: 1px solid #eab308;"
                "  border-radius: 10px;"
                "}"
            )
            self.btn_pause_resume.setText("▶")
            self.btn_pause_resume.setToolTip("실행 재개 (Resume)")
        else:
            self.title_label.setText(f"{clean_name} · 실행 중")
            self._set_status_dot_color("#22c55e")
            self.card.setStyleSheet(
                "#hudCard {"
                "  background-color: rgba(15, 23, 42, 240);"
                "  border: 1px solid #2563eb;"
                "  border-radius: 10px;"
                "}"
            )
            self.btn_pause_resume.setText("⏸")
            self.btn_pause_resume.setToolTip("일시정지 (Pause)")

        # Node progress string
        if step_index > 0:
            count_info = f"[{step_index}/{total_steps}]" if total_steps > 0 else f"[{step_index}번]"
            display_text = f"{count_info} {step_label or '노드 실행 중...'}"
            metrics = QtGui.QFontMetrics(self.step_label.font())
            elided = metrics.elidedText(display_text, QtCore.Qt.ElideRight, 220)
            self.step_label.setText(elided)
            self.step_label.setToolTip(display_text)
        else:
            self.step_label.setText("다음 노드 대기 중...")

    def update_click_preview(self, pixmap: QtGui.QPixmap) -> None:
        """Update thumbnail button with latest click/detection capture."""
        if pixmap.isNull():
            return
        self._last_pixmap = pixmap
        icon_pixmap = pixmap.scaled(
            30, 30, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        self.btn_thumb.setIcon(QtGui.QIcon(icon_pixmap))
        self.btn_thumb.setIconSize(QtCore.QSize(28, 28))
        self.btn_thumb.setText("")

    def _show_thumb_preview(self) -> None:
        if self._last_pixmap is None or self._last_pixmap.isNull():
            return
        # A modal exec() here starts a nested Qt event loop.  That made the
        # Studio look frozen while the preview was open and could swallow the
        # stop/restore buttons.  Keep one non-modal popup instead.
        if self._preview_popup is not None and self._preview_popup.isVisible():
            self._preview_popup.raise_()
            self._preview_popup.activateWindow()
            return
        popup = ClickPreviewPopup(self._last_pixmap, self)
        popup.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        popup.destroyed.connect(lambda *_args: setattr(self, "_preview_popup", None))
        self._preview_popup = popup
        global_pos = self.btn_thumb.mapToGlobal(QtCore.QPoint(0, self.btn_thumb.height() + 4))
        popup.adjustSize()
        screen = QtGui.QGuiApplication.screenAt(global_pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(global_pos.x(), area.right() - popup.width() + 1))
            y = max(area.top(), min(global_pos.y(), area.bottom() - popup.height() + 1))
            popup.move(x, y)
        else:
            popup.move(global_pos)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _on_pause_resume_clicked(self) -> None:
        if not self._is_running:
            return
        if self._is_paused:
            self.main_window.resume_running_macros()
        else:
            self.main_window.pause_running_macros()

    def _on_stop_clicked(self) -> None:
        self.main_window.stop_running_macros()

    def _restore_studio(self) -> None:
        self.main_window.showNormal()
        self.main_window.activateWindow()
        self.main_window.raise_()

    # ── Mouse Dragging & Double-Click Support ──

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_pos is not None and (event.buttons() & QtCore.Qt.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(self._clamp_position(new_pos))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._drag_pos is not None:
            self._drag_pos = None
            self._save_position()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._restore_studio()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _save_position(self) -> None:
        settings = QtCore.QSettings("MacroRelay", "MiniHUD")
        settings.setValue("pos_x", self.x())
        settings.setValue("pos_y", self.y())

    @staticmethod
    def _virtual_available_geometry() -> QtCore.QRect:
        screens = QtGui.QGuiApplication.screens()
        if not screens:
            return QtCore.QRect(0, 0, 1920, 1080)
        geometry = QtCore.QRect()
        for screen in screens:
            geometry = geometry.united(screen.availableGeometry())
        return geometry

    def _clamp_position(self, position: QtCore.QPoint) -> QtCore.QPoint:
        """Keep the HUD reachable on any monitor, including negative origins."""
        virtual = self._virtual_available_geometry()
        width = max(1, self.width())
        height = max(1, self.height())
        max_x = virtual.right() - width + 1
        max_y = virtual.bottom() - height + 1
        return QtCore.QPoint(
            max(virtual.left(), min(position.x(), max_x)),
            max(virtual.top(), min(position.y(), max_y)),
        )

    def _load_saved_position(self) -> None:
        settings = QtCore.QSettings("MacroRelay", "MiniHUD")
        pos_x = settings.value("pos_x", None)
        pos_y = settings.value("pos_y", None)
        screen_geo = self._virtual_available_geometry()

        if pos_x is not None and pos_y is not None:
            try:
                x = int(pos_x)
                y = int(pos_y)
                self.move(self._clamp_position(QtCore.QPoint(x, y)))
                return
            except (TypeError, ValueError):
                pass

        default_x = screen_geo.right() - self.width() - 20
        default_y = screen_geo.top() + 40
        self.move(self._clamp_position(QtCore.QPoint(default_x, default_y)))

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        if self._preview_popup is not None:
            self._preview_popup.close()
        super().hideEvent(event)
