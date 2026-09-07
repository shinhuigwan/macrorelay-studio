from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .theme import COLORS


def _find_target_window(exe_name: str, window_token: str) -> int:
    token = str(window_token or "").strip()
    if token.casefold().startswith("ahk_id"):
        raw = token.split(None, 1)[1].strip() if " " in token else ""
        try:
            hwnd = int(raw, 0)
            if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                return hwnd
        except (ValueError, OSError):
            pass
    wanted = Path(str(exe_name or "")).name.casefold()
    if not wanted:
        return 0
    matches: list[int] = []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                if Path(buffer.value).name.casefold() == wanted:
                    matches.append(int(hwnd))
                    return False
        finally:
            kernel32.CloseHandle(handle)
        return True

    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return 0
    return matches[0] if matches else 0


def capture_target_area(step: dict[str, Any]) -> tuple[np.ndarray | None, int, int, str]:
    """Capture target client area (or virtual screen) as a BGR numpy array.
    Returns (bgr_frame, base_x, base_y, description).
    """
    mode = str(step.get("region_mode") or "screen").casefold()
    hwnd = 0
    if mode != "screen":
        hwnd = _find_target_window(
            str(step.get("region_window_exe") or (step.get("click") or {}).get("window_exe") or ""),
            str(step.get("region_window") or (step.get("click") or {}).get("window") or ""),
        )

    user32 = ctypes.windll.user32
    if hwnd and user32.IsWindow(hwnd):
        if mode == "client":
            crect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if user32.GetClientRect(hwnd, ctypes.byref(crect)) and user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                bx, by = int(origin.x), int(origin.y)
                cw = int(crect.right - crect.left)
                ch = int(crect.bottom - crect.top)
                if cw > 4 and ch > 4:
                    from opencv_search import capture_region
                    frame = capture_region(bx, by, bx + cw, by + ch)
                    return frame, bx, by, f"클라이언트 · {cw}×{ch} (기준점 {bx},{by})"
        else:
            wrect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(wrect)):
                bx, by = int(wrect.left), int(wrect.top)
                ww = int(wrect.right - wrect.left)
                wh = int(wrect.bottom - wrect.top)
                if ww > 4 and wh > 4:
                    from opencv_search import capture_region
                    frame = capture_region(bx, by, bx + ww, by + wh)
                    return frame, bx, by, f"창 전체 · {ww}×{wh} (기준점 {bx},{by})"

    # Fallback to screen capture
    geom = QtCore.QRect()
    for s in QtGui.QGuiApplication.screens():
        geom = geom.united(s.geometry())
    l, t, r, b = geom.left(), geom.top(), geom.right() + 1, geom.bottom() + 1
    from opencv_search import capture_region
    frame = capture_region(l, t, r, b)
    return frame, l, t, f"전체 화면 · {r - l}×{b - t}"


class InteractiveCanvas(QtWidgets.QWidget):
    region_dragged = QtCore.Signal(str, list)  # (alias, [l, t, r, b])

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._pixmap: QtGui.QPixmap | None = None
        self._scale: float = 1.0
        self._active_alias: str = ""
        self._regions: dict[str, list[int]] = {}
        self._test_results: dict[str, dict[str, Any]] = {}
        self._drag_start: QtCore.QPoint | None = None
        self._drag_current: QtCore.QPoint | None = None
        self._hover_pos: QtCore.QPoint | None = None
        self.setStyleSheet("background: #0B0E14;")

    def set_frame(self, bgr_frame: np.ndarray | None) -> None:
        if bgr_frame is None or bgr_frame.size == 0:
            self._pixmap = None
        else:
            rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
            self._pixmap = QtGui.QPixmap.fromImage(qimg.copy())
        self.update_geometry()
        self.update()

    def update_geometry(self) -> None:
        if self._pixmap and not self._pixmap.isNull():
            w = int(self._pixmap.width() * self._scale)
            h = int(self._pixmap.height() * self._scale)
            self.setFixedSize(w, h)

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.2, min(4.0, scale))
        self.update_geometry()
        self.update()

    def set_data(
        self,
        regions: dict[str, list[int]],
        test_results: dict[str, dict[str, Any]],
        active_alias: str = "",
    ) -> None:
        self._regions = dict(regions)
        self._test_results = dict(test_results)
        self._active_alias = active_alias
        self.update()

    def set_active_alias(self, alias: str) -> None:
        self._active_alias = alias
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not self._pixmap or self._pixmap.isNull():
            painter.setPen(QtGui.QColor("#8A98B0"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "화면을 캡처하지 못했습니다.")
            return

        painter.drawPixmap(self.rect(), self._pixmap)

        s = self._scale
        # 1. Draw each designated region
        for idx, (alias, reg) in enumerate(self._regions.items()):
            if not reg or len(reg) < 4:
                continue
            l, t, r, b = reg
            res = self._test_results.get(alias, {})
            found = bool(res.get("found", False))
            score = float(res.get("score", 0.0))

            rect = QtCore.QRectF(l * s, t * s, (r - l) * s, (b - t) * s)
            is_active = (alias == self._active_alias)

            # Color: Green if found, Red if missed
            border_color = QtGui.QColor("#4ADE80" if found else "#F87171")
            fill_color = QtGui.QColor(74, 222, 128, 40 if found else 25) if found else QtGui.QColor(248, 113, 113, 40)
            if is_active:
                pen = QtGui.QPen(border_color, 2.5, QtCore.Qt.SolidLine)
            else:
                pen = QtGui.QPen(border_color, 1.8, QtCore.Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(fill_color))
            painter.drawRect(rect)

            # Label above box
            status_text = f"[{idx + 1}] {alias} · {score:.0%} {'✅' if found else '❌'}"
            painter.setPen(QtGui.QColor("#FFFFFF"))
            painter.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            label_bg = QtCore.QRectF(rect.left(), max(0.0, rect.top() - 20), max(rect.width(), 130.0), 18)
            painter.fillRect(label_bg, QtGui.QColor(20, 25, 35, 210))
            painter.drawText(label_bg.adjusted(4, 0, 0, 0), QtCore.Qt.AlignVCenter, status_text)

            # Draw match center crosshair if found
            if found and "hit_x" in res and "hit_y" in res:
                hx = float(res["hit_x"]) * s
                hy = float(res["hit_y"]) * s
                painter.setPen(QtGui.QPen(QtGui.QColor("#22C55E"), 2.0))
                painter.drawLine(QtCore.QPointF(hx - 6, hy), QtCore.QPointF(hx + 6, hy))
                painter.drawLine(QtCore.QPointF(hx, hy - 6), QtCore.QPointF(hx, hy + 6))
                painter.setBrush(QtGui.QBrush(QtGui.QColor("#22C55E")))
                painter.drawEllipse(QtCore.QPointF(hx, hy), 3, 3)

            # Draw hint box if missed inside region but found somewhere else on screen!
            if not found and res.get("full_found") and "full_x" in res and "full_y" in res:
                fx = float(res["full_x"]) * s
                fy = float(res["full_y"]) * s
                fw = float(res.get("tmpl_w", 24)) * s
                fh = float(res.get("tmpl_h", 24)) * s
                hint_rect = QtCore.QRectF(fx - fw / 2, fy - fh / 2, fw, fh)
                dash_pen = QtGui.QPen(QtGui.QColor("#FBBF24"), 2.0, QtCore.Qt.DashLine)
                painter.setPen(dash_pen)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(251, 191, 36, 45)))
                painter.drawRect(hint_rect)
                painter.setPen(QtGui.QColor("#FDE047"))
                painter.setFont(QtGui.QFont("Segoe UI", 8, QtGui.QFont.Bold))
                painter.drawText(QtCore.QPointF(hint_rect.left(), max(12.0, hint_rect.top() - 4)), f"실제 위치 ({res.get('full_score', 0):.0%})")

        # 2. Draw active dragging box
        if self._drag_start and self._drag_current:
            r = QtCore.QRectF(self._drag_start, self._drag_current).normalized()
            painter.setPen(QtGui.QPen(QtGui.QColor("#38BDF8"), 2, QtCore.Qt.DashLine))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(56, 189, 248, 50)))
            painter.drawRect(r)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_current = self._drag_start
            self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._hover_pos = event.position().toPoint()
        if self._drag_start is not None:
            self._drag_current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._drag_start and self._drag_current:
            r = QtCore.QRect(self._drag_start, self._drag_current).normalized()
            self._drag_start = None
            self._drag_current = None
            if r.width() >= 6 and r.height() >= 6 and self._active_alias:
                s = self._scale
                l = int(r.left() / s)
                t = int(r.top() / s)
                right = int(r.right() / s)
                bottom = int(r.bottom() / s)
                self.region_dragged.emit(self._active_alias, [l, t, right, bottom])
            self.update()


class RegionVisualTestDialog(QtWidgets.QDialog):
    """Interactive dialog that displays the live game screen with designated search regions
    overlaid as colored rectangles, evaluates image detection in real-time, and offers
    one-click adjustment tools (margins, shift, snap).
    """

    def __init__(
        self,
        step: dict[str, Any],
        repository: MacroRepository,
        asset_regions: dict[str, list[int]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.step = dict(step)
        self.repository = repository
        self._asset_regions: dict[str, list[int]] = dict(asset_regions or step.get("asset_regions") or {})
        self._aliases: list[str] = self._extract_aliases(step)
        self._bgr_frame: np.ndarray | None = None
        self._base_x: int = 0
        self._base_y: int = 0
        self._capture_desc: str = ""
        self._test_results: dict[str, dict[str, Any]] = {}
        self._required_count: int = int(step.get("required_count") or len(self._aliases))
        self._match_condition: str = str(step.get("match_condition") or "all_matched").strip()

        self.setWindowTitle("🔍 검색 영역 시각화 및 실시간 화면 검사기")
        self.resize(1180, 740)
        self.setStyleSheet("QDialog { background: #11151F; color: #E2E8F0; }")

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # ── LEFT PANEL: Controls & Analysis (width ~400px) ──
        left_panel = QtWidgets.QWidget()
        left_panel.setFixedWidth(410)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # Title & Target Info
        title_box = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("검색 영역 실시간 검증")
        title_lbl.setStyleSheet("font-size: 13pt; font-weight: 800; color: #FFFFFF;")
        title_box.addWidget(title_lbl)
        btn_refresh = QtWidgets.QPushButton("🔄 다시 캡처")
        btn_refresh.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #70C5FF; font-weight: 700; padding: 4px 10px; border-radius: 5px;")
        btn_refresh.clicked.connect(self._do_capture_and_test)
        title_box.addWidget(btn_refresh)
        left_layout.addLayout(title_box)

        self.lbl_target_info = QtWidgets.QLabel("대상: 확인 중…")
        self.lbl_target_info.setStyleSheet("color: #8A98B0; font-size: 9pt;")
        left_layout.addWidget(self.lbl_target_info)

        # Overall Status Banner
        self.banner_box = QtWidgets.QFrame()
        self.banner_box.setStyleSheet("background: #1B2232; border: 1px solid #2B3850; border-radius: 8px; padding: 8px;")
        banner_layout = QtWidgets.QVBoxLayout(self.banner_box)
        banner_layout.setContentsMargins(8, 8, 8, 8)
        self.lbl_banner_main = QtWidgets.QLabel("결과 분석 중…")
        self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800;")
        self.lbl_banner_sub = QtWidgets.QLabel("검색 영역 내 이미지 일치율을 검사합니다.")
        self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #A0ABC0;")
        self.lbl_banner_sub.setWordWrap(True)
        banner_layout.addWidget(self.lbl_banner_main)
        banner_layout.addWidget(self.lbl_banner_sub)
        left_layout.addWidget(self.banner_box)

        # Batch Adjustment Tools
        tools_group = QtWidgets.QGroupBox("⚡ 원클릭 영역 일괄 보정 도구")
        tools_group.setStyleSheet("QGroupBox { font-weight: 700; color: #35C89A; border: 1px solid #204538; border-radius: 8px; margin-top: 6px; padding: 8px; }")
        tools_layout = QtWidgets.QVBoxLayout(tools_group)
        tools_layout.setSpacing(6)

        snap_row = QtWidgets.QHBoxLayout()
        btn_snap_all = QtWidgets.QPushButton("🎯 실제 발견 위치로 자동 맞춤")
        btn_snap_all.setStyleSheet("background: #065F46; border: 1px solid #059669; color: #A7F3D0; font-weight: 700; padding: 6px; border-radius: 6px;")
        btn_snap_all.setToolTip("화면 전체에서 이미지가 발견된 실제 위치를 찾아 각 영역을 그 위치로 즉시 자동 이동합니다.")
        btn_snap_all.clicked.connect(self._snap_all_to_actual_matches)
        snap_row.addWidget(btn_snap_all)
        tools_layout.addLayout(snap_row)

        nudge_row = QtWidgets.QHBoxLayout()
        btn_margin = QtWidgets.QPushButton("↕️ 상하 마진 +10px")
        btn_margin.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #E2E8F0; padding: 4px; border-radius: 4px; font-weight: 600;")
        btn_margin.setToolTip("각 영역의 상하 높이를 위아래로 10px씩 넓혀 화살표가 짤리는 것을 방지합니다.")
        btn_margin.clicked.connect(lambda: self._adjust_margins(10))

        btn_up = QtWidgets.QPushButton("⬆️ 위로 10px")
        btn_up.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #E2E8F0; padding: 4px; border-radius: 4px; font-weight: 600;")
        btn_up.clicked.connect(lambda: self._shift_all_regions(0, -10))

        btn_down = QtWidgets.QPushButton("⬇️ 아래로 10px")
        btn_down.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #E2E8F0; padding: 4px; border-radius: 4px; font-weight: 600;")
        btn_down.clicked.connect(lambda: self._shift_all_regions(0, 10))

        nudge_row.addWidget(btn_margin)
        nudge_row.addWidget(btn_up)
        nudge_row.addWidget(btn_down)
        tools_layout.addLayout(nudge_row)
        left_layout.addWidget(tools_group)

        # Per-Image Cards Scroll Area
        scroll_lbl = QtWidgets.QLabel("이미지별 지정 영역 및 검증 결과:")
        scroll_lbl.setStyleSheet("font-weight: 700; color: #CBD5E1; font-size: 10pt;")
        left_layout.addWidget(scroll_lbl)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #2B3850; border-radius: 8px; background: #131722; }")
        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(6, 6, 6, 6)
        self.cards_layout.setSpacing(6)
        scroll.setWidget(self.cards_container)
        left_layout.addWidget(scroll, 1)

        # Bottom Buttons
        btn_row = QtWidgets.QHBoxLayout()
        btn_save = QtWidgets.QPushButton("✔ 보정된 영역 저장 및 적용")
        btn_save.setStyleSheet("background: #7C6CFF; color: white; font-weight: 800; font-size: 10pt; padding: 10px 16px; border-radius: 6px;")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QtWidgets.QPushButton("닫기")
        btn_cancel.setStyleSheet("padding: 10px 14px; border-radius: 6px; background: #1E2330; color: #E2E8F0; border: 1px solid #2A3040;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save, 1)
        btn_row.addWidget(btn_cancel)
        left_layout.addLayout(btn_row)

        main_layout.addWidget(left_panel)

        # ── RIGHT PANEL: Visual Canvas (interactive view) ──
        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Canvas toolbar
        canv_tools = QtWidgets.QHBoxLayout()
        hint = QtWidgets.QLabel("💡 화면에서 마우스로 드래그하면 선택된 이미지의 검색 영역을 직접 다시 지정할 수 있습니다.")
        hint.setStyleSheet("color: #70C5FF; font-size: 9pt;")
        canv_tools.addWidget(hint, 1)

        btn_zoom_fit = QtWidgets.QPushButton("화면 맞춤")
        btn_zoom_fit.clicked.connect(self._zoom_fit)
        btn_zoom_100 = QtWidgets.QPushButton("100%")
        btn_zoom_100.clicked.connect(lambda: self._set_zoom(1.0))
        btn_zoom_in = QtWidgets.QPushButton("+")
        btn_zoom_in.setFixedWidth(28)
        btn_zoom_in.clicked.connect(lambda: self._set_zoom(self.canvas._scale * 1.25))
        btn_zoom_out = QtWidgets.QPushButton("-")
        btn_zoom_out.setFixedWidth(28)
        btn_zoom_out.clicked.connect(lambda: self._set_zoom(self.canvas._scale * 0.8))

        for b in (btn_zoom_fit, btn_zoom_100, btn_zoom_in, btn_zoom_out):
            b.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #E2E8F0; padding: 4px 8px; border-radius: 4px;")
            canv_tools.addWidget(b)
        right_layout.addLayout(canv_tools)

        # Scroll Area for canvas
        self.canvas_scroll = QtWidgets.QScrollArea()
        self.canvas_scroll.setWidgetResizable(False)
        self.canvas_scroll.setStyleSheet("QScrollArea { border: 1px solid #2B3850; border-radius: 8px; background: #0B0E14; }")
        self.canvas = InteractiveCanvas(self.canvas_scroll)
        self.canvas.region_dragged.connect(self._on_canvas_region_dragged)
        self.canvas_scroll.setWidget(self.canvas)
        right_layout.addWidget(self.canvas_scroll, 1)

        main_layout.addWidget(right_panel, 1)

        # Initial Run
        QtCore.QTimer.singleShot(50, self._do_capture_and_test)

    @staticmethod
    def _extract_aliases(step: dict[str, Any]) -> list[str]:
        raw = step.get("assets") if isinstance(step.get("assets"), list) else []
        aliases = [str(x) for x in raw if str(x).strip()]
        primary = str(step.get("asset") or "").strip()
        if primary and primary not in aliases:
            aliases.insert(0, primary)
        return list(dict.fromkeys(aliases))

    def _do_capture_and_test(self) -> None:
        frame, bx, by, desc = capture_target_area(self.step)
        self._bgr_frame = frame
        self._base_x = bx
        self._base_y = by
        self._capture_desc = desc
        self.lbl_target_info.setText(f"대상: {desc}")

        if frame is None or frame.size == 0:
            self.lbl_banner_main.setText("❌ 화면 캡처 실패")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #F87171;")
            self.lbl_banner_sub.setText("대상 창을 찾지 못했거나 화면 캡처 권한이 없습니다.")
            return

        self.canvas.set_frame(frame)
        self._evaluate_all_regions()
        self._refresh_ui()

    def _evaluate_all_regions(self) -> None:
        if self._bgr_frame is None:
            return
        frame = self._bgr_frame
        fh, fw = frame.shape[:2]
        results: dict[str, dict[str, Any]] = {}

        # Default confidence threshold
        base_thresh = float(self.step.get("confidence") or 80) / 100.0
        conf_map = self.step.get("asset_confidences") or {}

        for alias in self._aliases:
            path = self.repository.asset_path(alias)
            if not path or not Path(path).is_file():
                results[alias] = {"found": False, "score": 0.0, "reason": "이미지 파일 없음"}
                continue
            tmpl = cv2.imread(str(path))
            if tmpl is None or tmpl.size == 0:
                results[alias] = {"found": False, "score": 0.0, "reason": "이미지 디코딩 실패"}
                continue
            th, tw = tmpl.shape[:2]
            thresh = float(conf_map.get(alias, base_thresh * 100)) / 100.0

            # 1. Designated region test
            reg = self._asset_regions.get(alias)
            if not reg or len(reg) < 4:
                # Fallback to node's overall region
                nreg = self.step.get("region") or [0, 0, fw, fh]
                reg = nreg

            l, t, r, b = [int(x) for x in reg[:4]]
            # Clamp to frame dimensions
            cl = max(0, min(l, fw - 1))
            ct = max(0, min(t, fh - 1))
            cr = max(cl + 1, min(r, fw))
            cb = max(ct + 1, min(b, fh))

            crop = frame[ct:cb, cl:cr]
            best_score = 0.0
            hit_x, hit_y = 0, 0
            found = False

            if crop.shape[0] >= th and crop.shape[1] >= tw:
                match_res = cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(match_res)
                best_score = float(max_val)
                if best_score >= thresh:
                    found = True
                    hit_x = cl + max_loc[0] + tw // 2
                    hit_y = ct + max_loc[1] + th // 2
            else:
                best_score = 0.0

            # 2. Full frame search to find where the image ACTUALLY is if missed
            full_found = False
            full_x, full_y = 0, 0
            full_score = 0.0
            if fh >= th and fw >= tw:
                f_res = cv2.matchTemplate(frame, tmpl, cv2.TM_CCOEFF_NORMED)
                _, f_max_val, _, f_max_loc = cv2.minMaxLoc(f_res)
                full_score = float(f_max_val)
                if full_score >= thresh:
                    full_found = True
                    full_x = f_max_loc[0] + tw // 2
                    full_y = f_max_loc[1] + th // 2

            results[alias] = {
                "found": found,
                "score": best_score,
                "threshold": thresh,
                "hit_x": hit_x,
                "hit_y": hit_y,
                "tmpl_w": tw,
                "tmpl_h": th,
                "full_found": full_found,
                "full_x": full_x,
                "full_y": full_y,
                "full_score": full_score,
                "region": [cl, ct, cr, cb],
            }

        self._test_results = results

    def _refresh_ui(self) -> None:
        active = self.canvas._active_alias or (self._aliases[0] if self._aliases else "")
        self.canvas.set_data(self._asset_regions, self._test_results, active_alias=active)

        # Update Top Banner
        total = len(self._aliases)
        matched_count = sum(1 for r in self._test_results.values() if r.get("found"))
        req = self._required_count if self._required_count > 0 else total

        is_success = (matched_count == req) if self._match_condition == "exact_n" else (matched_count >= req)

        if is_success:
            self.banner_box.setStyleSheet("background: #064E3B; border: 1px solid #059669; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"✅ 탐지 성공! ({matched_count}/{total}개 일치 ➔ 참 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #34D399;")
            self.lbl_banner_sub.setText(f"요구 조건({req}개)을 만족하여 [성공(참) 분기]로 정상 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #A7F3D0;")
        else:
            self.banner_box.setStyleSheet("background: #450A0A; border: 1px solid #DC2626; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"❌ 조건 미달 ({matched_count}/{total}개 일치 ➔ 거짓 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #F87171;")
            self.lbl_banner_sub.setText(f"요구 기준은 {req}개이나 {matched_count}개만 발견되어 [실패(거짓) 분기]로 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #FECACA;")

        # Rebuild Cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, alias in enumerate(self._aliases, 1):
            card = self._build_image_card(idx, alias)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def _build_image_card(self, idx: int, alias: str) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        is_active = (alias == self.canvas._active_alias)
        res = self._test_results.get(alias, {})
        found = bool(res.get("found", False))
        score = float(res.get("score", 0.0))

        border_color = "#38BDF8" if is_active else ("#059669" if found else "#7F1D1D")
        bg_color = "#1E293B" if is_active else ("#14241E" if found else "#1C1618")
        card.setStyleSheet(f"QFrame {{ background: {bg_color}; border: 1.5px solid {border_color}; border-radius: 6px; padding: 6px; }}")

        vbox = QtWidgets.QVBoxLayout(card)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(4)

        # Row 1: Index, Alias, Status
        top_row = QtWidgets.QHBoxLayout()
        name_lbl = QtWidgets.QLabel(f"<b>[{idx}] {alias}</b>")
        name_lbl.setStyleSheet("color: #FFFFFF; font-size: 10pt;")
        top_row.addWidget(name_lbl)
        top_row.addStretch(1)

        badge_text = f"일치 {score:.0%} ✅" if found else f"미탐지 ({score:.0%}) ❌"
        badge_color = "#34D399" if found else "#F87171"
        badge = QtWidgets.QLabel(badge_text)
        badge.setStyleSheet(f"color: {badge_color}; font-weight: 700; font-size: 9pt;")
        top_row.addWidget(badge)
        vbox.addLayout(top_row)

        # Row 2: Region coords
        reg = self._asset_regions.get(alias) or [0, 0, 0, 0]
        reg_lbl = QtWidgets.QLabel(f"영역: [{reg[0]}, {reg[1]}, {reg[2]}, {reg[3]}] (크기: {reg[2]-reg[0]}×{reg[3]-reg[1]})")
        reg_lbl.setStyleSheet("color: #94A3B8; font-size: 8.5pt;")
        vbox.addWidget(reg_lbl)

        # Row 3: Diagnosis / Hint if missed
        if not found:
            if res.get("full_found"):
                fy = res.get("full_y", 0)
                diff = fy - (reg[1] + (reg[3] - reg[1]) // 2)
                dir_str = f"아래로 {diff}px" if diff > 0 else f"위로 {abs(diff)}px"
                hint_lbl = QtWidgets.QLabel(f"💡 실제 이미지는 {dir_str}에 있습니다! (노란 점선)")
                hint_lbl.setStyleSheet("color: #FDE047; font-size: 8.5pt; font-weight: 600;")
                vbox.addWidget(hint_lbl)
            else:
                hint_lbl = QtWidgets.QLabel("⚠️ 화면 전체에서도 이미지를 찾지 못했습니다.")
                hint_lbl.setStyleSheet("color: #FCA5A5; font-size: 8.5pt;")
                vbox.addWidget(hint_lbl)

        # Row 4: Card Action Buttons
        btn_box = QtWidgets.QHBoxLayout()
        btn_sel = QtWidgets.QPushButton("선택 (캔버스 드래그 대상)")
        btn_sel.setStyleSheet("background: #0F172A; border: 1px solid #334155; color: #70C5FF; padding: 3px 6px; border-radius: 4px; font-size: 8.5pt;")
        btn_sel.clicked.connect(lambda _, a=alias: self._select_active_alias(a))
        btn_box.addWidget(btn_sel)

        if not found and res.get("full_found"):
            btn_snap = QtWidgets.QPushButton("🎯 실제 위치로 맞춤")
            btn_snap.setStyleSheet("background: #065F46; border: 1px solid #059669; color: #A7F3D0; font-weight: 700; padding: 3px 6px; border-radius: 4px; font-size: 8.5pt;")
            btn_snap.clicked.connect(lambda _, a=alias: self._snap_single_to_actual(a))
            btn_box.addWidget(btn_snap)

        vbox.addLayout(btn_box)
        return card

    def _select_active_alias(self, alias: str) -> None:
        self.canvas.set_active_alias(alias)
        self._refresh_ui()

    def _on_canvas_region_dragged(self, alias: str, reg: list[int]) -> None:
        self._asset_regions[alias] = reg
        self._evaluate_all_regions()
        self._refresh_ui()

    def _shift_all_regions(self, dx: int, dy: int) -> None:
        for alias, reg in list(self._asset_regions.items()):
            if reg and len(reg) >= 4:
                self._asset_regions[alias] = [reg[0] + dx, reg[1] + dy, reg[2] + dx, reg[3] + dy]
        self._evaluate_all_regions()
        self._refresh_ui()

    def _adjust_margins(self, margin: int) -> None:
        for alias, reg in list(self._asset_regions.items()):
            if reg and len(reg) >= 4:
                self._asset_regions[alias] = [reg[0], max(0, reg[1] - margin), reg[2], reg[3] + margin]
        self._evaluate_all_regions()
        self._refresh_ui()

    def _snap_single_to_actual(self, alias: str) -> None:
        res = self._test_results.get(alias, {})
        if res.get("full_found"):
            fx = int(res["full_x"])
            fy = int(res["full_y"])
            tw = int(res.get("tmpl_w", 24))
            th = int(res.get("tmpl_h", 24))
            pad_x = max(16, tw)
            pad_y = max(14, th)
            self._asset_regions[alias] = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
            self._evaluate_all_regions()
            self._refresh_ui()

    def _snap_all_to_actual_matches(self) -> None:
        snapped_count = 0
        for alias in self._aliases:
            res = self._test_results.get(alias, {})
            if res.get("full_found"):
                fx = int(res["full_x"])
                fy = int(res["full_y"])
                tw = int(res.get("tmpl_w", 24))
                th = int(res.get("tmpl_h", 24))
                pad_x = max(16, tw)
                pad_y = max(14, th)
                self._asset_regions[alias] = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
                snapped_count += 1
        if snapped_count > 0:
            self._evaluate_all_regions()
            self._refresh_ui()
            QtWidgets.QMessageBox.information(self, "자동 맞춤 완료", f"총 {snapped_count}개 이미지의 영역을 실제 발견 위치로 자동 보정했습니다!")
        else:
            QtWidgets.QMessageBox.warning(self, "자동 맞춤 불가", "화면 전체에서 일치하는 이미지를 찾지 못했습니다.")

    def _zoom_fit(self) -> None:
        if not self.canvas._pixmap or self.canvas._pixmap.isNull():
            return
        cw = self.canvas_scroll.viewport().width() - 20
        ch = self.canvas_scroll.viewport().height() - 20
        pw = self.canvas._pixmap.width()
        ph = self.canvas._pixmap.height()
        if pw > 0 and ph > 0:
            scale = min(cw / pw, ch / ph, 2.0)
            self.canvas.set_scale(scale)

    def _set_zoom(self, scale: float) -> None:
        self.canvas.set_scale(scale)

    def get_asset_regions(self) -> dict[str, list[int]]:
        return dict(self._asset_regions)

    def get_bounding_region(self) -> list[int] | None:
        valid_regs = [r for r in self._asset_regions.values() if isinstance(r, (list, tuple)) and len(r) >= 4]
        if not valid_regs:
            return None
        min_l = min(int(r[0]) for r in valid_regs)
        min_t = min(int(r[1]) for r in valid_regs)
        max_r = max(int(r[2]) for r in valid_regs)
        max_b = max(int(r[3]) for r in valid_regs)
        return [min_l, min_t, max_r, max_b]
