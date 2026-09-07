from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import re
from typing import Any

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .color_widgets import ColorToleranceBarWidget, ToleranceSpectrumBar
from .repository import MacroRepository
from .theme import COLORS


def hex_to_bgr(hex_str: str) -> tuple[int, int, int]:
    h = str(hex_str).strip().lstrip("#").lower()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) == 6:
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return (b, g, r)
        except ValueError:
            pass
    return (0, 0, 255)


def _ensure_interactive_desktop() -> None:
    """Attach current thread to the interactive desktop if running under an isolated desktop station."""
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


_ensure_interactive_desktop()


def _read_image_unicode(path: Path | str) -> np.ndarray | None:
    """Read an image from path supporting Windows Unicode/Korean paths and convert to BGR 3-channel."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = np.fromfile(str(p.resolve()), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None or img.size == 0:
            return None
        if img.ndim == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        elif img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img
    except Exception:
        return None


def _get_window_info(hwnd: int) -> dict[str, Any]:
    """Retrieve detailed metadata for a given HWND."""
    if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
        return {}
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    title_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buf, 512)
    title = title_buf.value.strip()

    cls_buf = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, cls_buf, 512)
    cls_name = cls_buf.value.strip()

    proc_name = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                proc_name = Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    return {
        "hwnd": hwnd,
        "pid": pid.value,
        "title": title,
        "class_name": cls_name,
        "exe_name": proc_name,
    }


def _find_target_window(exe_name: str, window_token: str) -> int:
    token = str(window_token or "").strip()
    m_id = re.search(r"ahk_id\s+([^\s,]+)", token, re.IGNORECASE)
    if m_id:
        try:
            hwnd = int(m_id.group(1).strip(), 0)
            if hwnd and ctypes.windll.user32.IsWindow(hwnd):
                return hwnd
        except (ValueError, OSError):
            pass

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32


    wanted_exe = Path(str(exe_name or "")).name.casefold()
    wanted_class = ""
    wanted_pid = 0

    m_exe = re.search(r"ahk_exe\s+([^\s,]+)", token, re.IGNORECASE)
    if m_exe:
        wanted_exe = Path(m_exe.group(1)).name.casefold()

    m_class = re.search(r"ahk_class\s+([^\s,]+)", token, re.IGNORECASE)
    if m_class:
        wanted_class = m_class.group(1).strip()

    m_pid = re.search(r"ahk_pid\s+([^\s,]+)", token, re.IGNORECASE)
    if m_pid:
        try:
            wanted_pid = int(m_pid.group(1).strip(), 0)
        except ValueError:
            pass

    cleaned_title = re.sub(r"ahk_(?:id|pid|class|exe)\s+[^\s,]+", "", token, flags=re.IGNORECASE).strip()
    wanted_title = cleaned_title

    matches: list[tuple[int, int]] = []  # (hwnd, score)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        crect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(crect))
        cw = crect.right - crect.left
        ch = crect.bottom - crect.top
        # Filter out tiny helper windows / tooltips
        if cw < 30 or ch < 30:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if wanted_pid and pid.value != wanted_pid:
            return True

        cls_buf = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, cls_buf, 512)
        cur_class = cls_buf.value.strip()
        if wanted_class and wanted_class.casefold() != cur_class.casefold():
            return True

        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        cur_title = title_buf.value.strip()
        if wanted_title and wanted_title.casefold() not in cur_title.casefold():
            return True

        proc_match = False
        if wanted_exe:
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    size = wintypes.DWORD(32768)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        if Path(buffer.value).name.casefold() == wanted_exe:
                            proc_match = True
                finally:
                    kernel32.CloseHandle(handle)
            if not proc_match:
                return True
        elif not wanted_class and not wanted_title and not wanted_pid:
            return True

        # Score window relevance
        score = 10
        if cur_title:
            score += 20
        if cw * ch > 100000:
            score += 30
        if wanted_class and wanted_class.casefold() == cur_class.casefold():
            score += 50
        matches.append((int(hwnd), score))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hdesk = None
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    except Exception:
        pass

    try:
        if hdesk:
            user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
            user32.EnumDesktopWindows.restype = wintypes.BOOL
            user32.EnumDesktopWindows(hdesk, WNDENUMPROC(callback), 0)
        else:
            user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        return 0
    finally:
        if hdesk:
            try:
                user32.CloseDesktop(hdesk)
            except Exception:
                pass

    if not matches:
        return 0
    matches.sort(key=lambda item: item[1], reverse=True)
    return matches[0][0]


def capture_target_area(step: dict[str, Any]) -> tuple[np.ndarray | None, int, int, str]:
    """Capture target client area (or virtual screen) as a BGR numpy array.
    Returns (bgr_frame, base_x, base_y, description).
    """
    mode = str(step.get("region_mode") or "screen").casefold()
    win_exe = str(step.get("region_window_exe") or (step.get("click") or {}).get("window_exe") or "")
    win_token = str(step.get("region_window") or (step.get("click") or {}).get("window") or "")
    hwnd = 0
    if mode != "screen":
        hwnd = _find_target_window(win_exe, win_token)

    user32 = ctypes.windll.user32
    if hwnd and user32.IsWindow(hwnd):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        info = _get_window_info(hwnd)
        pname = info.get("exe_name") or win_exe or "창"
        cname = info.get("class_name") or ""
        hhex = f"0x{hwnd:X}"
        class_tag = f" · {cname}" if cname else ""

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
                    desc = f"클라이언트 [{pname} · HWND: {hhex}{class_tag}] · {cw}×{ch} (기준점 {bx},{by})"
                    return frame, bx, by, desc
        else:
            wrect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(wrect)):
                bx, by = int(wrect.left), int(wrect.top)
                ww = int(wrect.right - wrect.left)
                wh = int(wrect.bottom - wrect.top)
                if ww > 4 and wh > 4:
                    from opencv_search import capture_region
                    frame = capture_region(bx, by, bx + ww, by + wh)
                    desc = f"창 전체 [{pname} · HWND: {hhex}{class_tag}] · {ww}×{wh} (기준점 {bx},{by})"
                    return frame, bx, by, desc

    # Fallback to screen capture
    geom = QtCore.QRect()
    for s in QtGui.QGuiApplication.screens():
        geom = geom.united(s.geometry())
    if geom.isEmpty() or geom.width() <= 0 or geom.height() <= 0:
        l = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        t = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        w = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        h = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if w <= 0 or h <= 0:
            w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            l, t = 0, 0
        r, b = l + max(1, w), t + max(1, h)
    else:
        l, t, r, b = geom.left(), geom.top(), geom.right() + 1, geom.bottom() + 1

    from opencv_search import capture_region
    frame = capture_region(l, t, r, b)
    target_str = win_exe or win_token
    if mode != "screen" and target_str:
        desc = f"⚠️ 대상 창({target_str}) 미발견 ➔ 전체 화면 대체 · {r - l}×{b - t}"
    else:
        desc = f"전체 화면 · {r - l}×{b - t}"
    return frame, l, t, desc


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
            if res.get("is_color"):
                cnt = int(res.get("match_count", 0))
                status_text = f"[{idx + 1}] {alias} · 일치 {cnt}px {'✅' if found else '❌'}"
            else:
                status_text = f"[{idx + 1}] {alias} · {score:.0%} {'✅' if found else '❌'}"
            painter.setPen(QtGui.QColor("#FFFFFF"))
            painter.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            label_bg = QtCore.QRectF(rect.left(), max(0.0, rect.top() - 20), max(rect.width(), 140.0), 18)
            painter.fillRect(label_bg, QtGui.QColor(20, 25, 35, 210))
            painter.drawText(label_bg.adjusted(4, 0, 0, 0), QtCore.Qt.AlignVCenter, status_text)

            # Draw match center crosshair if found
            if found and "hit_x" in res and "hit_y" in res:
                hx = float(res["hit_x"]) * s
                hy = float(res["hit_y"]) * s
                cross_clr = QtGui.QColor("#22C55E")
                painter.setPen(QtGui.QPen(cross_clr, 2.0))
                painter.drawLine(QtCore.QPointF(hx - 6, hy), QtCore.QPointF(hx + 6, hy))
                painter.drawLine(QtCore.QPointF(hx, hy - 6), QtCore.QPointF(hx, hy + 6))
                painter.setBrush(QtGui.QBrush(cross_clr))
                painter.drawEllipse(QtCore.QPointF(hx, hy), 3, 3)

            # Draw hint box if missed inside region but found somewhere else on screen!
            if not found and res.get("full_found") and "full_x" in res and "full_y" in res:
                fx = float(res["full_x"]) * s
                fy = float(res["full_y"]) * s
                if res.get("is_color"):
                    fw = 32.0 * s
                    fh = 32.0 * s
                else:
                    fw = float(res.get("tmpl_w", 24)) * s
                    fh = float(res.get("tmpl_h", 24)) * s
                hint_rect = QtCore.QRectF(fx - fw / 2, fy - fh / 2, fw, fh)
                dash_pen = QtGui.QPen(QtGui.QColor("#FBBF24"), 2.0, QtCore.Qt.DashLine)
                painter.setPen(dash_pen)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(251, 191, 36, 45)))
                painter.drawRect(hint_rect)
                painter.setPen(QtGui.QColor("#FDE047"))
                painter.setFont(QtGui.QFont("Segoe UI", 8, QtGui.QFont.Bold))
                if res.get("is_color"):
                    cnt = int(res.get("full_count", 0))
                    painter.drawText(QtCore.QPointF(hint_rect.left(), max(12.0, hint_rect.top() - 4)), f"실제 색상 발견 ({cnt}px)")
                else:
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
        self._is_color_mode: bool = (
            str(step.get("action") or "") == "pixel_search"
            or bool(step.get("colors"))
            or (bool(step.get("color")) and not step.get("asset") and not step.get("assets"))
        )
        if self._is_color_mode:
            raw_colors = step.get("colors") if isinstance(step.get("colors"), list) else []
            colors = [str(c).strip() for c in raw_colors if str(c).strip()]
            primary_color = str(step.get("color") or "").strip()
            if primary_color and primary_color not in colors:
                colors.insert(0, primary_color)
            if not colors:
                colors = ["#FF0000"]
            self._colors: list[str] = list(dict.fromkeys(colors))
            self._color_tolerances: dict[str, int] = dict(step.get("color_tolerances") or {})
            fallback_tol = int(step.get("tolerance") or 10)
            for c in self._colors:
                if c not in self._color_tolerances:
                    self._color_tolerances[c] = fallback_tol
            self._color_regions: dict[str, list[int]] = dict(step.get("color_regions") or {})
            fallback_reg = step.get("search_region") or step.get("region")
            for c in self._colors:
                if c not in self._color_regions:
                    if isinstance(fallback_reg, list) and len(fallback_reg) >= 4:
                        self._color_regions[c] = list(fallback_reg[:4])
                    else:
                        self._color_regions[c] = [0, 0, 0, 0]
            self._aliases: list[str] = list(self._colors)
            self._asset_regions: dict[str, list[int]] = self._color_regions
        else:
            self._colors = []
            self._color_tolerances = {}
            self._color_regions = {}
            self._asset_regions = dict(asset_regions or step.get("asset_regions") or {})
            self._aliases = self._extract_aliases(step)

        self._color_card_labels: dict[str, dict[str, QtWidgets.QLabel]] = {}
        self._bgr_frame: np.ndarray | None = None
        self._base_x: int = 0
        self._base_y: int = 0
        self._capture_desc: str = ""
        self._test_results: dict[str, dict[str, Any]] = {}
        self._required_count: int = int(step.get("required_count") or len(self._aliases))
        self._match_condition: str = str(step.get("match_condition") or "all_matched").strip()

        win_title = "🎨 색상 검색 영역 시각화 및 실시간 화면 검사기" if self._is_color_mode else "🔍 검색 영역 시각화 및 실시간 화면 검사기"
        self.setWindowTitle(win_title)
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
        title_lbl = QtWidgets.QLabel("색상 검색 영역 실시간 검증" if self._is_color_mode else "검색 영역 실시간 검증")
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
        scroll_lbl = QtWidgets.QLabel("색상별 지정 영역 및 허용 오차 조절:" if self._is_color_mode else "이미지별 지정 영역 및 검증 결과:")
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
        hint_msg = (
            "💡 화면에서 마우스로 드래그하면 선택된 색상의 검색 영역을 직접 다시 지정할 수 있습니다."
            if self._is_color_mode
            else "💡 화면에서 마우스로 드래그하면 선택된 이미지의 검색 영역을 직접 다시 지정할 수 있습니다."
        )
        hint = QtWidgets.QLabel(hint_msg)
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
        if self._is_color_mode:
            self._evaluate_all_colors()
            return
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
            tmpl = _read_image_unicode(path)
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
            region_reason = ""

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
                rw = cr - cl
                rh = cb - ct
                if rw < tw or rh < th:
                    region_reason = f"지정 영역({rw}×{rh})이 이미지({tw}×{th})보다 작음"

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
                "reason": region_reason,
            }

        self._test_results = results

    def _evaluate_all_colors(self) -> None:
        if self._bgr_frame is None:
            return
        frame = self._bgr_frame
        fh, fw = frame.shape[:2]
        results: dict[str, dict[str, Any]] = {}

        for color_hex in self._colors:
            tol = int(self._color_tolerances.get(color_hex, self.step.get("tolerance") or 10))
            target_bgr = np.array(hex_to_bgr(color_hex), dtype=np.int16)

            reg = self._color_regions.get(color_hex)
            if not reg or len(reg) < 4 or (reg[0] == 0 and reg[1] == 0 and reg[2] == 0 and reg[3] == 0):
                reg = [0, 0, fw, fh]

            l, t, r, b = [int(x) for x in reg[:4]]
            cl = max(0, min(l, fw - 1))
            ct = max(0, min(t, fh - 1))
            cr = max(cl + 1, min(r, fw))
            cb = max(ct + 1, min(b, fh))

            crop = frame[ct:cb, cl:cr]
            diff = np.abs(crop.astype(np.int16) - target_bgr)
            mask = np.all(diff <= tol, axis=2)
            match_count = int(np.count_nonzero(mask))
            found = (match_count > 0)

            hit_x, hit_y = 0, 0
            if found:
                ys, xs = np.where(mask)
                hit_x = cl + int(xs[0])
                hit_y = ct + int(ys[0])

            # Full frame search if not found in region
            full_found = False
            full_x, full_y = 0, 0
            full_count = 0
            full_diff = np.abs(frame.astype(np.int16) - target_bgr)
            full_mask = np.all(full_diff <= tol, axis=2)
            full_count = int(np.count_nonzero(full_mask))
            if full_count > 0:
                full_found = True
                fys, fxs = np.where(full_mask)
                full_x = int(fxs[0])
                full_y = int(fys[0])

            results[color_hex] = {
                "found": found,
                "score": float(match_count),
                "threshold": tol,
                "hit_x": hit_x,
                "hit_y": hit_y,
                "match_count": match_count,
                "full_found": full_found,
                "full_x": full_x,
                "full_y": full_y,
                "full_count": full_count,
                "region": [cl, ct, cr, cb],
                "color_hex": color_hex,
                "is_color": True,
            }

        self._test_results = results

    def _refresh_ui(self) -> None:
        active = self.canvas._active_alias or (self._aliases[0] if self._aliases else "")
        self.canvas.set_data(self._asset_regions, self._test_results, active_alias=active)

        # Update Top Banner
        total = len(self._aliases)
        matched_count = sum(1 for r in self._test_results.values() if r.get("found"))
        req = self._required_count if self._required_count > 0 else total

        target_name = "색상" if self._is_color_mode else "이미지"
        if self._match_condition == "all_matched":
            is_success = (matched_count == total)
            cond_desc = f"모든 {target_name}({total}개) 일치 필요"
        elif self._match_condition == "at_least_1":
            is_success = (matched_count >= 1)
            cond_desc = "최소 1개 이상 일치 시 참"
        elif self._match_condition == "exact_n":
            is_success = (matched_count == req)
            cond_desc = f"정확히 {req}개 일치 시 참"
        else:
            is_success = (matched_count >= req)
            cond_desc = f"최소 {req}개 이상 일치 시 참"

        if is_success:
            self.banner_box.setStyleSheet("background: #064E3B; border: 1px solid #059669; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"✅ 탐지 성공! ({matched_count}/{total}개 {target_name} 일치 ➔ 참 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #34D399;")
            self.lbl_banner_sub.setText(f"조건: {cond_desc} 만족 ➔ [성공(참) 분기]로 정상 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #A7F3D0;")
        else:
            self.banner_box.setStyleSheet("background: #450A0A; border: 1px solid #DC2626; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"❌ 조건 미달 ({matched_count}/{total}개 {target_name} 일치 ➔ 거짓 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #F87171;")
            self.lbl_banner_sub.setText(f"조건: {cond_desc} (현재 {matched_count}개 일치) ➔ [실패(거짓) 분기]로 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #FECACA;")

        # Rebuild Cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._color_card_labels.clear()
        for idx, alias in enumerate(self._aliases, 1):
            if self._is_color_mode:
                card = self._build_color_card(idx, alias)
            else:
                card = self._build_image_card(idx, alias)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def _build_color_card(self, idx: int, color_hex: str) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        is_active = (color_hex == self.canvas._active_alias)
        res = self._test_results.get(color_hex, {})
        found = bool(res.get("found", False))
        match_count = int(res.get("match_count", 0))

        border_color = "#38BDF8" if is_active else ("#059669" if found else "#7F1D1D")
        bg_color = "#1E293B" if is_active else ("#14241E" if found else "#1C1618")
        card.setStyleSheet(f"QFrame {{ background: {bg_color}; border: 1.5px solid {border_color}; border-radius: 6px; padding: 6px; }}")

        vbox = QtWidgets.QVBoxLayout(card)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(4)

        # Header Row
        top_row = QtWidgets.QHBoxLayout()
        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(18, 18)
        swatch.setStyleSheet(f"background: {color_hex}; border: 1px solid #FFFFFF; border-radius: 3px;")
        top_row.addWidget(swatch)

        lbl_name = QtWidgets.QLabel(f"<b>[{idx}] {color_hex}</b>")
        lbl_name.setStyleSheet("color: #FFFFFF; font-size: 9.5pt;")
        top_row.addWidget(lbl_name)

        top_row.addStretch(1)
        lbl_status = QtWidgets.QLabel(f"일치: {match_count}px {'✅' if found else '❌'}")
        lbl_status.setStyleSheet("font-weight: 700; color: #34D399;" if found else "font-weight: 700; color: #F87171;")
        top_row.addWidget(lbl_status)
        vbox.addLayout(top_row)

        # Region Coordinates
        reg = self._color_regions.get(color_hex)
        reg_str = f"[{reg[0]}, {reg[1]}, {reg[2]}, {reg[3]}]" if (reg and len(reg) >= 4 and (reg[0] or reg[1] or reg[2] or reg[3])) else "전체 화면"
        lbl_reg = QtWidgets.QLabel(f"검색 영역: {reg_str}")
        lbl_reg.setStyleSheet("color: #94A3B8; font-size: 8.5pt;")
        vbox.addWidget(lbl_reg)

        self._color_card_labels[color_hex] = {
            "status": lbl_status,
            "region": lbl_reg,
        }

        # Tolerance Bar Widget
        cur_tol = int(self._color_tolerances.get(color_hex, 10))
        tol_bar = ColorToleranceBarWidget(color_hex, cur_tol, parent=card)
        tol_bar.valueChanged.connect(lambda val, c=color_hex: self._on_color_tolerance_changed(c, val))
        vbox.addWidget(tol_bar)

        # Missed Hint
        if not found:
            if res.get("full_found"):
                fx = int(res.get("full_x", 0))
                fy = int(res.get("full_y", 0))
                cnt = int(res.get("full_count", 0))
                hint_lbl = QtWidgets.QLabel(f"💡 화면 다른 곳에서 발견! (X: {fx}, Y: {fy}, {cnt}px)")
                hint_lbl.setStyleSheet("color: #FDE047; font-size: 8.5pt; font-weight: 600;")
                vbox.addWidget(hint_lbl)
            else:
                hint_lbl = QtWidgets.QLabel("⚠️ 화면 전체에서도 해당 색상을 찾지 못했습니다. (오차 조절 필요)")
                hint_lbl.setStyleSheet("color: #FCA5A5; font-size: 8.5pt;")
                vbox.addWidget(hint_lbl)

        # Buttons
        btn_box = QtWidgets.QHBoxLayout()
        btn_sel = QtWidgets.QPushButton("선택 (캔버스 드래그 대상)")
        btn_sel.setStyleSheet("background: #0F172A; border: 1px solid #334155; color: #70C5FF; padding: 3px 6px; border-radius: 4px; font-size: 8.5pt;")
        btn_sel.clicked.connect(lambda _, a=color_hex: self._select_active_alias(a))
        btn_box.addWidget(btn_sel)

        if not found and res.get("full_found"):
            btn_snap = QtWidgets.QPushButton("🎯 실제 위치로 맞춤")
            btn_snap.setStyleSheet("background: #065F46; border: 1px solid #059669; color: #A7F3D0; font-weight: 700; padding: 3px 6px; border-radius: 4px; font-size: 8.5pt;")
            btn_snap.clicked.connect(lambda _, a=color_hex: self._snap_single_to_actual(a))
            btn_box.addWidget(btn_snap)

        vbox.addLayout(btn_box)
        return card

    def _on_color_tolerance_changed(self, color_hex: str, val: int) -> None:
        self._color_tolerances[color_hex] = val
        self._evaluate_all_regions()
        active = self.canvas._active_alias or (self._aliases[0] if self._aliases else "")
        self.canvas.set_data(self._asset_regions, self._test_results, active_alias=active)

        # Update Banner
        total = len(self._aliases)
        matched_count = sum(1 for r in self._test_results.values() if r.get("found"))
        req = self._required_count if self._required_count > 0 else total
        if self._match_condition == "all_matched":
            is_success = (matched_count == total)
            cond_desc = f"모든 색상({total}개) 일치 필요"
        elif self._match_condition == "at_least_1":
            is_success = (matched_count >= 1)
            cond_desc = "최소 1개 이상 일치 시 참"
        elif self._match_condition == "exact_n":
            is_success = (matched_count == req)
            cond_desc = f"정확히 {req}개 일치 시 참"
        else:
            is_success = (matched_count >= req)
            cond_desc = f"최소 {req}개 이상 일치 시 참"

        if is_success:
            self.banner_box.setStyleSheet("background: #064E3B; border: 1px solid #059669; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"✅ 탐지 성공! ({matched_count}/{total}개 색상 일치 ➔ 참 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #34D399;")
            self.lbl_banner_sub.setText(f"조건: {cond_desc} 만족 ➔ [성공(참) 분기]로 정상 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #A7F3D0;")
        else:
            self.banner_box.setStyleSheet("background: #450A0A; border: 1px solid #DC2626; border-radius: 8px; padding: 8px;")
            self.lbl_banner_main.setText(f"❌ 조건 미달 ({matched_count}/{total}개 색상 일치 ➔ 거짓 판정)")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #F87171;")
            self.lbl_banner_sub.setText(f"조건: {cond_desc} (현재 {matched_count}개 일치) ➔ [실패(거짓) 분기]로 이동합니다.")
            self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #FECACA;")

        # Update card status label in place
        res = self._test_results.get(color_hex, {})
        found = bool(res.get("found", False))
        match_count = int(res.get("match_count", 0))
        if color_hex in self._color_card_labels:
            lbl_status = self._color_card_labels[color_hex].get("status")
            if lbl_status:
                lbl_status.setText(f"일치: {match_count}px {'✅' if found else '❌'}")
                lbl_status.setStyleSheet("font-weight: 700; color: #34D399;" if found else "font-weight: 700; color: #F87171;")

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

        # Row 3: Diagnostic / Hint
        if not found:
            reason = res.get("reason")
            if reason:
                hint_lbl = QtWidgets.QLabel(f"⚠️ {reason}")
                hint_lbl.setStyleSheet("color: #F87171; font-size: 8.5pt;")
                vbox.addWidget(hint_lbl)
            elif res.get("full_found"):
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
        if self._is_color_mode:
            self._color_regions[alias] = reg
        self._evaluate_all_regions()
        self._refresh_ui()

    def _shift_all_regions(self, dx: int, dy: int) -> None:
        for alias, reg in list(self._asset_regions.items()):
            if reg and len(reg) >= 4:
                self._asset_regions[alias] = [reg[0] + dx, reg[1] + dy, reg[2] + dx, reg[3] + dy]
        if self._is_color_mode:
            for k, v in self._asset_regions.items():
                self._color_regions[k] = v
        self._evaluate_all_regions()
        self._refresh_ui()

    def _adjust_margins(self, margin: int) -> None:
        for alias, reg in list(self._asset_regions.items()):
            if reg and len(reg) >= 4:
                self._asset_regions[alias] = [reg[0], max(0, reg[1] - margin), reg[2], reg[3] + margin]
        if self._is_color_mode:
            for k, v in self._asset_regions.items():
                self._color_regions[k] = v
        self._evaluate_all_regions()
        self._refresh_ui()

    def _snap_single_to_actual(self, alias: str) -> None:
        res = self._test_results.get(alias, {})
        if res.get("full_found"):
            fx = int(res["full_x"])
            fy = int(res["full_y"])
            if res.get("is_color"):
                pad_x = 30
                pad_y = 30
            else:
                tw = int(res.get("tmpl_w", 24))
                th = int(res.get("tmpl_h", 24))
                pad_x = max(16, tw)
                pad_y = max(14, th)
            self._asset_regions[alias] = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
            if self._is_color_mode:
                self._color_regions[alias] = self._asset_regions[alias]
            self._evaluate_all_regions()
            self._refresh_ui()

    def _snap_all_to_actual_matches(self) -> None:
        snapped_count = 0
        for alias in self._aliases:
            res = self._test_results.get(alias, {})
            if res.get("full_found"):
                fx = int(res["full_x"])
                fy = int(res["full_y"])
                if res.get("is_color"):
                    pad_x = 30
                    pad_y = 30
                else:
                    tw = int(res.get("tmpl_w", 24))
                    th = int(res.get("tmpl_h", 24))
                    pad_x = max(16, tw)
                    pad_y = max(14, th)
                self._asset_regions[alias] = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
                if self._is_color_mode:
                    self._color_regions[alias] = self._asset_regions[alias]
                snapped_count += 1
        if snapped_count > 0:
            self._evaluate_all_regions()
            self._refresh_ui()
            item_name = "색상" if self._is_color_mode else "이미지"
            QtWidgets.QMessageBox.information(self, "자동 맞춤 완료", f"총 {snapped_count}개 {item_name}의 영역을 실제 발견 위치로 자동 보정했습니다!")
        else:
            item_name = "색상" if self._is_color_mode else "이미지"
            QtWidgets.QMessageBox.warning(self, "자동 맞춤 불가", f"화면 전체에서 일치하는 {item_name}을(를) 찾지 못했습니다.")

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

    def get_color_regions(self) -> dict[str, list[int]]:
        return dict(self._color_regions if self._is_color_mode else self._asset_regions)

    def get_color_tolerances(self) -> dict[str, int]:
        return dict(self._color_tolerances)

    def get_tolerance(self) -> int:
        if self._color_tolerances:
            return next(iter(self._color_tolerances.values()))
        return int(self.step.get("tolerance") or 10)

    def get_search_region(self) -> list[int] | None:
        return self.get_bounding_region()

    def get_bounding_region(self) -> list[int] | None:
        valid_regs = [r for r in self._asset_regions.values() if isinstance(r, (list, tuple)) and len(r) >= 4]
        if not valid_regs:
            return None
        min_l = min(int(r[0]) for r in valid_regs)
        min_t = min(int(r[1]) for r in valid_regs)
        max_r = max(int(r[2]) for r in valid_regs)
        max_b = max(int(r[3]) for r in valid_regs)
        return [min_l, min_t, max_r, max_b]
