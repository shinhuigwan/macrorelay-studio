from __future__ import annotations

import ctypes
from ctypes import wintypes
from copy import deepcopy
import logging
import os
from pathlib import Path
import re
from datetime import datetime
from typing import Any

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .color_widgets import ColorToleranceBarWidget, ToleranceSpectrumBar
from .repository import MacroRepository
from .screen_coordinates import display_coordinate_maps, logical_point_to_native
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


_NON_TARGET_WINDOW_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
}


def _configure_window_api(user32) -> None:
    """Preserve 64-bit HWND values when ctypes has no declared prototypes."""
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL


def _configure_process_api(kernel32) -> None:
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _window_target_details(hwnd: int) -> dict[str, Any] | None:
    """Return a portable target descriptor for a top-level Win32 window.

    A live HWND is deliberately not used in the persisted selector because it
    changes after an application restart and on another computer.
    """
    if not hwnd:
        return None
    try:
        user32 = ctypes.windll.user32
        _configure_window_api(user32)
        root = int(user32.GetAncestor(int(hwnd), 2) or hwnd)  # GA_ROOT
        if not root or not user32.IsWindow(root) or not user32.IsWindowVisible(root):
            return None

        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(root, class_buffer, len(class_buffer))
        class_name = class_buffer.value
        if class_name in _NON_TARGET_WINDOW_CLASSES:
            return None

        window_rect = wintypes.RECT()
        client_rect = wintypes.RECT()
        client_origin = wintypes.POINT(0, 0)
        if not user32.GetWindowRect(root, ctypes.byref(window_rect)):
            return None
        if not user32.GetClientRect(root, ctypes.byref(client_rect)):
            return None
        if not user32.ClientToScreen(root, ctypes.byref(client_origin)):
            return None

        window_width = int(window_rect.right - window_rect.left)
        window_height = int(window_rect.bottom - window_rect.top)
        client_width = int(client_rect.right - client_rect.left)
        client_height = int(client_rect.bottom - client_rect.top)
        if window_width < 32 or window_height < 32 or client_width < 4 or client_height < 4:
            return None

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        exe_name = ""
        kernel32 = ctypes.windll.kernel32
        _configure_process_api(kernel32)
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                length = wintypes.DWORD(32768)
                path_buffer = ctypes.create_unicode_buffer(length.value)
                if kernel32.QueryFullProcessImageNameW(
                    process, 0, path_buffer, ctypes.byref(length)
                ):
                    exe_name = Path(path_buffer.value).name
            finally:
                kernel32.CloseHandle(process)
        if not exe_name:
            return None

        title_buffer = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(root, title_buffer, len(title_buffer))
        # Class + executable remains stable across restarts and other PCs.
        if class_name:
            window_token = f"ahk_class {class_name} ahk_exe {exe_name}"
        else:
            window_token = f"ahk_exe {exe_name}"
        return {
            "window": window_token,
            "exe": exe_name,
            "class": class_name,
            "title": title_buffer.value,
            "hwnd": root,
            "client_origin": [int(client_origin.x), int(client_origin.y)],
            "client_size": [client_width, client_height],
            "window_origin": [int(window_rect.left), int(window_rect.top)],
            "window_size": [window_width, window_height],
        }
    except Exception:
        return None


def _window_target_at_native_point(
    point: QtCore.QPoint,
    ignored_hwnds: set[int] | None = None,
) -> dict[str, Any] | None:
    """Find the first visible non-Studio window under a physical screen point."""
    ignored = {int(value) for value in (ignored_hwnds or set()) if value}
    try:
        user32 = ctypes.windll.user32
        _configure_window_api(user32)
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd, _lparam):
            root = int(user32.GetAncestor(hwnd, 2) or hwnd)
            if not root or root in ignored or not user32.IsWindowVisible(root):
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(root, ctypes.byref(rect)):
                return True
            if rect.left <= point.x() < rect.right and rect.top <= point.y() < rect.bottom:
                details = _window_target_details(root)
                if details is not None:
                    found.append(root)
                    return False
            return True

        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(callback, 0)
        return _window_target_details(found[0]) if found else None
    except Exception:
        return None


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
    # Use the same title/class/EXE-aware resolver as the editor and test
    # center.  The older fallback below remains for unusual desktop stations.
    from .image_search_test import _find_window

    resolved = _find_window(exe_name, window_token)
    if resolved:
        return resolved
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
    win_exe = str(step.get("region_window_exe") or (step.get("click") or {}).get("window_exe") or "")
    win_token = str(step.get("region_window") or (step.get("click") or {}).get("window") or "")
    raw_mode = step.get("region_mode")
    if not raw_mode and win_exe:
        mode = "client"
    else:
        mode = str(raw_mode or "screen").casefold()
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

    # Do not silently reinterpret client-relative regions as screen-absolute
    # regions when the intended window is gone. That produced convincing but
    # vertically shifted overlays and could target another active program.
    target_str = win_exe or win_token
    if mode != "screen" and target_str:
        desc = f"⚠️ 대상 창({target_str})을 찾지 못했습니다. 대상 프로그램을 다시 지정하세요."
        return None, 0, 0, desc

    # Screen mode capture
    geom = QtCore.QRect()
    for mapping in display_coordinate_maps():
        geom = geom.united(mapping.native)
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
        l, t, r, b = geom.left(), geom.top(), geom.x() + geom.width(), geom.y() + geom.height()

    from opencv_search import capture_region
    frame = capture_region(l, t, r, b)
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
                l = int(r.x() / s)
                t = int(r.y() / s)
                # Stored regions use an exclusive right/bottom edge everywhere.
                right = int((r.x() + r.width()) / s)
                bottom = int((r.y() + r.height()) / s)
                if self._pixmap is not None:
                    l = max(0, min(l, self._pixmap.width() - 1))
                    t = max(0, min(t, self._pixmap.height() - 1))
                    right = max(l + 1, min(right, self._pixmap.width()))
                    bottom = max(t + 1, min(bottom, self._pixmap.height()))
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
        # The visual editor is cancellable. Keep nested click/asset structures
        # isolated until the caller accepts and copies the result.
        self.step = deepcopy(step)
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
            raw_offsets = step.get("asset_offsets") if isinstance(step.get("asset_offsets"), dict) else {}
            default_off = (step.get("click") or {}).get("offset") or [0, 0]
            self._asset_offsets: dict[str, list[int]] = {}
            for a in self._aliases:
                off_val = raw_offsets.get(a)
                if isinstance(off_val, (list, tuple)) and len(off_val) >= 2:
                    self._asset_offsets[a] = [int(off_val[0] or 0), int(off_val[1] or 0)]
                elif isinstance(default_off, (list, tuple)) and len(default_off) >= 2:
                    self._asset_offsets[a] = [int(default_off[0] or 0), int(default_off[1] or 0)]
                else:
                    self._asset_offsets[a] = [0, 0]

        self._color_card_labels: dict[str, dict[str, QtWidgets.QLabel]] = {}
        self._bgr_frame: np.ndarray | None = None
        self._base_x: int = 0
        self._base_y: int = 0
        self._capture_desc: str = ""
        self._test_results: dict[str, dict[str, Any]] = {}
        self._required_count: int = int(step.get("required_count") or len(self._aliases))
        self._match_condition: str = str(step.get("match_condition") or "all_matched").strip()

        # Click settings for condition match
        raw_target = str(step.get("click_target") or "").strip().lower()
        if not raw_target:
            if bool(step.get("click_enabled")):
                if str(step.get("search_mode") or "").lower() == "all" and str(step.get("all_action") or "").lower() == "click_all":
                    raw_target = "each_image"
                elif step.get("custom_click_x") is not None and (int(step.get("custom_click_x") or 0) or int(step.get("custom_click_y") or 0)):
                    raw_target = "custom_coord"
                else:
                    raw_target = "first_image"
            elif step.get("custom_click_x") is not None and (int(step.get("custom_click_x") or 0) or int(step.get("custom_click_y") or 0)):
                raw_target = "custom_coord"
            else:
                raw_target = "each_image"
        self._click_target: str = raw_target
        self._custom_click_x: int = int(step.get("custom_click_x") or 0)
        self._custom_click_y: int = int(step.get("custom_click_y") or 0)

        # A missing target must remain screen-based until the user selects or
        # drags over the intended program. Borrowing the first macro target or
        # a running emulator made unrelated applications receive the search.
        if not self.step.get("region_window_exe"):
            self.step["region_mode"] = "screen"
            self.step["region_coords"] = "screen"

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
        btn_refresh.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; color: #70C5FF; font-weight: 700; padding: 4px 8px; border-radius: 5px;")
        btn_refresh.clicked.connect(self._do_capture_and_test)
        title_box.addWidget(btn_refresh)

        self.btn_toggle_mode = QtWidgets.QPushButton("🎯 대상 전환")
        self.btn_toggle_mode.setStyleSheet("background: #1E293B; border: 1px solid #38BDF8; color: #38BDF8; font-weight: 700; padding: 4px 8px; border-radius: 5px;")
        self.btn_toggle_mode.clicked.connect(self._toggle_target_mode)
        title_box.addWidget(self.btn_toggle_mode)
        left_layout.addLayout(title_box)

        self.lbl_target_info = QtWidgets.QLabel("대상: 확인 중…")
        self.lbl_target_info.setStyleSheet("color: #8A98B0; font-size: 9pt;")
        left_layout.addWidget(self.lbl_target_info)

        # Multi-image Management Box
        if not self._is_color_mode:
            img_tools_box = QtWidgets.QFrame()
            img_tools_box.setStyleSheet("background: #141C2E; border: 1px solid #2B3D5B; border-radius: 8px; padding: 6px;")
            img_tools_layout = QtWidgets.QVBoxLayout(img_tools_box)
            img_tools_layout.setContentsMargins(8, 8, 8, 8)
            img_tools_layout.setSpacing(6)

            # Row 1: Add buttons
            add_btn_row = QtWidgets.QHBoxLayout()
            btn_add_capture = QtWidgets.QPushButton("➕ 화면 캡처로 새 이미지 추가")
            btn_add_capture.setStyleSheet("""
                QPushButton {
                    background: #0284C7;
                    color: #FFFFFF;
                    font-weight: 800;
                    font-size: 9pt;
                    padding: 6px 10px;
                    border-radius: 5px;
                    border: 1px solid #38BDF8;
                }
                QPushButton:hover {
                    background: #0369A1;
                }
            """)
            btn_add_capture.setToolTip("화면에서 원하는 이미지 영역을 마우스로 드래그 캡처하여 즉시 멀티 이미지 서치 목록에 추가합니다.")
            btn_add_capture.clicked.connect(self._add_image_via_capture)

            btn_add_asset = QtWidgets.QPushButton("📂 보관함에서 추가")
            btn_add_asset.setStyleSheet("""
                QPushButton {
                    background: #1E293B;
                    color: #70C5FF;
                    font-weight: 700;
                    font-size: 8.8pt;
                    padding: 6px 8px;
                    border-radius: 5px;
                    border: 1px solid #3B4A6B;
                }
                QPushButton:hover {
                    background: #334155;
                    color: #FFFFFF;
                }
            """)
            btn_add_asset.setToolTip("기존 에셋 보관함에 저장된 이미지 중 하나를 선택하여 추가합니다.")
            btn_add_asset.clicked.connect(self._add_image_from_assets)

            add_btn_row.addWidget(btn_add_capture, 3)
            add_btn_row.addWidget(btn_add_asset, 2)
            img_tools_layout.addLayout(add_btn_row)

            # Row 2: Condition & Required Count
            cond_row = QtWidgets.QHBoxLayout()
            cond_lbl = QtWidgets.QLabel("일치 조건:")
            cond_lbl.setStyleSheet("color: #CBD5E1; font-weight: 700; font-size: 8.8pt;")
            cond_row.addWidget(cond_lbl)

            self.combo_condition = QtWidgets.QComboBox()
            self.combo_condition.setStyleSheet("""
                QComboBox {
                    background: #0F172A;
                    color: #E2E8F0;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 8.8pt;
                }
            """)
            self.combo_condition.addItem("모든 이미지 일치 (AND)", "all_matched")
            self.combo_condition.addItem("1개 이상 일치 (OR)", "at_least_1")
            self.combo_condition.addItem("N개 이상 일치", "at_least_n")
            self.combo_condition.addItem("정확히 N개 일치", "exact_n")

            cur_cond = self._match_condition or "all_matched"
            c_idx = self.combo_condition.findData(cur_cond)
            if c_idx >= 0:
                self.combo_condition.setCurrentIndex(c_idx)
            self.combo_condition.currentIndexChanged.connect(self._on_condition_changed)
            cond_row.addWidget(self.combo_condition, 1)

            self.spin_req_count = QtWidgets.QSpinBox()
            self.spin_req_count.setRange(1, 99)
            self.spin_req_count.setValue(max(1, self._required_count))
            self.spin_req_count.setSuffix("개")
            self.spin_req_count.setFixedWidth(65)
            self.spin_req_count.setStyleSheet("""
                QSpinBox {
                    background: #0F172A;
                    color: #38BDF8;
                    font-weight: bold;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 3px;
                }
            """)
            self.spin_req_count.setEnabled(cur_cond in {"at_least_n", "exact_n"})
            self.spin_req_count.valueChanged.connect(self._on_req_count_changed)
            cond_row.addWidget(self.spin_req_count)

            img_tools_layout.addLayout(cond_row)

            # Row 3: Click Target & Custom Coords
            click_row = QtWidgets.QHBoxLayout()
            click_lbl = QtWidgets.QLabel("클릭 동작:")
            click_lbl.setStyleSheet("color: #CBD5E1; font-weight: 700; font-size: 8.8pt;")
            click_row.addWidget(click_lbl)

            self.combo_click_target = QtWidgets.QComboBox()
            self.combo_click_target.setStyleSheet("""
                QComboBox {
                    background: #0F172A;
                    color: #E2E8F0;
                    border: 1px solid #334155;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 8.8pt;
                }
            """)
            self.combo_click_target.addItem("발견된 각 이미지 클릭 (오프셋 적용)", "each_image")
            self.combo_click_target.addItem("첫 번째 발견 이미지 클릭", "first_image")
            self.combo_click_target.addItem("지정 좌표 클릭 (고정 위치)", "custom_coord")
            self.combo_click_target.addItem("클릭 안 함 (조건 분기만)", "none")

            t_idx = self.combo_click_target.findData(self._click_target)
            if t_idx >= 0:
                self.combo_click_target.setCurrentIndex(t_idx)
            self.combo_click_target.currentIndexChanged.connect(self._on_click_target_changed)
            click_row.addWidget(self.combo_click_target, 1)
            img_tools_layout.addLayout(click_row)

            # Row 4: Custom coord spinboxes (visible only when custom_coord is selected)
            self.custom_coord_row = QtWidgets.QHBoxLayout()
            self.lbl_coord_desc = QtWidgets.QLabel("지정 좌표:")
            self.lbl_coord_desc.setStyleSheet("color: #A5B4FC; font-weight: 600; font-size: 8.5pt;")
            self.custom_coord_row.addWidget(self.lbl_coord_desc)

            self.spin_click_x = QtWidgets.QSpinBox()
            self.spin_click_x.setRange(-100_000, 100_000)
            self.spin_click_x.setValue(self._custom_click_x)
            self.spin_click_x.setPrefix("X: ")
            self.spin_click_x.setSuffix(" px")
            self.spin_click_x.setStyleSheet("background: #0F172A; color: #A5B4FC; border: 1px solid #334155; border-radius: 4px; padding: 2px;")
            self.spin_click_x.valueChanged.connect(lambda v: setattr(self, "_custom_click_x", int(v)))

            self.spin_click_y = QtWidgets.QSpinBox()
            self.spin_click_y.setRange(-100_000, 100_000)
            self.spin_click_y.setValue(self._custom_click_y)
            self.spin_click_y.setPrefix("Y: ")
            self.spin_click_y.setSuffix(" px")
            self.spin_click_y.setStyleSheet("background: #0F172A; color: #A5B4FC; border: 1px solid #334155; border-radius: 4px; padding: 2px;")
            self.spin_click_y.valueChanged.connect(lambda v: setattr(self, "_custom_click_y", int(v)))

            self.custom_coord_row.addWidget(self.spin_click_x)
            self.custom_coord_row.addWidget(self.spin_click_y)

            btn_pick_coord = QtWidgets.QPushButton("⌖ 찍기")
            btn_pick_coord.setStyleSheet("background: #312E81; border: 1px solid #4F46E5; color: #C7D2FE; font-weight: bold; border-radius: 4px; padding: 2px 6px; font-size: 8.5pt;")
            btn_pick_coord.setToolTip("화면에서 원하는 위치를 마우스 좌클릭하여 지정 클릭 좌표를 즉시 설정합니다.")
            btn_pick_coord.clicked.connect(self._pick_custom_click_point)
            self.custom_coord_row.addWidget(btn_pick_coord)

            self.custom_coord_widget = QtWidgets.QWidget()
            self.custom_coord_widget.setLayout(self.custom_coord_row)
            self.custom_coord_widget.setVisible(self._click_target == "custom_coord")
            img_tools_layout.addWidget(self.custom_coord_widget)

            left_layout.addWidget(img_tools_box)

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

        if hasattr(self, "btn_toggle_mode"):
            mode = str(self.step.get("region_mode") or "screen").casefold()
            if mode == "client":
                self.btn_toggle_mode.setText("🖥️ 전체 화면 전환")
                self.btn_toggle_mode.setToolTip("현재 선택한 프로그램의 클라이언트 영역을 캡처 중입니다. 클릭하면 모니터 전체 화면 모드로 전환합니다.")
            elif mode == "window":
                self.btn_toggle_mode.setText("🖥️ 전체 화면 전환")
                self.btn_toggle_mode.setToolTip("현재 선택한 프로그램 창 전체를 캡처 중입니다. 클릭하면 모니터 전체 화면 모드로 전환합니다.")
            else:
                self.btn_toggle_mode.setText("🎯 프로그램 선택")
                self.btn_toggle_mode.setToolTip("대상 프로그램을 클릭해 선택합니다. 이미지나 검색 영역을 드래그해도 그 아래 프로그램이 자동 지정됩니다.")

        if frame is None or frame.size == 0:
            self.lbl_banner_main.setText("❌ 화면 캡처 실패")
            self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #F87171;")
            self.lbl_banner_sub.setText("대상 창을 찾지 못했거나 화면 캡처 권한이 없습니다.")
            return

        self.canvas.set_frame(frame)
        self._evaluate_all_regions()
        self._refresh_ui()

    def _uses_screen_coordinates(self) -> bool:
        mode = str(self.step.get("region_mode") or "screen").casefold()
        coords = str(self.step.get("region_coords") or "").casefold()
        return mode == "screen" or coords == "screen"

    def _region_to_frame(self, region: list[int] | tuple[int, ...]) -> list[int]:
        """Convert persisted native-screen coordinates to capture-frame coordinates."""
        values = [int(value) for value in region[:4]]
        if self._uses_screen_coordinates():
            return [
                values[0] - self._base_x,
                values[1] - self._base_y,
                values[2] - self._base_x,
                values[3] - self._base_y,
            ]
        return values

    def _region_from_frame(self, region: list[int] | tuple[int, ...]) -> list[int]:
        """Convert capture-frame coordinates to the persisted coordinate system."""
        values = [int(value) for value in region[:4]]
        if self._uses_screen_coordinates():
            return [
                values[0] + self._base_x,
                values[1] + self._base_y,
                values[2] + self._base_x,
                values[3] + self._base_y,
            ]
        return values

    def _canvas_regions(self) -> dict[str, list[int]]:
        return {
            alias: self._region_to_frame(region)
            for alias, region in self._asset_regions.items()
            if isinstance(region, (list, tuple)) and len(region) >= 4
        }

    def _ignored_window_roots(self) -> set[int]:
        roots: set[int] = set()
        try:
            user32 = ctypes.windll.user32
            _configure_window_api(user32)
            for widget in QtWidgets.QApplication.topLevelWidgets():
                try:
                    hwnd = int(widget.winId())
                    root = int(user32.GetAncestor(hwnd, 2) or hwnd)
                    if root:
                        roots.add(root)
                except Exception:
                    pass
        except Exception:
            pass
        return roots

    def _target_at_native_rect(self, rect: QtCore.QRect) -> dict[str, Any] | None:
        if not rect.isValid():
            return None
        return _window_target_at_native_point(rect.center(), self._ignored_window_roots())

    def _apply_window_target(self, target: dict[str, Any], *, refresh: bool = True) -> bool:
        """Rebase stored regions and bind this node to the detected client."""
        exe_name = str(target.get("exe") or "").strip()
        window_token = str(target.get("window") or "").strip()
        origin = target.get("client_origin")
        size = target.get("client_size")
        if not exe_name or not window_token or not isinstance(origin, (list, tuple)) or len(origin) < 2:
            return False
        if not isinstance(size, (list, tuple)) or len(size) < 2:
            return False

        new_x, new_y = int(origin[0]), int(origin[1])
        new_w, new_h = max(1, int(size[0])), max(1, int(size[1]))
        old_is_screen = self._uses_screen_coordinates()
        old_x, old_y = int(self._base_x), int(self._base_y)
        converted: dict[str, list[int]] = {}
        for alias, value in self._asset_regions.items():
            if not isinstance(value, (list, tuple)) or len(value) < 4:
                continue
            screen = [int(part) for part in value[:4]]
            if not old_is_screen:
                screen = [screen[0] + old_x, screen[1] + old_y, screen[2] + old_x, screen[3] + old_y]
            relative = [
                max(0, min(new_w, screen[0] - new_x)),
                max(0, min(new_h, screen[1] - new_y)),
                max(0, min(new_w, screen[2] - new_x)),
                max(0, min(new_h, screen[3] - new_y)),
            ]
            if relative[2] > relative[0] and relative[3] > relative[1]:
                converted[str(alias)] = relative
            else:
                # A region belonging to another window cannot be reused safely.
                converted[str(alias)] = [0, 0, new_w, new_h]

        self._asset_regions.update(converted)
        if self._is_color_mode:
            self._color_regions = self._asset_regions
        self.step["region_window"] = window_token
        self.step["region_window_exe"] = exe_name
        self.step["region_mode"] = "client"
        self.step["region_coords"] = "relative"
        click = self.step.get("click")
        if isinstance(click, dict):
            click["window"] = window_token
            click["window_exe"] = exe_name
        self._base_x, self._base_y = new_x, new_y
        if refresh:
            self._do_capture_and_test()
        return True

    def _pick_program_target(self) -> None:
        """Let the user click any program and bind the search to that client."""
        from .action_editor import CoordinatePickerDialog

        original_opacity = self.windowOpacity()
        self.setWindowOpacity(0.0)
        QtWidgets.QApplication.processEvents()
        picker: CoordinatePickerDialog | None = None
        try:
            picker = CoordinatePickerDialog(parent=None)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return
            native_point = logical_point_to_native(picker.point)
            target = _window_target_at_native_point(native_point, self._ignored_window_roots())
            if target is None or not self._apply_window_target(target):
                QtWidgets.QMessageBox.warning(
                    self,
                    "프로그램 감지 실패",
                    "선택한 위치에서 일반 프로그램 창을 찾지 못했습니다. 대상 창 내부를 다시 클릭해주세요.",
                )
        finally:
            if picker is not None:
                picker.deleteLater()
            self.setWindowOpacity(original_opacity if original_opacity > 0 else 1.0)
            self.show()
            self.raise_()
            self.activateWindow()

    def _toggle_target_mode(self) -> None:
        """Toggle full screen or choose the actual program under a click."""
        cur_mode = str(self.step.get("region_mode") or "screen").casefold()
        old_bx, old_by = self._base_x, self._base_y

        if cur_mode in {"client", "window"}:
            self.step["region_mode"] = "screen"
            self.step["region_coords"] = "screen"
            for alias, reg in list(self._asset_regions.items()):
                if isinstance(reg, (list, tuple)) and len(reg) >= 4 and (reg[0] or reg[1] or reg[2] or reg[3]):
                    self._asset_regions[alias] = [reg[0] + old_bx, reg[1] + old_by, reg[2] + old_bx, reg[3] + old_by]
            self._do_capture_and_test()
        else:
            self._pick_program_target()

    def _on_condition_changed(self) -> None:
        if hasattr(self, "combo_condition"):
            self._match_condition = str(self.combo_condition.currentData() or "all_matched")
        if hasattr(self, "spin_req_count"):
            self.spin_req_count.setEnabled(self._match_condition in {"at_least_n", "exact_n"})
        self._refresh_banner_only()

    def _on_req_count_changed(self, val: int) -> None:
        self._required_count = int(val)
        self._refresh_banner_only()

    def _on_click_target_changed(self) -> None:
        if hasattr(self, "combo_click_target"):
            self._click_target = str(self.combo_click_target.currentData() or "each_image")
        if hasattr(self, "custom_coord_widget"):
            self.custom_coord_widget.setVisible(self._click_target == "custom_coord")

    def _pick_custom_click_point(self) -> None:
        from .action_editor import OffsetPointPickerDialog
        orig_opacity = self.windowOpacity()
        self.setWindowOpacity(0.0)
        QtCore.QThread.msleep(150)
        QtWidgets.QApplication.processEvents()
        picker: OffsetPointPickerDialog | None = None
        try:
            picker = OffsetPointPickerDialog(single_click=True)
            if picker.exec() == QtWidgets.QDialog.Accepted:
                pt = picker.offset()
                if pt and len(pt) >= 2:
                    if self._base_x or self._base_y:
                        pt_x = pt[0] - self._base_x
                        pt_y = pt[1] - self._base_y
                    else:
                        pt_x = pt[0]
                        pt_y = pt[1]
                    self._custom_click_x = pt_x
                    self._custom_click_y = pt_y
                    if hasattr(self, "spin_click_x"):
                        self.spin_click_x.setValue(pt_x)
                    if hasattr(self, "spin_click_y"):
                        self.spin_click_y.setValue(pt_y)
        finally:
            if picker is not None:
                picker.deleteLater()
            self.setWindowOpacity(orig_opacity)
            self.show()
            self.raise_()
            self.activateWindow()

    def _add_image_via_capture(self) -> None:
        from .image_editor import ScreenCaptureDialog, capture_virtual_desktop
        orig_opacity = self.windowOpacity()
        self.setWindowOpacity(0.0)
        QtCore.QThread.msleep(150)
        QtWidgets.QApplication.processEvents()

        picker: ScreenCaptureDialog | None = None
        try:
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                QtWidgets.QMessageBox.warning(self, "캡처 실패", "가상 데스크톱 화면을 캡처하지 못했습니다.")
                return

            hint_text = "추가할 이미지 대상을 마우스로 드래그 선택 후 Enter (Esc 취소)"
            picker = ScreenCaptureDialog(pixmap, geometry, parent=None, hint_text=hint_text)
            if picker.exec() == QtWidgets.QDialog.Accepted:
                image = picker.captured_image()
                screen_rect = picker.selected_native_screen_rect()
                if image.isNull() or not screen_rect.isValid() or screen_rect.width() < 4 or screen_rect.height() < 4:
                    return

                idx = len(self._aliases) + 1
                alias = f"multi-img-{idx}-{datetime.now():%H%M%S}"

                try:
                    self.repository.add_asset_image(image, alias)
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "저장 오류", f"이미지 에셋 저장 중 오류 발생: {e}")
                    return

                # Bind the node to the actual program underneath the dragged
                # capture. This works for browsers, desktop apps and emulators;
                # no hard-coded player name or transient HWND is persisted.
                target = self._target_at_native_rect(screen_rect)
                if target is not None:
                    self._apply_window_target(target, refresh=False)

                # Persist either native-screen or target-relative coordinates,
                # matching region_mode/region_coords used by the runtime.
                if not self._uses_screen_coordinates():
                    rel_left = screen_rect.left() - self._base_x
                    rel_top = screen_rect.top() - self._base_y
                else:
                    rel_left = screen_rect.left()
                    rel_top = screen_rect.top()
                rel_right = rel_left + screen_rect.width()
                rel_bottom = rel_top + screen_rect.height()

                # Add a margin around captured object for flexible search (+20px)
                if self._uses_screen_coordinates():
                    # Negative coordinates are valid on monitors positioned to
                    # the left or above the primary monitor.
                    reg = [rel_left - 20, rel_top - 20, rel_right + 20, rel_bottom + 20]
                else:
                    target_size = target.get("client_size") if target else None
                    max_w = int(target_size[0]) if isinstance(target_size, (list, tuple)) and len(target_size) >= 2 else None
                    max_h = int(target_size[1]) if isinstance(target_size, (list, tuple)) and len(target_size) >= 2 else None
                    reg = [
                        max(0, rel_left - 20),
                        max(0, rel_top - 20),
                        min(max_w, rel_right + 20) if max_w is not None else rel_right + 20,
                        min(max_h, rel_bottom + 20) if max_h is not None else rel_bottom + 20,
                    ]

                if alias not in self._aliases:
                    self._aliases.append(alias)
                self._asset_regions[alias] = reg
                self._asset_offsets[alias] = [0, 0]
                self.canvas.set_active_alias(alias)

                if hasattr(self, "spin_req_count"):
                    self.spin_req_count.setMaximum(max(1, len(self._aliases)))

                self._do_capture_and_test()
        finally:
            if picker is not None:
                picker.deleteLater()
            self.setWindowOpacity(orig_opacity)
            self.show()
            self.raise_()
            self.activateWindow()

    def _add_image_from_assets(self) -> None:
        assets = self.repository.load_assets()
        existing = set(self._aliases)
        candidates = [k for k in sorted(assets.keys()) if k not in existing]
        if not candidates:
            QtWidgets.QMessageBox.information(
                self,
                "에셋 없음",
                "추가할 수 있는 새 에셋이 없습니다.\n[➕ 화면 캡처로 새 이미지 추가] 버튼으로 직접 캡처하세요."
            )
            return

        selected, ok = QtWidgets.QInputDialog.getItem(
            self,
            "에셋 보관함에서 이미지 추가",
            "추가할 에셋을 선택하세요:",
            candidates,
            0,
            False,
        )
        if ok and selected:
            if selected not in self._aliases:
                self._aliases.append(selected)
            if self._bgr_frame is not None:
                fh, fw = self._bgr_frame.shape[:2]
                self._asset_regions[selected] = [0, 0, fw, fh]
            else:
                self._asset_regions[selected] = [0, 0, 0, 0]
            self._asset_offsets[selected] = [0, 0]
            self.canvas.set_active_alias(selected)
            if hasattr(self, "spin_req_count"):
                self.spin_req_count.setMaximum(max(1, len(self._aliases)))
            self._evaluate_all_regions()
            self._refresh_ui()

    def _remove_image_alias(self, alias: str) -> None:
        if alias in self._aliases:
            self._aliases.remove(alias)
        self._asset_regions.pop(alias, None)
        self._asset_offsets.pop(alias, None)
        self._test_results.pop(alias, None)
        if self.canvas._active_alias == alias:
            self.canvas.set_active_alias(self._aliases[0] if self._aliases else "")
        if hasattr(self, "spin_req_count"):
            self.spin_req_count.setMaximum(max(1, len(self._aliases)))
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

            l, t, r, b = self._region_to_frame(reg)
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

            l, t, r, b = self._region_to_frame(reg)
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

    def _refresh_banner_only(self) -> None:
        total = len(self._aliases)
        if total == 0:
            return
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

    def _refresh_ui(self) -> None:
        active = self.canvas._active_alias or (self._aliases[0] if self._aliases else "")
        self.canvas.set_data(self._canvas_regions(), self._test_results, active_alias=active)

        total = len(self._aliases)
        if total == 0:
            self.banner_box.setStyleSheet("background: #1E293B; border: 1px solid #3B4A6B; border-radius: 8px; padding: 8px;")
            if not self._is_color_mode:
                self.lbl_banner_main.setText("ℹ️ 등록된 검색 이미지가 없습니다")
                self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #38BDF8;")
                self.lbl_banner_sub.setText("상단의 [➕ 화면 캡처로 새 이미지 추가] 버튼을 눌러 탐색할 이미지들을 등록하세요.")
                self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #94A3B8;")
            else:
                self.lbl_banner_main.setText("ℹ️ 등록된 색상이 없습니다")
                self.lbl_banner_main.setStyleSheet("font-size: 11pt; font-weight: 800; color: #38BDF8;")
                self.lbl_banner_sub.setText("검색할 색상을 추가하세요.")
                self.lbl_banner_sub.setStyleSheet("font-size: 9pt; color: #94A3B8;")

            while self.cards_layout.count():
                item = self.cards_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            empty_card = QtWidgets.QFrame()
            empty_card.setStyleSheet("background: #141C2E; border: 1px dashed #2B3D5B; border-radius: 8px; padding: 16px;")
            ec_layout = QtWidgets.QVBoxLayout(empty_card)
            ec_lbl = QtWidgets.QLabel("🖼️ <b>등록된 이미지가 없습니다.</b><br><br>상단의 <b>[➕ 화면 캡처로 새 이미지 추가]</b> 버튼을 누르고 게임/창 화면에서 찾고자 하는 이미지 영역을 드래그하여 등록하세요.<br><br>여러 이미지를 등록하여 일치 조건을 자유롭게 설정할 수 있습니다.")
            ec_lbl.setStyleSheet("color: #94A3B8; font-size: 9pt; line-height: 1.4;")
            ec_lbl.setWordWrap(True)
            ec_layout.addWidget(ec_lbl)
            self.cards_layout.addWidget(empty_card)
            self.cards_layout.addStretch(1)
            return

        self._refresh_banner_only()

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
        self.canvas.set_data(self._canvas_regions(), self._test_results, active_alias=active)

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

        # Row 1: Index, Thumbnail, Alias, Status
        top_row = QtWidgets.QHBoxLayout()

        # Asset thumbnail
        path = self.repository.asset_path(alias)
        if path and Path(path).is_file():
            pix = QtGui.QPixmap(str(path))
            if not pix.isNull():
                thumb = QtWidgets.QLabel()
                thumb.setFixedSize(30, 30)
                thumb.setPixmap(pix.scaled(30, 30, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                thumb.setStyleSheet("background: #0B0E14; border: 1px solid #334155; border-radius: 4px; padding: 1px;")
                top_row.addWidget(thumb)

        name_lbl = QtWidgets.QLabel(f"<b>[{idx}] {alias}</b>")
        name_lbl.setStyleSheet("color: #FFFFFF; font-size: 9.5pt;")
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

        # Row 2.5: Individual Click Offset (X, Y)
        cur_off = self._asset_offsets.get(alias) or [0, 0]
        off_row = QtWidgets.QHBoxLayout()
        off_lbl = QtWidgets.QLabel("클릭 오프셋:")
        off_lbl.setStyleSheet("color: #CBD5E1; font-size: 8.5pt; font-weight: 600;")
        off_row.addWidget(off_lbl)

        off_x_spin = QtWidgets.QSpinBox()
        off_x_spin.setRange(-2000, 2000)
        off_x_spin.setValue(int(cur_off[0]))
        off_x_spin.setPrefix("X: ")
        off_x_spin.setSuffix(" px")
        off_x_spin.setToolTip(f"'{alias}' 발견 시 클릭할 가로(X) 상대 위치 오프셋 (중심 기준)")
        off_x_spin.setStyleSheet("background: #0B0E14; color: #38BDF8; border: 1px solid #334155; border-radius: 4px; padding: 1px 4px; font-size: 8.5pt;")
        off_x_spin.valueChanged.connect(lambda val, a=alias: self._set_asset_offset(a, 0, val))
        off_row.addWidget(off_x_spin)

        off_y_spin = QtWidgets.QSpinBox()
        off_y_spin.setRange(-2000, 2000)
        off_y_spin.setValue(int(cur_off[1]))
        off_y_spin.setPrefix("Y: ")
        off_y_spin.setSuffix(" px")
        off_y_spin.setToolTip(f"'{alias}' 발견 시 클릭할 세로(Y) 상대 위치 오프셋 (중심 기준)")
        off_y_spin.setStyleSheet("background: #0B0E14; color: #38BDF8; border: 1px solid #334155; border-radius: 4px; padding: 1px 4px; font-size: 8.5pt;")
        off_y_spin.valueChanged.connect(lambda val, a=alias: self._set_asset_offset(a, 1, val))
        off_row.addWidget(off_y_spin)
        vbox.addLayout(off_row)

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

        btn_del = QtWidgets.QPushButton("🗑️ 삭제")
        btn_del.setStyleSheet("""
            QPushButton {
                background: #450A0A;
                border: 1px solid #991B1B;
                color: #FCA5A5;
                padding: 3px 8px;
                border-radius: 4px;
                font-size: 8.5pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #991B1B;
                color: #FFFFFF;
            }
        """)
        btn_del.clicked.connect(lambda _, a=alias: self._remove_image_alias(a))
        btn_box.addWidget(btn_del)

        vbox.addLayout(btn_box)
        return card

    def _set_asset_offset(self, alias: str, axis: int, value: int) -> None:
        if alias not in self._asset_offsets:
            self._asset_offsets[alias] = [0, 0]
        self._asset_offsets[alias][axis] = int(value)

    def _select_active_alias(self, alias: str) -> None:
        self.canvas.set_active_alias(alias)
        self._refresh_ui()

    def _on_canvas_region_dragged(self, alias: str, reg: list[int]) -> None:
        # A drag on the full-desktop canvas also identifies the real program
        # underneath that area and rebases all regions to its client origin.
        if self._uses_screen_coordinates() and len(reg) >= 4:
            native_rect = QtCore.QRect(
                int(reg[0]) + self._base_x,
                int(reg[1]) + self._base_y,
                max(1, int(reg[2]) - int(reg[0])),
                max(1, int(reg[3]) - int(reg[1])),
            )
            target = self._target_at_native_rect(native_rect)
            if target is not None and self._apply_window_target(target, refresh=False):
                origin = target.get("client_origin") or [0, 0]
                size = target.get("client_size") or [1, 1]
                stored_reg = [
                    max(0, min(int(size[0]), native_rect.x() - int(origin[0]))),
                    max(0, min(int(size[1]), native_rect.y() - int(origin[1]))),
                    max(0, min(int(size[0]), native_rect.x() + native_rect.width() - int(origin[0]))),
                    max(0, min(int(size[1]), native_rect.y() + native_rect.height() - int(origin[1]))),
                ]
                self._asset_regions[alias] = stored_reg
                if self._is_color_mode:
                    self._color_regions[alias] = stored_reg
                self._do_capture_and_test()
                return
        stored_reg = self._region_from_frame(reg)
        self._asset_regions[alias] = stored_reg
        if self._is_color_mode:
            self._color_regions[alias] = stored_reg
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
            local_reg = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
            self._asset_regions[alias] = self._region_from_frame(local_reg)
            if self._is_color_mode:
                self._color_regions[alias] = self._asset_regions[alias]
            self._evaluate_all_regions()
            self._refresh_ui()

    def _snap_all_to_actual_matches(self) -> None:
        if not self._aliases:
            QtWidgets.QMessageBox.information(self, "알림", "등록된 대상(이미지/색상)이 없습니다.")
            return
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
                local_reg = [max(0, fx - pad_x), max(0, fy - pad_y), fx + pad_x, fy + pad_y]
                self._asset_regions[alias] = self._region_from_frame(local_reg)
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

    def get_aliases(self) -> list[str]:
        return list(self._aliases)

    def get_match_condition(self) -> str:
        return self._match_condition

    def get_required_count(self) -> int:
        return self._required_count

    def get_asset_offsets(self) -> dict[str, list[int]]:
        return {k: list(v) for k, v in self._asset_offsets.items()}

    def get_click_target(self) -> str:
        return self._click_target

    def get_custom_click_coords(self) -> tuple[int, int]:
        return (self._custom_click_x, self._custom_click_y)

    def accept(self) -> None:
        self.setWindowOpacity(1.0)
        p = self.parent()
        if p is not None and hasattr(p, "setEnabled"):
            p.setEnabled(True)
            if hasattr(p, "activateWindow"):
                p.activateWindow()
        super().accept()

    def reject(self) -> None:
        self.setWindowOpacity(1.0)
        p = self.parent()
        if p is not None and hasattr(p, "setEnabled"):
            p.setEnabled(True)
            if hasattr(p, "activateWindow"):
                p.activateWindow()
        super().reject()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.setWindowOpacity(1.0)
        p = self.parent()
        if p is not None and hasattr(p, "setEnabled"):
            p.setEnabled(True)
            if hasattr(p, "activateWindow"):
                p.activateWindow()
        super().closeEvent(event)
