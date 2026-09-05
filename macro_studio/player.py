"""Dedicated standalone Macro Player for ultra-fast, zero-overhead macro execution.

A sleek, lightweight runner interface separate from the heavy Macro Studio.
Provides instant launch, turbo execution (zero disk I/O), real-time stopwatch,
loop counters, and always-on-top compact remote control.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository, MacroSummary


class MacroPlayerWindow(QtWidgets.QMainWindow):
    """Ultra-fast, lightweight standalone Macro Player."""

    def __init__(self, repository: MacroRepository | None = None, default_macro: str = "", parent=None) -> None:
        super().__init__(parent)
        self.repository = repository or MacroRepository()
        self.default_macro = default_macro
        self.current_process: subprocess.Popen[Any] | None = None
        self.is_running = False
        self.is_paused = False
        self.start_time: float = 0.0
        self.elapsed_offset: float = 0.0
        self.loop_count: int = 0
        self.current_step: int = 0
        self.total_steps: int = 0
        self.current_macro_payload: dict[str, Any] = {}
        self._compact_mode = False

        self._stopwatch_timer = QtCore.QTimer(self)
        self._stopwatch_timer.setInterval(50)
        self._stopwatch_timer.timeout.connect(self._update_stopwatch)

        self._monitor_timer = QtCore.QTimer(self)
        self._monitor_timer.setInterval(60)
        self._monitor_timer.timeout.connect(self._poll_process)

        self._init_ui()
        self._load_settings()
        self.refresh_macro_list()

        if self.default_macro:
            self.select_macro(self.default_macro)

    def _init_ui(self) -> None:
        self.setWindowTitle("⚡ Macro Player")
        self.resize(460, 420)
        self.setMinimumSize(380, 220)

        # Dark theme styling
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #0D1117;
                color: #F0F6FC;
            }
            QWidget {
                color: #F0F6FC;
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }
            QFrame#HeaderBar {
                background: #161B22;
                border-bottom: 1px solid #30363D;
            }
            QFrame#Card {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
            }
            QFrame#DashboardCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #131B2A, stop:1 #1A2234);
                border: 1px solid #2B3A55;
                border-radius: 10px;
            }
            QComboBox {
                background-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 12px;
                color: #F0F6FC;
                font-size: 13px;
                font-weight: bold;
            }
            QComboBox:hover {
                border-color: #58A6FF;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #161B22;
                border: 1px solid #30363D;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
                padding: 4px;
            }
            QCheckBox {
                font-size: 12px;
                color: #C9D1D9;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #30363D;
                background-color: #21262D;
            }
            QCheckBox::indicator:checked {
                background-color: #238636;
                border-color: #2EA043;
            }
            QPushButton#BtnRun {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2EA043, stop:1 #238636);
                color: #FFFFFF;
                border: 1px solid #3FB950;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 800;
                padding: 10px 16px;
            }
            QPushButton#BtnRun:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3FB950, stop:1 #2EA043);
            }
            QPushButton#BtnRun:disabled {
                background: #21262D;
                border-color: #30363D;
                color: #484F58;
            }
            QPushButton#BtnPause {
                background: #D29922;
                color: #FFFFFF;
                border: 1px solid #E3B341;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px 12px;
            }
            QPushButton#BtnPause:hover {
                background: #E3B341;
            }
            QPushButton#BtnStop {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #DA3633, stop:1 #B62324);
                color: #FFFFFF;
                border: 1px solid #F85149;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 800;
                padding: 10px 16px;
            }
            QPushButton#BtnStop:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F85149, stop:1 #DA3633);
            }
            QProgressBar {
                background-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 5px;
                text-align: center;
                color: #C9D1D9;
                font-size: 11px;
                font-weight: bold;
                height: 14px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #238636, stop:1 #2EA043);
                border-radius: 4px;
            }
            QToolButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                color: #8B949E;
                padding: 4px 6px;
                font-size: 12px;
            }
            QToolButton:hover {
                background: #21262D;
                border-color: #30363D;
                color: #F0F6FC;
            }
            QToolButton:checked {
                background: #1F6FEB;
                color: #FFFFFF;
                border-color: #388BFD;
            }
            """
        )

        central_widget = QtWidgets.QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 12, 14, 14)
        main_layout.setSpacing(10)

        # 1. Top Bar: Brand, Always-on-top, Compact toggle
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(8)

        brand_label = QtWidgets.QLabel("⚡ <b>MACRO PLAYER</b>")
        brand_label.setStyleSheet("color: #58A6FF; font-size: 13px; font-weight: 800;")
        top_bar.addWidget(brand_label)

        ver_badge = QtWidgets.QLabel("TURBO")
        ver_badge.setStyleSheet(
            "background: #1F6FEB; color: white; font-size: 9px; font-weight: 800; "
            "border-radius: 4px; padding: 2px 5px;"
        )
        top_bar.addWidget(ver_badge)
        top_bar.addStretch()

        self.btn_pin = QtWidgets.QToolButton(self)
        self.btn_pin.setText("📌 항상 위")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setToolTip("플레이어 창을 다른 프로그램 항상 위에 띄웁니다.")
        self.btn_pin.toggled.connect(self._toggle_always_on_top)
        top_bar.addWidget(self.btn_pin)

        self.btn_compact = QtWidgets.QToolButton(self)
        self.btn_compact.setText("🪟 미니 모드")
        self.btn_compact.setCheckable(True)
        self.btn_compact.setToolTip("화면을 가리지 않는 초슬림 리모컨 모드로 전환합니다.")
        self.btn_compact.toggled.connect(self._toggle_compact_mode)
        top_bar.addWidget(self.btn_compact)

        main_layout.addLayout(top_bar)

        # 2. Macro Selection Card
        select_card = QtWidgets.QFrame(self)
        select_card.setObjectName("Card")
        select_layout = QtWidgets.QHBoxLayout(select_card)
        select_layout.setContentsMargins(10, 8, 10, 8)
        select_layout.setSpacing(8)

        lbl_target = QtWidgets.QLabel("실행 매크로:")
        lbl_target.setStyleSheet("color: #8B949E; font-size: 12px; font-weight: 600;")
        select_layout.addWidget(lbl_target)

        self.macro_combo = QtWidgets.QComboBox(self)
        self.macro_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.macro_combo.currentIndexChanged.connect(self._on_macro_selected)
        select_layout.addWidget(self.macro_combo, 1)

        self.btn_refresh = QtWidgets.QToolButton(self)
        self.btn_refresh.setText("🔄 새로고침")
        self.btn_refresh.setToolTip("스튜디오에서 수정한 매크로 목록을 새로고침합니다.")
        self.btn_refresh.clicked.connect(self.refresh_macro_list)
        select_layout.addWidget(self.btn_refresh)

        main_layout.addWidget(select_card)

        # 3. Mode & Loop Options
        self.opts_card = QtWidgets.QFrame(self)
        self.opts_card.setObjectName("Card")
        opts_layout = QtWidgets.QHBoxLayout(self.opts_card)
        opts_layout.setContentsMargins(10, 8, 10, 8)
        opts_layout.setSpacing(14)

        self.chk_turbo = QtWidgets.QCheckBox("⚡ 초고속 터보 (Zero-Disk I/O)", self)
        self.chk_turbo.setChecked(False)
        self.chk_turbo.setToolTip("매 스텝별 디스크 로깅을 생략하고 순수 메모리에서 0.1ms 극속으로 실행합니다.")
        opts_layout.addWidget(self.chk_turbo)

        self.chk_loop = QtWidgets.QCheckBox("🔁 무한 반복", self)
        self.chk_loop.setToolTip("매크로가 끝나면 처음부터 자동으로 다시 실행합니다.")
        self.chk_loop.toggled.connect(self._on_loop_toggled)
        opts_layout.addWidget(self.chk_loop)

        opts_layout.addStretch()
        main_layout.addWidget(self.opts_card)

        # 4. Live Dashboard Card
        self.dash_card = QtWidgets.QFrame(self)
        self.dash_card.setObjectName("DashboardCard")
        dash_layout = QtWidgets.QVBoxLayout(self.dash_card)
        dash_layout.setContentsMargins(14, 12, 14, 12)
        dash_layout.setSpacing(8)

        # Row 1: State Badge, Timer, Loop counter
        stat_row = QtWidgets.QHBoxLayout()
        self.lbl_status = QtWidgets.QLabel("⚪ 대기 중")
        self.lbl_status.setStyleSheet("color: #8B949E; font-size: 13px; font-weight: 800;")
        stat_row.addWidget(self.lbl_status)
        stat_row.addStretch()

        self.lbl_loop_count = QtWidgets.QLabel("0회차")
        self.lbl_loop_count.setStyleSheet("color: #58A6FF; font-size: 12px; font-weight: 700;")
        stat_row.addWidget(self.lbl_loop_count)

        self.lbl_timer = QtWidgets.QLabel("⏱️ 00:00.00")
        self.lbl_timer.setStyleSheet("color: #3FB950; font-family: 'Consolas', monospace; font-size: 14px; font-weight: 800;")
        stat_row.addWidget(self.lbl_timer)
        dash_layout.addLayout(stat_row)

        # Row 2: Current node step label
        self.lbl_step_detail = QtWidgets.QLabel("선택된 매크로를 실행할 준비가 되었습니다.")
        self.lbl_step_detail.setStyleSheet("color: #E6EDF3; font-size: 12px;")
        self.lbl_step_detail.setWordWrap(True)
        dash_layout.addWidget(self.lbl_step_detail)

        # Row 3: Progress Bar
        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0 / 0 단계")
        dash_layout.addWidget(self.progress_bar)

        main_layout.addWidget(self.dash_card)

        # 5. Big Control Buttons Bar
        ctrl_bar = QtWidgets.QHBoxLayout()
        ctrl_bar.setSpacing(8)

        self.btn_run = QtWidgets.QPushButton("▶ 실 행  [F5]", self)
        self.btn_run.setObjectName("BtnRun")
        self.btn_run.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_run.clicked.connect(self.start_macro)
        ctrl_bar.addWidget(self.btn_run, 3)

        self.btn_pause = QtWidgets.QPushButton("⏸ 일시정지 [F7]", self)
        self.btn_pause.setObjectName("BtnPause")
        self.btn_pause.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        ctrl_bar.addWidget(self.btn_pause, 2)

        self.btn_stop = QtWidgets.QPushButton("⏹ 비상 정지  [F6]", self)
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_macro)
        ctrl_bar.addWidget(self.btn_stop, 3)

        main_layout.addLayout(ctrl_bar)

        # 6. Global Shortcuts
        self._shortcut_f5 = QtGui.QShortcut(QtGui.QKeySequence("F5"), self)
        self._shortcut_f5.activated.connect(self.start_macro)

        self._shortcut_f6 = QtGui.QShortcut(QtGui.QKeySequence("F6"), self)
        self._shortcut_f6.activated.connect(self.stop_macro)

        self._shortcut_f7 = QtGui.QShortcut(QtGui.QKeySequence("F7"), self)
        self._shortcut_f7.activated.connect(self.toggle_pause)

        # 7. Collapsible Mini Log Area
        self.log_edit = QtWidgets.QPlainTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(65)
        self.log_edit.setStyleSheet(
            "background: #0D1117; border: 1px solid #30363D; border-radius: 6px; "
            "color: #8B949E; font-family: 'Consolas', monospace; font-size: 11px;"
        )
        self.log_edit.appendPlainText("Macro Player 준비 완료. 매크로를 선택하고 [F5]를 누르세요.")
        main_layout.addWidget(self.log_edit)

    def _load_settings(self) -> None:
        settings = QtCore.QSettings("MacroRelay", "Player")
        always_on_top = settings.value("always_on_top", True, type=bool)
        if not settings.value("turbo_safe_default_migrated", False, type=bool):
            settings.setValue("turbo_mode", False)
            settings.setValue("turbo_safe_default_migrated", True)
        turbo = settings.value("turbo_mode", False, type=bool)
        compact = settings.value("compact_mode", False, type=bool)

        self.btn_pin.setChecked(always_on_top)
        self.chk_turbo.setChecked(turbo)
        if compact:
            self.btn_compact.setChecked(True)

    def _save_settings(self) -> None:
        settings = QtCore.QSettings("MacroRelay", "Player")
        settings.setValue("always_on_top", self.btn_pin.isChecked())
        settings.setValue("turbo_mode", self.chk_turbo.isChecked())
        settings.setValue("compact_mode", self.btn_compact.isChecked())
        if self.macro_combo.currentText():
            settings.setValue("last_macro", self.macro_combo.currentData() or self.macro_combo.currentText())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.is_running:
            self.stop_macro()
        self._save_settings()
        super().closeEvent(event)

    def _toggle_always_on_top(self, checked: bool) -> None:
        pos = self.pos()
        size = self.size()
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, checked)
        self.show()
        self.move(pos)
        self.resize(size)
        self._save_settings()

    def _toggle_compact_mode(self, checked: bool) -> None:
        self._compact_mode = checked
        if checked:
            self.opts_card.hide()
            self.log_edit.hide()
            self.progress_bar.hide()
            self.setMinimumHeight(160)
            self.adjustSize()
        else:
            self.opts_card.show()
            self.log_edit.show()
            self.progress_bar.show()
            self.setMinimumHeight(220)
            self.resize(self.width(), 430)
        self._save_settings()

    def move_to_cursor(self) -> None:
        """Position the player window near the mouse cursor, clamped within screen bounds."""
        cursor_pos = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(cursor_pos) or QtGui.QGuiApplication.primaryScreen()
        w = self.frameGeometry().width() or self.width()
        h = self.frameGeometry().height() or self.height()
        # Position horizontally centered around cursor, top edge just above/at cursor
        new_x = cursor_pos.x() - (w // 2)
        new_y = cursor_pos.y() - 30
        if screen:
            screen_geo = screen.availableGeometry()
            new_x = max(screen_geo.left(), min(new_x, screen_geo.right() - w))
            new_y = max(screen_geo.top(), min(new_y, screen_geo.bottom() - h))
        self.move(new_x, new_y)

    def refresh_macro_list(self) -> None:
        """Fetch all available macros directly from repository without needing exports."""
        current_sel = self.macro_combo.currentData() or self.default_macro
        if not current_sel:
            settings = QtCore.QSettings("MacroRelay", "Player")
            current_sel = settings.value("last_macro", "")

        self.macro_combo.blockSignals(True)
        self.macro_combo.clear()

        try:
            summaries = self.repository.list_macros()
        except Exception:
            summaries = []

        select_idx = 0
        for i, s in enumerate(summaries):
            title = f"{s.name} ({s.steps}단계)"
            self.macro_combo.addItem(title, s.name)
            if current_sel and s.name == current_sel:
                select_idx = i

        self.macro_combo.blockSignals(False)
        if self.macro_combo.count() > 0:
            self.macro_combo.setCurrentIndex(select_idx)
            self._on_macro_selected(select_idx)

    def select_macro(self, name: str) -> None:
        for idx in range(self.macro_combo.count()):
            if self.macro_combo.itemData(idx) == name or self.macro_combo.itemText(idx).startswith(name):
                self.macro_combo.setCurrentIndex(idx)
                return

    def _on_macro_selected(self, index: int) -> None:
        if index < 0 or index >= self.macro_combo.count():
            return
        name = self.macro_combo.itemData(index)
        try:
            self.current_macro_payload = self.repository.load_macro(name)
            steps = self.current_macro_payload.get("steps") or []
            self.total_steps = len(steps)
            self.progress_bar.setRange(0, self.total_steps)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat(f"0 / {self.total_steps} 단계")
            self.lbl_step_detail.setText(f"대기 중 · 총 {self.total_steps}개 노드로 구성됨")
            self._save_settings()
        except Exception as exc:
            self.lbl_step_detail.setText(f"매크로 로드 오류: {exc}")

    def _on_loop_toggled(self, checked: bool) -> None:
        if checked:
            self.lbl_loop_count.setText("반복 모드 ON")
        else:
            self.lbl_loop_count.setText(f"{self.loop_count}회차")

    def start_macro(self) -> None:
        """Start or restart the selected macro with turbo speed."""
        if self.is_running:
            return
        macro_name = self.macro_combo.currentData()
        if not macro_name:
            QtWidgets.QMessageBox.warning(self, "매크로 선택", "실행할 매크로를 선택하세요.")
            return

        is_turbo = self.chk_turbo.isChecked()
        mode_str = "초고속 터보" if is_turbo else "일반"

        try:
            self.current_process = self.repository.run_macro(macro_name, turbo=is_turbo)
        except Exception as exc:
            self.lbl_status.setText("🔴 실행 실패")
            self.lbl_status.setStyleSheet("color: #F85149; font-size: 13px; font-weight: 800;")
            self.log_edit.appendPlainText(f"[오류] {exc}")
            QtWidgets.QMessageBox.critical(self, "실행 오류", f"매크로를 시작할 수 없습니다:\n{exc}")
            return

        self.is_running = True
        self.is_paused = False
        self.start_time = time.time()
        self.elapsed_offset = 0.0
        self.loop_count += 1

        self.lbl_status.setText(f"🟢 실행 중 ({mode_str})")
        self.lbl_status.setStyleSheet("color: #3FB950; font-size: 13px; font-weight: 800;")
        self.lbl_loop_count.setText(f"{self.loop_count}회차 실행")

        self.btn_run.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ 일시정지 [F7]")
        self.btn_stop.setEnabled(True)

        self._stopwatch_timer.start()
        self._monitor_timer.start()

        self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ▶ '{macro_name}' 실행 시작 ({mode_str})")

    def toggle_pause(self) -> None:
        """Pause or resume the running macro via AutoHotkey control file."""
        if not self.is_running or not self.current_process:
            return
        control_path = getattr(self.current_process, "macrorelay_control_path", None)
        if not control_path:
            return

        if not self.is_paused:
            self.is_paused = True
            try:
                Path(control_path).write_text("PAUSE", encoding="utf-8")
            except Exception:
                pass
            self.lbl_status.setText("🟡 일시정지됨")
            self.lbl_status.setStyleSheet("color: #D29922; font-size: 13px; font-weight: 800;")
            self.btn_pause.setText("▶ 계속 진행 [F7]")
            self._stopwatch_timer.stop()
            self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ⏸ 매크로 일시정지")
        else:
            self.is_paused = False
            try:
                Path(control_path).write_text("RUN", encoding="utf-8")
            except Exception:
                pass
            self.lbl_status.setText("🟢 실행 재개")
            self.lbl_status.setStyleSheet("color: #3FB950; font-size: 13px; font-weight: 800;")
            self.btn_pause.setText("⏸ 일시정지 [F7]")
            self.start_time = time.time() - self.elapsed_offset
            self._stopwatch_timer.start()
            self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ▶ 매크로 재개")

    def stop_macro(self) -> None:
        """Immediately and forcibly terminate the running macro."""
        if not self.is_running and not self.current_process:
            return

        proc = self.current_process
        self.current_process = None
        self.is_running = False
        self.is_paused = False
        self._stopwatch_timer.stop()
        self._monitor_timer.stop()

        if proc:
            pid = getattr(proc, "pid", None)
            try:
                proc.kill()
            except Exception:
                pass
            if pid:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=1.0)
                except Exception:
                    pass

        self.lbl_status.setText("🔴 비상 정지됨")
        self.lbl_status.setStyleSheet("color: #F85149; font-size: 13px; font-weight: 800;")
        self.lbl_step_detail.setText("사용자가 매크로를 비상 정지했습니다.")

        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ 일시정지 [F7]")
        self.btn_stop.setEnabled(False)

        self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ⏹ 매크로 정지 완료")

    def _update_stopwatch(self) -> None:
        if not self.is_running or self.is_paused:
            return
        self.elapsed_offset = time.time() - self.start_time
        mins = int(self.elapsed_offset // 60)
        secs = int(self.elapsed_offset % 60)
        millis = int((self.elapsed_offset * 100) % 100)
        self.lbl_timer.setText(f"⏱️ {mins:02d}:{secs:02d}.{millis:02d}")

    def _poll_process(self) -> None:
        if not self.is_running or not self.current_process:
            return

        ret = self.current_process.poll()

        # Update step progress
        progress_path = getattr(self.current_process, "macrorelay_progress_path", None)
        if progress_path and os.path.exists(progress_path):
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content.isdigit():
                    step_num = int(content)
                    if step_num != self.current_step and step_num > 0:
                        self.current_step = step_num
                        self.progress_bar.setValue(self.current_step)
                        self.progress_bar.setFormat(f"{self.current_step} / {self.total_steps} 단계")

                        steps = self.current_macro_payload.get("steps") or []
                        if 0 < self.current_step <= len(steps):
                            st = steps[self.current_step - 1]
                            action = st.get("action", "step")
                            label = st.get("label") or action
                            self.lbl_step_detail.setText(f"진행 중: {self.current_step}번 [{label}]")
            except Exception:
                pass

        if ret is not None:
            # Process terminated
            self._handle_finished(ret)

    def _handle_finished(self, return_code: int) -> None:
        self.is_running = False
        self.is_paused = False
        self._stopwatch_timer.stop()
        self._monitor_timer.stop()
        self.current_process = None

        if return_code == 0:
            self.lbl_status.setText("🔵 실행 완료")
            self.lbl_status.setStyleSheet("color: #58A6FF; font-size: 13px; font-weight: 800;")
            self.lbl_step_detail.setText(f"매크로 정상 완료 ({self.total_steps}단계 완료, {self.lbl_timer.text()})")
            self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ✔ 매크로 1회 정상 완료 ({self.lbl_timer.text()})")

            # Check infinite loop option
            if self.chk_loop.isChecked():
                self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] 🔁 무한 반복: 다음 회차 즉시 시작...")
                QtCore.QTimer.singleShot(150, self.start_macro)
                return
        else:
            self.lbl_status.setText("🔴 비정상 종료")
            self.lbl_status.setStyleSheet("color: #F85149; font-size: 13px; font-weight: 800;")
            self.lbl_step_detail.setText(f"종료 코드: {return_code}")
            self.log_edit.appendPlainText(f"[{time.strftime('%H:%M:%S')}] ⚠️ 매크로 비정상 종료 (코드 {return_code})")

        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)


def launch_player(macro_name: str = "") -> int:
    """Entry point to launch the standalone Macro Player."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("MacroRelay Player")
    app.setStyle("Fusion")

    repo = MacroRepository()
    window = MacroPlayerWindow(repository=repo, default_macro=macro_name)

    icon_path = repo.root / "branding" / "macrorelay-runner.ico"
    if not icon_path.exists():
        icon_path = repo.root / "branding" / "macrorelay-studio.ico"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
        window.setWindowIcon(QtGui.QIcon(str(icon_path)))

    window.show()
    window.move_to_cursor()
    return app.exec()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(launch_player(target))
