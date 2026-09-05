from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .action_editor import ActionEditor, CoordinatePickerDialog, WindowPickerDialog, action_template
from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop
from .node_editor import ACTION_TITLES
from .theme import COLORS
from .widgets import WheelSafeSpinBox


def configure_success_candidates(
    steps: list[dict[str, Any]], source: int, candidates: list[int]
) -> list[int]:
    """Configure an ordered fan-out from one success port.

    The source enters the first candidate. A failed candidate advances to the
    next one, while a successful candidate keeps its own success flow. Original
    failure settings are saved so removing the fan-out is reversible.
    """
    if not 0 < source <= len(steps):
        return []
    source_step = steps[source - 1]
    previous = source_step.get("success_candidates") or []
    if isinstance(previous, list):
        for value in previous:
            try:
                target = int(value)
            except (TypeError, ValueError):
                continue
            if not 0 < target <= len(steps):
                continue
            candidate = steps[target - 1]
            automation = candidate.get("_automation")
            if not isinstance(automation, dict) or int(automation.get("success_candidate_source") or 0) != source:
                continue
            if automation.pop("candidate_original_fail_present", False):
                candidate["on_fail"] = int(automation.pop("candidate_original_on_fail", 0) or 0)
            else:
                candidate.pop("on_fail", None)
                automation.pop("candidate_original_on_fail", None)
            if automation.pop("candidate_original_abort_present", False):
                candidate["abort_on_fail"] = bool(automation.pop("candidate_original_abort_on_fail", False))
            else:
                candidate.pop("abort_on_fail", None)
                automation.pop("candidate_original_abort_on_fail", None)
            for key in ("success_candidate_source", "success_candidate_position", "success_candidate_count"):
                automation.pop(key, None)
            if automation:
                candidate["_automation"] = automation
            else:
                candidate.pop("_automation", None)

    normalized: list[int] = []
    for value in candidates:
        try:
            target = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < target <= len(steps) and target != source and target not in normalized:
            normalized.append(target)
    if not normalized:
        source_step.pop("success_candidates", None)
        source_step.pop("on_success", None)
        return []
    source_step["on_success"] = normalized[0]
    if len(normalized) == 1:
        source_step.pop("success_candidates", None)
        return normalized

    source_step["success_candidates"] = normalized
    for position, target in enumerate(normalized):
        candidate = steps[target - 1]
        automation = candidate.get("_automation") if isinstance(candidate.get("_automation"), dict) else {}
        automation["candidate_original_fail_present"] = "on_fail" in candidate
        automation["candidate_original_on_fail"] = int(candidate.get("on_fail") or 0)
        automation["candidate_original_abort_present"] = "abort_on_fail" in candidate
        automation["candidate_original_abort_on_fail"] = bool(candidate.get("abort_on_fail", False))
        automation.update(
            {
                "success_candidate_source": source,
                "success_candidate_position": position + 1,
                "success_candidate_count": len(normalized),
            }
        )
        candidate["_automation"] = automation
        if position + 1 < len(normalized):
            candidate["on_fail"] = normalized[position + 1]
            candidate["abort_on_fail"] = False
    return normalized


def configure_fail_candidates(
    steps: list[dict[str, Any]], source: int, candidates: list[int]
) -> list[int]:
    """Configure an ordered fan-out from one fail port."""
    if not 0 < source <= len(steps):
        return []
    source_step = steps[source - 1]
    previous = source_step.get("fail_candidates") or []
    if isinstance(previous, list):
        for value in previous:
            try:
                target = int(value)
            except (TypeError, ValueError):
                continue
            if not 0 < target <= len(steps):
                continue
            candidate = steps[target - 1]
            automation = candidate.get("_automation")
            if not isinstance(automation, dict) or int(automation.get("fail_candidate_source") or 0) != source:
                continue
            if automation.pop("fail_candidate_original_present", False):
                candidate["on_fail"] = int(automation.pop("fail_candidate_original_on_fail", 0) or 0)
            else:
                candidate.pop("on_fail", None)
            for key in ("fail_candidate_source", "fail_candidate_position", "fail_candidate_count"):
                automation.pop(key, None)
            if automation:
                candidate["_automation"] = automation
            else:
                candidate.pop("_automation", None)

    normalized: list[int] = []
    for value in candidates:
        try:
            target = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < target <= len(steps) and target != source and target not in normalized:
            normalized.append(target)
    if not normalized:
        source_step.pop("fail_candidates", None)
        source_step.pop("on_fail", None)
        return []
    source_step["on_fail"] = normalized[0]
    if len(normalized) == 1:
        source_step.pop("fail_candidates", None)
        return normalized

    source_step["fail_candidates"] = normalized
    for position, target in enumerate(normalized):
        candidate = steps[target - 1]
        automation = candidate.get("_automation") if isinstance(candidate.get("_automation"), dict) else {}
        automation["fail_candidate_original_present"] = "on_fail" in candidate
        automation["fail_candidate_original_on_fail"] = int(candidate.get("on_fail") or 0)
        automation.update(
            {
                "fail_candidate_source": source,
                "fail_candidate_position": position + 1,
                "fail_candidate_count": len(normalized),
            }
        )
        candidate["_automation"] = automation
        if position + 1 < len(normalized):
            candidate["on_fail"] = normalized[position + 1]
            candidate["abort_on_fail"] = False
    return normalized


def _resolve_live_client_rect(window: dict[str, Any]) -> QtCore.QRect:
    """Resolve current client rectangle for live search region mapping."""
    if sys.platform != "win32" or not window:
        return QtCore.QRect()
    try:
        from .image_search_test import _find_window

        saved_hwnd = int(window.get("hwnd") or 0)
        user32 = ctypes.windll.user32
        hwnd = saved_hwnd if saved_hwnd and user32.IsWindow(saved_hwnd) else _find_window(
            str(window.get("exe") or ""), _window_token(window)
        )
        if not hwnd or not user32.IsWindow(hwnd):
            return QtCore.QRect()
        rect = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return QtCore.QRect(pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        pass
    return QtCore.QRect()


def _window_label(window: dict[str, Any]) -> str:
    return str(window.get("exe") or window.get("title") or "대상 창")


def _window_token(window: dict[str, Any]) -> str:
    exe = str(window.get("exe") or "").strip()
    title = str(window.get("title") or "").strip()
    window_class = str(window.get("class") or "").strip()
    # Program name alone is ambiguous for apps such as KakaoTalk where the
    # main window and several chat windows share one executable.
    if title and exe:
        return f"{title} ahk_exe {exe}"
    if window_class and exe:
        return f"ahk_class {window_class} ahk_exe {exe}"
    if exe:
        return f"ahk_exe {exe}"
    return title or "A"


def load_recording(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("type") in {
            "mouse", "mouse_drag", "key", "screen_condition", "screen_verification", "wait_marker"
        }:
            events.append(payload)
    return sorted(events, key=lambda item: int(item.get("t") or 0))


def recording_drafts(events: list[dict[str, Any]], include_waits: bool = True) -> list[dict[str, Any]]:
    """Compress raw hook events into editable, meaningful automation actions."""
    drafts: list[dict[str, Any]] = []
    # A drag starts as a mouse-down event and is only known to be a drag when
    # the button is released.  Suppress that source click so one gesture never
    # becomes both a click node and a drag node.
    drag_source_ids = {
        str(event.get("source_event_id") or "")
        for event in events
        if event.get("type") == "mouse_drag" and str(event.get("source_event_id") or "")
    }
    index = 0
    previous_time = 0
    while index < len(events):
        event = events[index]
        current_time = int(event.get("t") or 0)
        gap = current_time - previous_time
        record_mode = "branch" if str(event.get("record_mode") or "action").lower() == "branch" else "action"
        workflow = {
            "workflow_id": str(event.get("workflow_id") or ""),
            "workflow_index": int(event.get("workflow_index") or 1),
        }
        # Branch-mode F8 captures open an editor, so their timestamp gaps are
        # UI editing time rather than intentional macro waits. Keep those
        # candidates contiguous while still preserving their real actions.
        if (
            include_waits
            and drafts
            and gap >= 900
            and record_mode != "branch"
            and event.get("type") != "wait_marker"
        ):
            drafts.append(
                {
                    "kind": "wait",
                    "record_mode": record_mode,
                    "t": previous_time,
                    "duration": min(5000, max(300, gap - 180)),
                    "detail": f"화면 반응 대기 {min(5000, max(300, gap - 180))} ms",
                    **workflow,
                }
            )
        if event.get("type") == "wait_marker":
            duration = max(100, int(event.get("duration") or 1000))
            drafts.append(
                {
                    "kind": "wait",
                    "record_mode": record_mode,
                    "t": current_time,
                    "duration": duration,
                    "detail": f"단축키 대기 {duration} ms",
                    "event": event,
                    **workflow,
                }
            )
            previous_time = current_time
            index += 1
            continue

        event_type = str(event.get("type") or "")
        if event_type in {
            "wait", "datetime_condition", "browser_action", "ocr", "table_store",
            "table_copy", "table_paste", "table_excel_read", "table_excel_write",
            "set_var", "calc_var", "coord_mode", "call_submacro", "flow_control",
            "text_condition", "run_program", "terminate_program", "type_text",
            "pixel_search", "ocr_tracking", "multi_pixel_check", "wait_color", "color_ratio", "find_text_click"
        }:
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            title = ACTION_TITLES.get(event_type, event_type)
            drafts.append(
                {
                    "kind": event_type,
                    "t": current_time,
                    "event": event,
                    "count": 1,
                    "strategy": "custom",
                    "record_mode": record_mode,
                    "detail": str(event.get("detail") or title),
                    "target": _window_label(window),
                    **workflow,
                }
            )
            previous_time = current_time
            index += 1
            continue

        if event.get("type") in {"screen_condition", "screen_verification"}:
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            drafts.append(
                {
                    "kind": "screen_verification",
                    "t": current_time,
                    "event": event,
                    "count": 1,
                    "strategy": "image",
                    "record_mode": record_mode,
                    "detail": "화면 이미지 확인 · 없으면 정지",
                    "source_control": str(event.get("source_control") or ("F5" if event.get("type") == "screen_verification" else "RightClick")),
                    "target": _window_label(window),
                    **workflow,
                }
            )
            previous_time = current_time
            index += 1
            continue
        if event.get("type") == "mouse" and str(event.get("event_id") or "") in drag_source_ids:
            index += 1
            continue
        if event.get("type") == "mouse_drag":
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            start = event.get("from_screen") if isinstance(event.get("from_screen"), list) else [0, 0]
            end = event.get("to_screen") if isinstance(event.get("to_screen"), list) else [0, 0]
            drafts.append(
                {
                    "kind": "mouse_drag",
                    "t": current_time,
                    "event": event,
                    "count": 1,
                    "strategy": "window",
                    "record_mode": record_mode,
                    "detail": (
                        f"{event.get('button', 'Left')} 드래그 · "
                        f"{int(start[0] or 0)}, {int(start[1] or 0)} → {int(end[0] or 0)}, {int(end[1] or 0)}"
                    ),
                    "target": _window_label(window),
                    **workflow,
                }
            )
            previous_time = current_time
            index += 1
            continue
        if event.get("type") == "mouse":
            count = 1
            if index + 1 < len(events):
                following = events[index + 1]
                if (
                    following.get("type") == "mouse"
                    and str(following.get("record_mode") or "action").lower() == record_mode
                    and str(following.get("workflow_id") or "") == str(event.get("workflow_id") or "")
                    and following.get("button") == event.get("button")
                    and abs(int(following.get("x") or 0) - int(event.get("x") or 0)) <= 4
                    and abs(int(following.get("y") or 0) - int(event.get("y") or 0)) <= 4
                    and int(following.get("t") or 0) - current_time <= 460
                ):
                    count = 2
                    index += 1
                    current_time = int(following.get("t") or current_time)
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            button_name = str(event.get("button") or "Left")
            if button_name in {"WheelUp", "WheelDown"}:
                count = max(count, max(1, abs(int(event.get("wheel_delta") or 120)) // 120))
                detail = f"{'휠 위' if button_name == 'WheelUp' else '휠 아래'} · {count}칸"
            else:
                detail = f"{button_name} {'더블 클릭' if count == 2 else '클릭'} · {event.get('x')}, {event.get('y')}"
            draft = {
                    "kind": "mouse",
                    "t": current_time,
                    "event": event,
                    "count": count,
                    "strategy": "window",
                    "record_mode": record_mode,
                    "detail": detail,
                    "target": _window_label(window),
                    **workflow,
                }
            if isinstance(event.get("_handle_profile"), dict):
                draft["handle_profile"] = dict(event["_handle_profile"])
            if event.get("_review_multi_group"):
                draft["_review_multi_group"] = str(event["_review_multi_group"])
            drafts.append(draft)
            previous_time = current_time
            index += 1
            continue

        if event.get("type") == "capture":
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            size = event.get("image_sample_size") if isinstance(event.get("image_sample_size"), list) else [0, 0]
            draft = {
                    "kind": "image_capture",
                    "t": current_time,
                    "event": event,
                    "count": 1,
                    "strategy": "image",
                    "record_mode": record_mode,
                    "detail": f"직접 지정 이미지 · {int(size[0] or 0)}×{int(size[1] or 0)}",
                    "target": _window_label(window),
                    **workflow,
                }
            if isinstance(event.get("_handle_profile"), dict):
                draft["handle_profile"] = dict(event["_handle_profile"])
            if event.get("_review_multi_group"):
                draft["_review_multi_group"] = str(event["_review_multi_group"])
            drafts.append(draft)
            previous_time = current_time
            index += 1
            continue

        window = event.get("window") if isinstance(event.get("window"), dict) else {}
        group: list[dict[str, Any]] = []
        last_time = current_time
        while index < len(events):
            key_event = events[index]
            key_window = key_event.get("window") if isinstance(key_event.get("window"), dict) else {}
            if key_event.get("type") != "key" or int(key_event.get("t") or 0) - last_time > 1100:
                break
            if str(key_event.get("record_mode") or "action").lower() != record_mode:
                break
            if int(key_window.get("hwnd") or 0) != int(window.get("hwnd") or 0):
                break
            group.append(key_event)
            last_time = int(key_event.get("t") or last_time)
            index += 1
        if not group:
            # Forward-compatible guard for recorder controls unknown to this
            # Studio build. Never leave the draft compressor stuck on one row.
            index += 1
            previous_time = current_time
            continue
        text = ""
        segments: list[tuple[str, str]] = []
        for key_event in group:
            token = str(key_event.get("token") or "")
            character = str(key_event.get("char") or "")
            if token == "Backspace":
                text = text[:-1]
            elif character:
                text += character
            elif token:
                if text:
                    segments.append(("text", text))
                    text = ""
                segments.append(("key", token))
        if text:
            segments.append(("text", text))
        for segment_kind, value in segments:
            drafts.append(
                {
                    "kind": segment_kind,
                    "record_mode": record_mode,
                    "t": last_time,
                    "text": value if segment_kind == "text" else "",
                    "token": value if segment_kind == "key" else "",
                    "window": window,
                    "detail": f"텍스트 입력 · {len(value)}자" if segment_kind == "text" else f"키 입력 · {value}",
                    "target": _window_label(window),
                    "event": group[0],
                    **workflow,
                }
            )
        previous_time = last_time
    return drafts


class PixelColorPickerDialog(QtWidgets.QDialog):
    """Fullscreen overlay with magnifier lens for precise pixel color extraction."""

    def __init__(self, pixmap: QtGui.QPixmap, geometry: QtCore.QRect, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        )
        self.setGeometry(geometry)
        self.setCursor(QtCore.Qt.CrossCursor)
        self._source = pixmap
        self._geometry = geometry
        self._selected_color: QtGui.QColor | None = None
        self._selected_point: QtCore.QPoint | None = None
        self._mouse_pos = QtGui.QCursor.pos()
        self._zoom = 8
        self._lens_size = 140  # pixel size of magnifier square
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")

    def selected_color(self) -> QtGui.QColor | None:
        return self._selected_color

    def selected_point(self) -> QtCore.QPoint | None:
        return self._selected_point

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._mouse_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            screen_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            img = self._source.toImage()
            local_x = screen_pos.x() - self._geometry.left()
            local_y = screen_pos.y() - self._geometry.top()
            if 0 <= local_x < img.width() and 0 <= local_y < img.height():
                self._selected_color = img.pixelColor(local_x, local_y)
                self._selected_point = screen_pos
            self.accept()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # Draw dimmed background
        painter.drawPixmap(0, 0, self._source.scaled(self.size(), QtCore.Qt.KeepAspectRatioByExpanding))

        # Semi-transparent dark overlay
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 60))

        screen_pos = self._mouse_pos
        local_x = screen_pos.x() - self._geometry.left()
        local_y = screen_pos.y() - self._geometry.top()
        img = self._source.toImage()

        # Magnifier lens position — offset from cursor to avoid overlap
        lens_x = screen_pos.x() - self._geometry.left() + 20
        lens_y = screen_pos.y() - self._geometry.top() - self._lens_size - 20
        # Ensure lens stays on screen
        if lens_y < 10:
            lens_y = screen_pos.y() - self._geometry.top() + 20
        if lens_x + self._lens_size + 10 > self.width():
            lens_x = screen_pos.x() - self._geometry.left() - self._lens_size - 20

        lens_rect = QtCore.QRect(lens_x, lens_y, self._lens_size, self._lens_size)

        # Draw zoomed pixels
        sample_radius = self._lens_size // (self._zoom * 2)
        src_rect = QtCore.QRect(
            max(0, local_x - sample_radius),
            max(0, local_y - sample_radius),
            sample_radius * 2,
            sample_radius * 2,
        )
        if src_rect.right() > img.width():
            src_rect.setRight(img.width())
        if src_rect.bottom() > img.height():
            src_rect.setBottom(img.height())

        if src_rect.width() > 0 and src_rect.height() > 0:
            zoomed = self._source.copy(src_rect).scaled(
                self._lens_size, self._lens_size, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.FastTransformation
            )
            # Draw lens border
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#0D1017"))
            painter.drawRoundedRect(lens_rect.adjusted(-3, -3, 3, 3), 8, 8)
            painter.drawPixmap(lens_rect.topLeft(), zoomed)

        # Draw crosshair on center of lens
        center_x = lens_rect.center().x()
        center_y = lens_rect.center().y()
        pixel_size = self._zoom
        cross_pen = QtGui.QPen(QtGui.QColor("#FF4D6A"), 1.5)
        painter.setPen(cross_pen)
        painter.drawRect(center_x - pixel_size // 2, center_y - pixel_size // 2, pixel_size, pixel_size)
        painter.drawLine(center_x, lens_rect.top() + 4, center_x, center_y - pixel_size)
        painter.drawLine(center_x, center_y + pixel_size, center_x, lens_rect.bottom() - 4)
        painter.drawLine(lens_rect.left() + 4, center_y, center_x - pixel_size, center_y)
        painter.drawLine(center_x + pixel_size, center_y, lens_rect.right() - 4, center_y)

        # Draw current color info
        if 0 <= local_x < img.width() and 0 <= local_y < img.height():
            color = img.pixelColor(local_x, local_y)
            hex_str = color.name().upper()
            r, g, b = color.red(), color.green(), color.blue()

            # Color info box below lens
            info_rect = QtCore.QRect(lens_rect.left(), lens_rect.bottom() + 4, self._lens_size, 44)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(13, 16, 23, 230))
            painter.drawRoundedRect(info_rect, 6, 6)

            # Color swatch
            swatch = QtCore.QRect(info_rect.left() + 6, info_rect.top() + 6, 32, 32)
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#3B455C"), 1))
            painter.drawRoundedRect(swatch, 4, 4)

            # HEX text
            painter.setPen(QtGui.QColor("#FFFFFF"))
            font = painter.font()
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QtCore.QRect(swatch.right() + 8, info_rect.top() + 4, 100, 20),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                hex_str,
            )
            font.setPointSize(8)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("#8A98B0"))
            painter.drawText(
                QtCore.QRect(swatch.right() + 8, info_rect.top() + 22, 100, 16),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                f"R:{r}  G:{g}  B:{b}",
            )

        # Top hint text
        hint = "[ 색상 추출 ] 좌클릭으로 색상 선택 · ESC 취소"
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        hint_rect = painter.fontMetrics().boundingRect(hint)
        hint_bg = QtCore.QRect(
            (self.width() - hint_rect.width()) // 2 - 16,
            12,
            hint_rect.width() + 32,
            hint_rect.height() + 14,
        )
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(13, 16, 23, 210))
        painter.drawRoundedRect(hint_bg, 8, 8)
        painter.setPen(QtGui.QColor("#FFFFFF"))
class MultiPixelPickerDialog(QtWidgets.QDialog):
    """Fullscreen overlay with magnifier lens for extracting multiple pixel colors sequentially."""

    def __init__(self, pixmap: QtGui.QPixmap, geometry: QtCore.QRect, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
        )
        self.setGeometry(geometry)
        self.setCursor(QtCore.Qt.CrossCursor)
        self._source = pixmap
        self._geometry = geometry
        self._points: list[dict[str, Any]] = []
        self._mouse_pos = QtGui.QCursor.pos()
        self._zoom = 8
        self._lens_size = 140
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")

    def selected_points(self) -> list[dict[str, Any]]:
        return self._points

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._mouse_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            screen_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            img = self._source.toImage()
            local_x = screen_pos.x() - self._geometry.left()
            local_y = screen_pos.y() - self._geometry.top()
            if 0 <= local_x < img.width() and 0 <= local_y < img.height():
                color = img.pixelColor(local_x, local_y)
                self._points.append({
                    "x": screen_pos.x(),
                    "y": screen_pos.y(),
                    "color": color.name().upper(),
                    "tolerance": 10,
                })
                self.update()
        elif event.button() == QtCore.Qt.RightButton:
            if self._points:
                self._points.pop()
                self.update()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self._points:
                self.accept()
            else:
                self.reject()
        elif event.key() == QtCore.Qt.Key_Escape:
            self.reject()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.drawPixmap(0, 0, self._source.scaled(self.size(), QtCore.Qt.KeepAspectRatioByExpanding))
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 60))

        # Draw already marked pin badges
        for idx, pt in enumerate(self._points, start=1):
            px = pt["x"] - self._geometry.left()
            py = pt["y"] - self._geometry.top()
            clr = QtGui.QColor(pt["color"])
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2))
            painter.setBrush(clr)
            painter.drawEllipse(QtCore.QPoint(px, py), 12, 12)
            painter.setPen(QtGui.QColor("#000000" if clr.lightness() > 128 else "#FFFFFF"))
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QtCore.QRect(px - 12, py - 12, 24, 24), QtCore.Qt.AlignCenter, str(idx))

        screen_pos = self._mouse_pos
        local_x = screen_pos.x() - self._geometry.left()
        local_y = screen_pos.y() - self._geometry.top()
        img = self._source.toImage()

        lens_x = screen_pos.x() - self._geometry.left() + 20
        lens_y = screen_pos.y() - self._geometry.top() - self._lens_size - 20
        if lens_y < 10:
            lens_y = screen_pos.y() - self._geometry.top() + 20
        if lens_x + self._lens_size + 10 > self.width():
            lens_x = screen_pos.x() - self._geometry.left() - self._lens_size - 20

        lens_rect = QtCore.QRect(lens_x, lens_y, self._lens_size, self._lens_size)

        sample_radius = self._lens_size // (self._zoom * 2)
        src_rect = QtCore.QRect(
            max(0, local_x - sample_radius),
            max(0, local_y - sample_radius),
            sample_radius * 2,
            sample_radius * 2,
        )
        if src_rect.right() > img.width():
            src_rect.setRight(img.width())
        if src_rect.bottom() > img.height():
            src_rect.setBottom(img.height())

        if src_rect.width() > 0 and src_rect.height() > 0:
            zoomed = self._source.copy(src_rect).scaled(
                self._lens_size, self._lens_size, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.FastTransformation
            )
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#0D1017"))
            painter.drawRoundedRect(lens_rect.adjusted(-3, -3, 3, 3), 8, 8)
            painter.drawPixmap(lens_rect.topLeft(), zoomed)

        center_x = lens_rect.center().x()
        center_y = lens_rect.center().y()
        pixel_size = self._zoom
        cross_pen = QtGui.QPen(QtGui.QColor("#FF4D6A"), 1.5)
        painter.setPen(cross_pen)
        painter.drawRect(center_x - pixel_size // 2, center_y - pixel_size // 2, pixel_size, pixel_size)
        painter.drawLine(center_x, lens_rect.top() + 4, center_x, center_y - pixel_size)
        painter.drawLine(center_x, center_y + pixel_size, center_x, lens_rect.bottom() - 4)
        painter.drawLine(lens_rect.left() + 4, center_y, center_x - pixel_size, center_y)
        painter.drawLine(center_x + pixel_size, center_y, lens_rect.right() - 4, center_y)

        if 0 <= local_x < img.width() and 0 <= local_y < img.height():
            color = img.pixelColor(local_x, local_y)
            hex_str = color.name().upper()
            info_rect = QtCore.QRect(lens_rect.left(), lens_rect.bottom() + 4, self._lens_size, 38)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(13, 16, 23, 230))
            painter.drawRoundedRect(info_rect, 6, 6)

            swatch = QtCore.QRect(info_rect.left() + 6, info_rect.top() + 6, 26, 26)
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#3B455C"), 1))
            painter.drawRoundedRect(swatch, 4, 4)

            painter.setPen(QtGui.QColor("#FFFFFF"))
            font = painter.font()
            font.setPointSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QtCore.QRect(swatch.right() + 6, info_rect.top() + 4, 90, 16),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                hex_str,
            )
            font.setPointSize(7)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QtGui.QColor("#8A98B0"))
            painter.drawText(
                QtCore.QRect(swatch.right() + 6, info_rect.top() + 20, 90, 14),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                f"{len(self._points)}개 선택됨",
            )

        hint = f"[ 다중 픽셀 선택 ] 좌클릭: 핀 추가 ({len(self._points)}개) · 우클릭: 취소 · Enter: 완료 · ESC: 취소"
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        hint_rect = painter.fontMetrics().boundingRect(hint)
        hint_bg = QtCore.QRect(
            (self.width() - hint_rect.width()) // 2 - 16,
            12,
            hint_rect.width() + 32,
            hint_rect.height() + 14,
        )
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(13, 16, 23, 220))
        painter.drawRoundedRect(hint_bg, 8, 8)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(hint_bg, QtCore.Qt.AlignCenter, hint)
        painter.end()


class DraggableIconWrapper(QtWidgets.QWidget):
    clicked = QtCore.Signal(str)

    def __init__(self, kind: str, icon_char: str, label: str, accent_color: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.icon_char = icon_char
        self.label_text = label
        self.accent_color = accent_color
        self._press_pos = QtCore.QPoint()
        self.setFixedSize(74, 58)
        self.setStyleSheet("background: transparent; border: none;")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        self.icon_btn = QtWidgets.QLabel(icon_char)
        self.icon_btn.setAlignment(QtCore.Qt.AlignCenter)
        self.icon_btn.setFixedSize(38, 38)
        self.icon_btn.setStyleSheet(f"""
            QLabel {{
                background: #141820;
                color: {accent_color};
                border: 1.5px solid #2B354A;
                border-radius: 10px;
                font-size: 11pt;
                font-weight: 700;
            }}
            QLabel:hover {{
                background: #1E2436;
                border: 1.5px solid {accent_color};
                color: #FFFFFF;
            }}
        """)

        self.name_label = QtWidgets.QLabel(label)
        self.name_label.setAlignment(QtCore.Qt.AlignCenter)
        self.name_label.setStyleSheet("color: #8A98B0; font-size: 7pt; font-weight: 500; background: transparent; border: none;")

        layout.addWidget(self.icon_btn, 0, QtCore.Qt.AlignCenter)
        layout.addWidget(self.name_label, 0, QtCore.Qt.AlignCenter)
        self.setToolTip(f"'{label}' 노드 추가 (마우스로 끌어서 순서 변경 가능)")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            if (event.pos() - self._press_pos).manhattanLength() > 8:
                drag = QtGui.QDrag(self)
                mime = QtCore.QMimeData()
                mime.setData("application/x-macrostudio-nodeicon", self.kind.encode("utf-8"))
                drag.setMimeData(mime)
                pixmap = self.grab()
                drag.setPixmap(pixmap)
                drag.setHotSpot(event.pos())
                drag.exec(QtCore.Qt.MoveAction)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            if (event.pos() - self._press_pos).manhattanLength() <= 8:
                self.clicked.emit(self.kind)
        super().mouseReleaseEvent(event)


class ReorderableIconContainer(QtWidgets.QWidget):
    reordered = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.layout_box = QtWidgets.QHBoxLayout(self)
        self.layout_box.setContentsMargins(2, 2, 2, 2)
        self.layout_box.setSpacing(6)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-macrostudio-nodeicon"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-macrostudio-nodeicon"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat("application/x-macrostudio-nodeicon"):
            event.ignore()
            return
        kind = event.mimeData().data("application/x-macrostudio-nodeicon").data().decode("utf-8")
        drop_x = event.position().toPoint().x()
        target_idx = 0
        for i in range(self.layout_box.count()):
            item = self.layout_box.itemAt(i)
            w = item.widget() if item else None
            if w and drop_x > w.geometry().center().x():
                target_idx = i + 1

        source_widget = None
        for i in range(self.layout_box.count()):
            item = self.layout_box.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableIconWrapper) and w.kind == kind:
                source_widget = w
                break

        if source_widget:
            self.layout_box.removeWidget(source_widget)
            if target_idx > self.layout_box.count():
                target_idx = self.layout_box.count()
            self.layout_box.insertWidget(target_idx, source_widget)
            self.reordered.emit()
            event.acceptProposedAction()


class IconNodeToolbar(QtWidgets.QFrame):
    node_clicked = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background: #11151F; border: 1px solid #232B3E; border-radius: 8px; }"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(4)

        header_layout = QtWidgets.QHBoxLayout()
        header_title = QtWidgets.QLabel("노드 빠른 추가 | 아이콘을 클릭하면 선택한 노드가 현재 녹화 흐름에 추가됩니다. (아이콘을 드래그해 순서를 바꿀 수 있습니다)")
        header_title.setStyleSheet("font-size: 8.5pt; font-weight: 700; color: #8A98B0;")
        header_layout.addWidget(header_title)
        header_layout.addStretch(1)
        layout.addLayout(header_layout)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.scroll.setFixedHeight(74)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:horizontal { height: 6px; background: #0D1017; border-radius: 3px; }"
            "QScrollBar::handle:horizontal { background: #2F3B52; border-radius: 3px; }"
            "QScrollBar::handle:horizontal:hover { background: #4D9FFF; }"
        )
        self.scroll.installEventFilter(self)
        if self.scroll.viewport():
            self.scroll.viewport().installEventFilter(self)

        self.container = ReorderableIconContainer()
        self.container.setStyleSheet("background: transparent;")
        self.container.installEventFilter(self)
        self.container.reordered.connect(self._save_order)

        specs = [
            ("ocr", "OCR", "OCR 인식", "#7C6CFF"),
            ("type_text", "T", "텍스트 입력", "#C47CFF"),
            ("wait", "◷", "대기", "#F5B942"),
            ("datetime_condition", "📅", "날짜·시간 조건", "#F5B942"),
            ("browser_action", "⟨/⟩", "브라우저 요소", "#35C89A"),
            ("table_store", "▦", "테이블 저장", "#24B8C7"),
            ("table_copy", "⧉", "테이블 복사", "#24B8C7"),
            ("table_paste", "⇲", "테이블 붙이기", "#24B8C7"),
            ("set_var", "{ }", "변수 설정", "#C47CFF"),
            ("calc_var", "∑", "변수 계산", "#C47CFF"),
            ("text_condition", "Ab", "텍스트 조건", "#F06A78"),
            ("run_program", "▶", "프로그램 실행", "#35C89A"),
            ("terminate_program", "■", "프로그램 종료", "#F06A78"),
            ("pixel_search", "◈", "색상 서치", "#FF6B9D"),
            ("multi_pixel_check", "❖", "다중 픽셀", "#FF6B9D"),
            ("wait_color", "⏳", "색상 대기", "#F5B942"),
            ("color_ratio", "📊", "색상 비율", "#FF6B9D"),
            ("ocr_tracking", "⊕", "OCR 추적", "#4D9FFF"),
            ("find_text_click", "🎯", "텍스트 클릭", "#4D9FFF"),
        ]

        saved_order = QtCore.QSettings("TodayNews", "MacroStudio").value("icon_node_toolbar_order")
        if isinstance(saved_order, list) and saved_order:
            order_map = {str(k): idx for idx, k in enumerate(saved_order)}
            specs.sort(key=lambda s: order_map.get(s[0], 999))

        for kind, icon_char, label, accent_color in specs:
            wrapper = DraggableIconWrapper(kind, icon_char, label, accent_color)
            wrapper.installEventFilter(self)
            wrapper.clicked.connect(self.node_clicked.emit)
            self.container.layout_box.addWidget(wrapper)

        self.container.layout_box.addStretch(1)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

    def _save_order(self) -> None:
        order = []
        for i in range(self.container.layout_box.count()):
            item = self.container.layout_box.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableIconWrapper):
                order.append(w.kind)
        QtCore.QSettings("TodayNews", "MacroStudio").setValue("icon_node_toolbar_order", order)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Wheel and isinstance(event, QtGui.QWheelEvent):
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.angleDelta().x()
            if delta != 0:
                h_bar = self.scroll.horizontalScrollBar()
                step = -int(delta / 120 * 80) if abs(delta) >= 120 else (-80 if delta > 0 else 80)
                h_bar.setValue(h_bar.value() + step)
                return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        if delta != 0:
            h_bar = self.scroll.horizontalScrollBar()
            step = -int(delta / 120 * 80) if abs(delta) >= 120 else (-80 if delta > 0 else 80)
            h_bar.setValue(h_bar.value() + step)
            event.accept()
            return
        super().wheelEvent(event)


class RecordingBar(QtWidgets.QDialog):
    stop_requested = QtCore.Signal()
    capture_requested = QtCore.Signal(dict)
    branch_requested = QtCore.Signal()
    events_deleted = QtCore.Signal(list)
    manual_node_requested = QtCore.Signal(str)

    def __init__(self, repository=None, parent=None) -> None:
        if isinstance(repository, QtWidgets.QWidget) and parent is None:
            parent = repository
            repository = getattr(parent, "repository", None)
        super().__init__(parent)
        self.repository = repository
        self.elapsed = QtCore.QElapsedTimer()
        self.elapsed.start()
        self.gate_active = False
        self.record_mode = "action"
        self.setWindowTitle("스마트 녹화")
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(1080, 680)
        self.setMinimumSize(780, 480)
        self.workflow_index = 1
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        controls = QtWidgets.QHBoxLayout()
        self.dot = QtWidgets.QLabel("●")
        self.dot.setStyleSheet("color:#697386; font-size:18pt;")
        self.mode_badge = QtWidgets.QLabel("일반 액션")
        self.mode_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.mode_badge.setFixedWidth(82)
        self.label = QtWidgets.QLabel("2초 후 준비 · ` 시작/정지 · F4 서치영역추출 · F5 확인 · F8 캡처 · F10 종료")
        self.label.setStyleSheet("font-weight:700;")
        self.capture_button = QtWidgets.QPushButton("▣ 이미지 캡처  F8")
        self.capture_button.setToolTip("화면 위에서 붓처럼 드래그해 이미지 서치 원본 영역을 지정합니다.")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.capture_requested.emit)
        self.region_capture_button = QtWidgets.QPushButton("🎯 서치영역추출  F4")
        self.region_capture_button.setToolTip("이미지 원본과 빠른 서치 영역을 함께 연속으로 지정합니다.")
        self.region_capture_button.setEnabled(False)
        self.region_capture_button.clicked.connect(lambda: self.capture_requested.emit({"with_search_region": True, "vk": 0x73}))
        self.branch_button = QtWidgets.QPushButton("⑂ 다음 작업  F7")
        self.branch_button.setToolTip("현재 작업을 끝내고 독립된 새 작업 분기 녹화를 시작합니다.")
        self.branch_button.setVisible(False)
        self.branch_button.clicked.connect(self.branch_requested.emit)
        stop = QtWidgets.QPushButton("■ 종료  F10")
        stop.clicked.connect(self.stop_requested.emit)
        controls.addWidget(self.dot)
        controls.addWidget(self.mode_badge)
        controls.addWidget(self.label, 1)
        controls.addWidget(self.branch_button)
        controls.addWidget(self.region_capture_button)
        controls.addWidget(self.capture_button)
        controls.addWidget(stop)
        layout.addLayout(controls)
        shortcuts = QtWidgets.QLabel(
            "F4 서치영역추출 · F5 결과 확인 · F6 1초 대기 · F7 새 작업 · F8 이미지 · F9 시작 · F10 종료 · "
            "F11 일반/분기 · F12 기록 ON/OFF · 우클릭=F5와 같은 화면 확인 · Del 키=선택 노드 제거"
        )
        shortcuts.setObjectName("Muted")
        shortcuts.setWordWrap(True)
        layout.addWidget(shortcuts)
        from .node_editor import NodeCanvas

        self.live_canvas = NodeCanvas(self)
        self.live_canvas.flow_label.setText("SMART RECORDING · LIVE NODE MAP")
        self.live_canvas.setMinimumHeight(280)
        self.live_canvas.link_requested.connect(self._connect_live_nodes)
        self.live_canvas.edge_delete_requested.connect(self._delete_live_edge)
        self.live_canvas.node_delete_requested.connect(self._delete_live_node)
        self.live_canvas.inspector_requested.connect(self._open_live_node_editor)
        self.live_canvas.multi_image_merge_requested.connect(self._merge_live_image_nodes)
        self.live_canvas.set_macro({"steps": []})
        layout.addWidget(self.live_canvas, 1)

        self.delete_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Delete, self)
        self.delete_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self.delete_shortcut.activated.connect(self._delete_selected_live_nodes)

        self.icon_toolbar = IconNodeToolbar(self)
        self.icon_toolbar.node_clicked.connect(self._on_icon_node_clicked)
        layout.addWidget(self.icon_toolbar)

        self._event_positions: dict[str, list[float]] = {}
        self._last_live_signature: tuple[str, ...] = ()
        self._live_events: list[dict[str, Any]] = []
        self._live_link_overrides: dict[tuple[str, str], str | list[str] | None] = {}
        self._live_multi_groups: dict[str, str] = {}
        self._live_rebuild_pending = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.set_record_mode("action")

    def _on_icon_node_clicked(self, kind: str) -> None:
        if kind == "image_capture":
            self.capture_requested.emit({})
        elif kind == "search_region":
            self.capture_requested.emit({"with_search_region": True, "vk": 0x73})
        elif kind == "branch":
            self.branch_requested.emit()
        else:
            self.manual_node_requested.emit(kind)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if getattr(self, "_restore_position", None) is not None:
            self.move(self._restore_position)
            self._restore_position = None
        else:
            QtCore.QTimer.singleShot(0, self._position_next_to_studio)

    def _position_next_to_studio(self) -> None:
        """Attach the recorder to the Studio edge while keeping it on-screen."""
        host = self.parentWidget()
        if host is None:
            self._position_at_cursor()
            return
        host_rect = host.frameGeometry()
        screen = QtGui.QGuiApplication.screenAt(host_rect.center()) or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = self.frameGeometry().width()
        height = self.frameGeometry().height()
        right_x = host_rect.right() + 1
        left_x = host_rect.left() - width
        if right_x + width - 1 <= available.right():
            x = right_x
        elif left_x >= available.left():
            x = left_x
        else:
            x = min(max(available.left(), right_x), available.right() - width + 1)
        y = min(max(available.top(), host_rect.top()), available.bottom() - height + 1)
        self.move(x, y)

    def _position_at_cursor(self) -> None:
        cursor_pos = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(cursor_pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        dialog_size = self.frameGeometry().size()
        x = min(max(available.left() + 10, cursor_pos.x() - dialog_size.width() // 2), available.right() - dialog_size.width() - 10)
        y = min(max(available.top() + 10, cursor_pos.y() - dialog_size.height() // 2), available.bottom() - dialog_size.height() - 10)
        self.move(x, y)

    def _delete_live_node(self, index: int) -> None:
        drafts = self._group_live_drafts(recording_drafts(self._live_events, include_waits=False))
        if not 0 < index <= len(drafts):
            return
        target_draft = drafts[index - 1]
        member_ids = [str(vid) for vid in target_draft.get("_member_event_ids") or [] if str(vid)]
        event = target_draft.get("event") if isinstance(target_draft.get("event"), dict) else {}
        event_id = str(event.get("event_id") or "")
        if event_id and event_id not in member_ids:
            member_ids.append(event_id)
        if member_ids:
            self.events_deleted.emit(member_ids)

    def _delete_selected_live_nodes(self) -> None:
        """선택된 노드들을 일괄 삭제합니다."""
        selected = self.live_canvas.selected_indexes()
        if selected:
            for idx in sorted(selected, reverse=True):
                self._delete_live_node(idx)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self._delete_selected_live_nodes()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_live_node_editor(self, index: int) -> None:
        """라이브 노드맵에서 노드 더블클릭 시 상세 설정 창(ActionEditorDialog)을 엽니다."""
        drafts = self._group_live_drafts(recording_drafts(self._live_events, include_waits=False))
        if not 0 < index <= len(drafts):
            return
        target_draft = drafts[index - 1]
        event = target_draft.get("event") if isinstance(target_draft.get("event"), dict) else {}

        from .action_editor import ActionEditorDialog

        step = dict(target_draft.get("step") or {})
        if not step:
            kind = str(target_draft.get("kind") or "")
            action_type = "type_text" if kind in {"text", "key", "type_text"} else ("ocr" if kind == "find_text_click" else kind)
            if kind in {"mouse", "image_capture"}:
                action_type = "image_search"
            elif kind in {"screen_verification", "screen_condition"}:
                action_type = "screen_condition"
            elif kind == "mouse_drag":
                action_type = "inactive_click"

            step = {
                "action": action_type,
                "label": str(target_draft.get("detail") or kind),
            }
            if kind == "wait":
                step["duration"] = int(target_draft.get("duration") or event.get("duration") or 500)
            elif kind in {"text", "type_text"}:
                step["text"] = str(target_draft.get("text") or event.get("text") or "")
            elif kind == "key":
                step["text"] = "{" + str(target_draft.get("token") or event.get("token") or "") + "}"
            elif kind in {"mouse", "image_capture", "screen_condition", "screen_verification"}:
                alias = str(event.get("asset") or target_draft.get("asset") or "")
                if alias:
                    step["asset"] = alias
                    step["assets"] = [alias]

            for k, v in event.items():
                if not k.startswith("_") and k not in step:
                    step[k] = v

            reg = event.get("region") or event.get("search_region") or event.get("_review_search_region")
            if reg:
                step["region"] = list(reg)
                step["search_region"] = list(reg)

        dialog = ActionEditorDialog(self.repository, step, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            updated = dialog.step()
            target_draft["step"] = updated
            target_draft["detail"] = updated.get("label", target_draft.get("detail"))
            if isinstance(event, dict):
                event.update(updated)
                event["detail"] = target_draft["detail"]
                if updated.get("region"):
                    event["search_region"] = list(updated["region"])
                    event["region"] = list(updated["region"])
            self.update_live_events(self._live_events, force=True)

    def _tick(self) -> None:
        seconds = max(0, self.elapsed.elapsed() - 2000) // 1000
        elapsed = f"{seconds // 60:02d}:{seconds % 60:02d}"
        if self.gate_active and self.record_mode == "branch":
            self.label.setText(f"ON {elapsed} · 분기 액션 · F5 확인 · F8 이미지 · F11 → 일반")
        elif self.gate_active:
            self.label.setText(f"ON {elapsed} · 일반 액션 · F5 확인 · F8 이미지 · F11 → 분기")
        else:
            self.label.setText(f"OFF {elapsed} · ` 또는 F12 → 시작 · F11 → 모드 · F10 종료")

    def enable_workflow_branches(self) -> None:
        self.branch_button.setVisible(True)
        self.mode_badge.setText(f"작업 {self.workflow_index}")
        self._tick()

    def set_workflow_index(self, index: int) -> None:
        self.workflow_index = max(1, int(index))
        if self.branch_button.isVisible():
            self.mode_badge.setText(f"작업 {self.workflow_index}")
            self.label.setText(
                f"작업 {self.workflow_index} · 우클릭/F5=화면 확인 · F7=다음 작업 · F10=종료"
            )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.stop_requested.emit()
        super().closeEvent(event)

    def set_gate_active(self, active: bool) -> None:
        self.gate_active = bool(active)
        self.capture_button.setEnabled(self.gate_active)
        self.region_capture_button.setEnabled(self.gate_active)
        self.dot.setStyleSheet(
            f"color:{'#FF4D67' if self.gate_active else '#697386'}; font-size:18pt;"
        )
        self._tick()

    def set_record_mode(self, mode: str) -> None:
        self.record_mode = "branch" if str(mode).lower() == "branch" else "action"
        branch = self.record_mode == "branch"
        self.mode_badge.setText("분기 후보" if branch else "일반 액션")
        self.mode_badge.setStyleSheet(
            "padding:4px 7px; border-radius:6px; font-weight:800;"
            + (
                "color:#FFCC66; background:#3A2D18; border:1px solid #8B6828;"
                if branch
                else "color:#55E0C2; background:#17312C; border:1px solid #2F7969;"
            )
        )
        self._tick()

    def show_capture_result(self, width: int, height: int, search_region_set: bool = False) -> None:
        role = "분기 후보" if self.record_mode == "branch" else "기본 이미지"
        extra = " + 서치 영역 지정" if search_region_set else ""
        self.label.setText(f"{role} {width}×{height}{extra} 완료 · F11 → 모드 · F12 → 기록 정지")

    def _toggle_live_map(self) -> None:
        visible = not self.live_canvas.isVisible()
        self.live_canvas.setVisible(visible)
        self.map_toggle.setText("노드맵 접기" if visible else "노드맵 펼치기")
        if visible:
            self.resize(max(self.width(), 900), max(self.height(), 520))

    @staticmethod
    def _live_step(draft: dict[str, Any], index: int) -> dict[str, Any]:
        kind = str(draft.get("kind") or "")
        event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
        event_id = str(event.get("event_id") or f"draft-{kind}-{int(draft.get('t') or 0)}-{index}")
        workflow_id = str(draft.get("workflow_id") or "workflow-01")
        common = {
            "label": str(draft.get("detail") or kind or "녹화 동작"),
            "_event_id": event_id,
            "workflow_id": workflow_id,
            "workflow_label": f"스마트 작업 {int(draft.get('workflow_index') or 1)}",
            "_member_event_ids": list(draft.get("_member_event_ids") or [event_id]),
        }
        if draft.get("step") and isinstance(draft["step"], dict):
            return {**common, **draft["step"]}
        if kind == "wait":
            return {**common, "action": "wait", "duration": int(draft.get("duration") or 1000)}
        if kind in {"screen_verification", "screen_condition"}:
            return {**common, "action": "screen_condition", "label": str(draft.get("detail") or "화면 이미지 확인")}
        if kind in {"mouse", "image_capture"}:
            label = "F8 이미지 캡처" if kind == "image_capture" else str(draft.get("detail") or "클릭")
            return {**common, "action": "image_search", "label": label}
        if kind == "mouse_drag":
            return {**common, "action": "inactive_click", "label": str(draft.get("detail") or "드래그")}
        if kind in {"text", "key", "type_text"}:
            return {**common, "action": "type_text", "label": str(draft.get("detail") or "텍스트 입력")}
        if kind in {
            "datetime_condition", "browser_action", "ocr", "table_store",
            "table_copy", "table_paste", "table_excel_read", "table_excel_write",
            "set_var", "calc_var", "coord_mode", "call_submacro", "flow_control",
            "text_condition", "run_program", "terminate_program",
            "pixel_search", "ocr_tracking", "multi_pixel_check", "wait_color", "color_ratio"
        }:
            return {**common, "action": kind, "label": str(draft.get("detail") or ACTION_TITLES.get(kind, kind))}
        if kind == "find_text_click":
            return {**common, "action": "ocr", "label": str(draft.get("detail") or "텍스트 찾아 클릭")}
        return {**common, "action": kind if kind else "flow_control"}

    def _group_live_drafts(self, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: list[dict[str, Any]] = []
        used_groups: set[str] = set()
        for draft in drafts:
            event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
            event_id = str(event.get("event_id") or "")
            group = self._live_multi_groups.get(event_id, "")
            if not group:
                grouped.append(draft)
                continue
            if group in used_groups:
                continue
            members = []
            for candidate in drafts:
                candidate_event = candidate.get("event") if isinstance(candidate.get("event"), dict) else {}
                candidate_id = str(candidate_event.get("event_id") or "")
                if self._live_multi_groups.get(candidate_id, "") == group:
                    members.append((candidate_id, candidate))
            if len(members) < 2:
                grouped.append(draft)
                continue
            primary = dict(members[0][1])
            primary["event"] = dict(primary.get("event") or {})
            primary["detail"] = f"멀티 이미지 서치 {len(members)}개"
            primary["_member_event_ids"] = [identifier for identifier, _candidate in members]
            grouped.append(primary)
            used_groups.add(group)
        return grouped

    def _remember_live_positions(self) -> None:
        for index, node in self.live_canvas.nodes.items():
            if not 0 < index <= len(self.live_canvas.steps):
                continue
            event_id = str(self.live_canvas.steps[index - 1].get("_event_id") or "")
            if event_id:
                self._event_positions[event_id] = [round(node.pos().x(), 2), round(node.pos().y(), 2)]

    def update_live_events(self, events: list[dict[str, Any]], force: bool = False) -> None:
        self._live_events = list(events)
        drafts = self._group_live_drafts(recording_drafts(events, include_waits=False))
        steps = [self._live_step(draft, index) for index, draft in enumerate(drafts, start=1)]
        event_ids = tuple(str(step.get("_event_id") or "") for step in steps)
        signature = tuple(
            f"{event_id}|{','.join(str(value) for value in step.get('_member_event_ids') or [])}"
            for event_id, step in zip(event_ids, steps)
        )
        if signature == self._last_live_signature and not force:
            return
        self._remember_live_positions()
        positions = {
            str(index): self._event_positions[event_id]
            for index, event_id in enumerate(event_ids, start=1)
            if event_id in self._event_positions
        }
        workflow_groups: list[list[int]] = []
        workflow_lookup: dict[str, int] = {}
        for index, step in enumerate(steps, start=1):
            workflow_id = str(step.get("workflow_id") or "workflow-01")
            if workflow_id not in workflow_lookup:
                workflow_lookup[workflow_id] = len(workflow_groups)
                workflow_groups.append([])
            workflow_groups[workflow_lookup[workflow_id]].append(index)
        if len(workflow_groups) > 1:
            first_indexes = [indexes[0] for indexes in workflow_groups if indexes]
            for indexes in workflow_groups:
                for position, index in enumerate(indexes):
                    if position + 1 < len(indexes):
                        steps[index - 1]["on_success"] = indexes[position + 1]
                    else:
                        steps[index - 1]["stop_on_success"] = True
            for position, index in enumerate(first_indexes):
                steps[index - 1]["abort_on_fail"] = True
                automation = steps[index - 1].get("_automation") if isinstance(steps[index - 1].get("_automation"), dict) else {}
                automation.update({"start_workflow_candidate": True, "hide_candidate_fail_edge": True})
                steps[index - 1]["_automation"] = automation
                if position + 1 < len(first_indexes):
                    steps[index - 1]["on_fail"] = first_indexes[position + 1]
                    steps[index - 1]["abort_on_fail"] = False
        else:
            for index, step in enumerate(steps[:-1], start=1):
                step["on_success"] = index + 1
        event_index = {event_id: index for index, event_id in enumerate(event_ids, start=1)}
        for (source_id, kind), target_id in self._live_link_overrides.items():
            source_index = event_index.get(source_id)
            if source_index is None:
                continue
            if kind == "success" and isinstance(target_id, list):
                configure_success_candidates(
                    steps,
                    source_index,
                    [event_index[value] for value in target_id if value in event_index],
                )
                continue
            field = "on_fail" if kind == "fail" else "on_success"
            if target_id is None or not isinstance(target_id, str) or target_id not in event_index:
                steps[source_index - 1].pop(field, None)
            else:
                steps[source_index - 1][field] = event_index[target_id]
        macro = {"steps": steps, "graph_positions": positions}
        if len(workflow_groups) > 1:
            candidates = [indexes[0] for indexes in workflow_groups if indexes]
            macro["graph_start_step"] = candidates[0]
            macro["start_search_candidates"] = candidates
        previews: dict[int, Any] = {}
        for index, draft in enumerate(drafts, start=1):
            event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
            image = _recorded_sample_image(event)
            if not image.isNull():
                pixmap = QtGui.QPixmap.fromImage(image)
                search_region = event.get("search_region") or event.get("_review_search_region")
                if isinstance(search_region, list) and len(search_region) >= 4:
                    previews[index] = (pixmap, search_region)
                else:
                    previews[index] = pixmap
        self.live_canvas.set_asset_previews(previews)
        self.live_canvas.set_macro(macro)
        self._last_live_signature = signature

    def _connect_live_nodes(self, source: int, target: int, kind: str) -> None:
        if not (0 < source <= len(self.live_canvas.steps) and 0 < target <= len(self.live_canvas.steps)):
            return
        source_id = str(self.live_canvas.steps[source - 1].get("_event_id") or "")
        target_id = str(self.live_canvas.steps[target - 1].get("_event_id") or "")
        if not source_id or not target_id:
            return
        normalized_kind = "fail" if kind == "fail" else "success"
        if normalized_kind == "fail":
            # Failure ports are single-destination branches. Dragging them again
            # replaces the old target; only success ports support fan-out.
            self._live_link_overrides[(source_id, normalized_kind)] = target_id
            self._schedule_live_rebuild()
            return
        target_key = "on_success" if normalized_kind == "success" else "on_fail"
        old_target = self.live_canvas.retargeting_target(source, normalized_kind)
        current = self._live_link_overrides.get((source_id, normalized_kind))
        if isinstance(current, list):
            candidates = list(current)
        else:
            candidates = []
            current_target = int(self.live_canvas.steps[source - 1].get(target_key) or 0)
            if 0 < current_target <= len(self.live_canvas.steps):
                current_id = str(self.live_canvas.steps[current_target - 1].get("_event_id") or "")
                if current_id:
                    candidates.append(current_id)
        if old_target:
            old_id = str(self.live_canvas.steps[old_target - 1].get("_event_id") or "") if old_target <= len(self.live_canvas.steps) else ""
            candidates = [target_id if value == old_id else value for value in candidates]
        elif target_id not in candidates:
            candidates.append(target_id)
        self._live_link_overrides[(source_id, normalized_kind)] = candidates if len(candidates) > 1 else target_id
        self._schedule_live_rebuild()

    def _delete_live_edge(self, source: int, _target: int, kind: str) -> None:
        if not 0 < source <= len(self.live_canvas.steps):
            return
        source_id = str(self.live_canvas.steps[source - 1].get("_event_id") or "")
        if source_id:
            normalized_kind = "fail" if kind == "fail" else "success"
            key = (source_id, normalized_kind)
            current = self._live_link_overrides.get(key)
            if isinstance(current, list):
                target_id = ""
                if 0 < _target <= len(self.live_canvas.steps):
                    target_id = str(self.live_canvas.steps[_target - 1].get("_event_id") or "")
                remaining = [value for value in current if value != target_id]
                self._live_link_overrides[key] = remaining if len(remaining) > 1 else (remaining[0] if remaining else None)
            else:
                self._live_link_overrides[key] = None
            self._schedule_live_rebuild()

    def _merge_live_image_nodes(self, indexes: list[int]) -> None:
        selected = sorted({int(index) for index in indexes if 0 < int(index) <= len(self.live_canvas.steps)})
        if len(selected) < 2:
            return
        member_ids: list[str] = []
        for index in selected:
            step = self.live_canvas.steps[index - 1]
            if str(step.get("action") or "") != "image_search":
                return
            member_ids.extend(str(value) for value in step.get("_member_event_ids") or [] if str(value))
        if len(member_ids) < 2:
            return
        group_id = f"live-multi-{uuid.uuid4().hex}"
        for event_id in member_ids:
            self._live_multi_groups[event_id] = group_id
        self._schedule_live_rebuild()

    def _schedule_live_rebuild(self) -> None:
        if self._live_rebuild_pending:
            return
        self._live_rebuild_pending = True

        def rebuild() -> None:
            self._live_rebuild_pending = False
            self.update_live_events(self._live_events, force=True)

        QtCore.QTimer.singleShot(0, rebuild)

    def event_positions(self) -> dict[str, list[float]]:
        self._remember_live_positions()
        return dict(self._event_positions)

    def event_links(self) -> dict[tuple[str, str], str | list[str] | None]:
        return dict(self._live_link_overrides)

    def multi_groups(self) -> dict[str, str]:
        return dict(self._live_multi_groups)


def _recorded_sample_image(event: dict[str, Any]) -> QtGui.QImage:
    if isinstance(event.get("sample_image"), QtGui.QImage):
        return event["sample_image"]
    sample = str(event.get("image_sample_bmp") or event.get("sample_image") or "")
    if not sample:
        return QtGui.QImage()
    try:
        return QtGui.QImage.fromData(base64.b64decode(sample))
    except (TypeError, ValueError):
        return QtGui.QImage()


def _encoded_png(image: QtGui.QImage) -> str:
    if image.isNull():
        return ""
    payload = QtCore.QByteArray()
    buffer = QtCore.QBuffer(payload)
    buffer.open(QtCore.QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(bytes(payload)).decode("ascii")


def _recorded_detail_image(event: dict[str, Any]) -> QtGui.QImage:
    sample = str(event.get("_review_edited_image_bmp") or "")
    if not sample:
        return QtGui.QImage()
    try:
        return QtGui.QImage.fromData(base64.b64decode(sample))
    except (TypeError, ValueError):
        return QtGui.QImage()


def _centered_crop_rect(image: QtGui.QImage, anchor: QtCore.QPoint, size: QtCore.QSize) -> QtCore.QRect:
    width = max(16, min(image.width(), size.width()))
    height = max(16, min(image.height(), size.height()))
    left = max(0, min(image.width() - width, anchor.x() - width // 2))
    top = max(0, min(image.height() - height, anchor.y() - height // 2))
    return QtCore.QRect(left, top, width, height)


def _expanded_capture_rect(rect: QtCore.QRect, bounds: QtCore.QRect, minimum: QtCore.QSize = QtCore.QSize(64, 48)) -> QtCore.QRect:
    """Add stable visual context to fragile icon-sized captures."""
    if not rect.isValid() or not bounds.isValid():
        return QtCore.QRect()
    center = rect.center()
    width = max(minimum.width(), rect.width())
    height = max(minimum.height(), rect.height())
    expanded = QtCore.QRect(center.x() - width // 2, center.y() - height // 2, width, height)
    return expanded.intersected(bounds)


class RecordedCropCanvas(QtWidgets.QWidget):
    selection_changed = QtCore.Signal(QtCore.QRect)

    def __init__(self, image: QtGui.QImage, anchor: QtCore.QPoint | None = None, parent=None) -> None:
        super().__init__(parent)
        self.image = image
        self.crop_size = QtCore.QSize(min(96, image.width()), min(64, image.height()))
        self.anchor = QtCore.QPoint(anchor or QtCore.QPoint(image.width() // 2, image.height() // 2))
        self.crop_rect = _centered_crop_rect(image, self.anchor, self.crop_size)
        self.guide_shape = "rect"
        self._drag_mode = ""
        self._drag_start = QtCore.QPoint()
        self._drag_rect = QtCore.QRect()
        self.setMinimumSize(680, 450)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.setMouseTracking(True)

    def set_crop_rect(self, rect: QtCore.QRect) -> None:
        clipped = QtCore.QRect(rect).normalized().intersected(self.image.rect())
        if clipped.width() < 16 or clipped.height() < 16:
            clipped = _centered_crop_rect(self.image, self.anchor, self.crop_size)
        self.crop_rect = clipped
        self.crop_size = clipped.size()
        self.selection_changed.emit(QtCore.QRect(clipped))
        self.update()

    def _image_rect(self) -> QtCore.QRect:
        target_size = self.image.size().scaled(self.size() - QtCore.QSize(32, 32), QtCore.Qt.KeepAspectRatio)
        rect = QtCore.QRect(QtCore.QPoint(), target_size)
        rect.moveCenter(self.rect().center())
        return rect

    def _widget_crop_rect(self) -> QtCore.QRect:
        image_rect = self._image_rect()
        scale_x = image_rect.width() / max(1, self.image.width())
        scale_y = image_rect.height() / max(1, self.image.height())
        return QtCore.QRectF(
            image_rect.left() + self.crop_rect.left() * scale_x,
            image_rect.top() + self.crop_rect.top() * scale_y,
            self.crop_rect.width() * scale_x,
            self.crop_rect.height() * scale_y,
        ).toAlignedRect()

    def _source_point(self, point: QtCore.QPoint) -> QtCore.QPoint:
        image_rect = self._image_rect()
        x = round((point.x() - image_rect.left()) * self.image.width() / max(1, image_rect.width()))
        y = round((point.y() - image_rect.top()) * self.image.height() / max(1, image_rect.height()))
        return QtCore.QPoint(
            max(self.image.rect().left(), min(self.image.rect().right(), x)),
            max(self.image.rect().top(), min(self.image.rect().bottom(), y)),
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        point = event.position().toPoint()
        mode = ScreenCaptureDialog._hit_test(self._widget_crop_rect(), point, 10)
        if not mode:
            return
        self._drag_mode = mode
        self._drag_start = self._source_point(point)
        self._drag_rect = QtCore.QRect(self.crop_rect)
        self.setCursor(ScreenCaptureDialog._cursor_for_mode(mode))
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        point = event.position().toPoint()
        if self._drag_mode:
            rect = ScreenCaptureDialog._drag_selection(
                self._drag_mode,
                self._drag_rect,
                self._drag_start,
                self._source_point(point),
                self.image.rect(),
                16,
            )
            self.set_crop_rect(rect)
            event.accept()
            return
        mode = ScreenCaptureDialog._hit_test(self._widget_crop_rect(), point, 10)
        self.setCursor(ScreenCaptureDialog._cursor_for_mode(mode))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._drag_mode:
            self._drag_mode = ""
            mode = ScreenCaptureDialog._hit_test(self._widget_crop_rect(), event.position().toPoint(), 10)
            self.setCursor(ScreenCaptureDialog._cursor_for_mode(mode))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resize_from_wheel(self, point: QtCore.QPoint, delta_y: int) -> bool:
        """Resize the edge nearest the pointer; resize proportionally from the centre otherwise."""
        if not delta_y:
            return False
        mode = ScreenCaptureDialog._hit_test(self._widget_crop_rect(), point, 24)
        if not mode:
            return False
        ticks = max(1, abs(int(delta_y)) // 120)
        direction = 1 if delta_y > 0 else -1
        step = 8 * ticks
        rect = QtCore.QRect(self.crop_rect)
        if mode == "move":
            width_delta = direction * step * 2
            height_delta = direction * max(4, round(step * 2 * rect.height() / max(1, rect.width())))
            size = QtCore.QSize(
                max(16, min(self.image.width(), rect.width() + width_delta)),
                max(16, min(self.image.height(), rect.height() + height_delta)),
            )
            if self.guide_shape == "circle":
                diameter = max(16, min(size.width(), size.height(), self.image.width(), self.image.height()))
                size = QtCore.QSize(diameter, diameter)
            self.set_crop_rect(_centered_crop_rect(self.image, rect.center(), size))
            return True

        dx = 0
        dy = 0
        if "w" in mode:
            dx = -direction * step
        elif "e" in mode:
            dx = direction * step
        if "n" in mode:
            dy = -direction * step
        elif "s" in mode:
            dy = direction * step
        resized = ScreenCaptureDialog._drag_selection(
            mode,
            rect,
            QtCore.QPoint(),
            QtCore.QPoint(dx, dy),
            self.image.rect(),
            16,
        )
        self.set_crop_rect(resized)
        return True

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self.resize_from_wheel(event.position().toPoint(), event.angleDelta().y()):
            mode = ScreenCaptureDialog._hit_test(self._widget_crop_rect(), event.position().toPoint(), 24)
            self.setCursor(ScreenCaptureDialog._cursor_for_mode(mode))
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QtGui.QColor("#0D1017"))
        image_rect = self._image_rect()
        painter.drawImage(image_rect, self.image)
        painter.fillRect(image_rect, QtGui.QColor(4, 7, 12, 135))

        source_crop = self.crop_rect
        scale_x = image_rect.width() / max(1, self.image.width())
        scale_y = image_rect.height() / max(1, self.image.height())
        crop_rect = QtCore.QRectF(
            image_rect.left() + source_crop.left() * scale_x,
            image_rect.top() + source_crop.top() * scale_y,
            source_crop.width() * scale_x,
            source_crop.height() * scale_y,
        )
        painter.drawImage(crop_rect, self.image, source_crop)
        pen = QtGui.QPen(QtGui.QColor("#41D9D2"), 3)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        if self.guide_shape == "circle":
            painter.drawEllipse(crop_rect)
        else:
            painter.drawRoundedRect(crop_rect, 8, 8)
        center = crop_rect.center()
        painter.drawLine(QtCore.QPointF(center.x() - 10, center.y()), QtCore.QPointF(center.x() + 10, center.y()))
        painter.drawLine(QtCore.QPointF(center.x(), center.y() - 10), QtCore.QPointF(center.x(), center.y() + 10))
        painter.setPen(QtGui.QPen(QtGui.QColor("#E7FFFF"), 1))
        painter.setBrush(QtGui.QColor("#19D7D0"))
        handle_points = (
            crop_rect.topLeft(), QtCore.QPointF(crop_rect.center().x(), crop_rect.top()), crop_rect.topRight(),
            QtCore.QPointF(crop_rect.left(), crop_rect.center().y()), QtCore.QPointF(crop_rect.right(), crop_rect.center().y()),
            crop_rect.bottomLeft(), QtCore.QPointF(crop_rect.center().x(), crop_rect.bottom()), crop_rect.bottomRight(),
        )
        for point in handle_points:
            painter.drawRect(QtCore.QRectF(point.x() - 5, point.y() - 5, 10, 10))
        painter.end()


class RecordedImageDetailDialog(QtWidgets.QDialog):
    """Inspect the real template, expose runtime analysis channels, and allow manual cleanup."""

    def __init__(
        self,
        image: QtGui.QImage,
        parent=None,
        *,
        precise: bool = True,
        initial_click_offset: QtCore.QPoint | None = None,
    ) -> None:
        super().__init__(parent)
        self.image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
        self._cutout_applied = self._has_transparent_pixels(self.image)
        self.click_point = (
            QtCore.QPoint(self.image.width() // 2, self.image.height() // 2) + initial_click_offset
            if initial_click_offset is not None
            else None
        )
        self.click_pick_active = False
        self.setWindowTitle("스마트 녹화 · 이미지 상세 편집")
        self.resize(1080, 780)
        self.setMinimumSize(900, 650)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("이미지 서치에 실제로 저장될 표본을 확인하고 보정합니다.")
        title.setStyleSheet("font-size:15pt; font-weight:800;")
        description = QtWidgets.QLabel(
            "정밀 검색은 한 번 캡처한 화면에서 컬러·흑백 구조·윤곽 특징을 함께 비교합니다. "
            "자동 누끼의 투명 마스크는 세 분석 채널에 똑같이 적용됩니다. "
            "누끼 후에도 추가 편집에서 지우개·색상 제거·투명화·흑백·대비를 계속 보정할 수 있습니다."
        )
        description.setWordWrap(True)
        description.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(description)

        self.tabs = QtWidgets.QTabWidget()
        self.preview_labels: list[QtWidgets.QLabel] = []
        for label in ("1 · 원본 컬러", "2 · 흑백 구조", "3 · 윤곽 특징"):
            preview = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
            preview.setMinimumSize(720, 390)
            preview.setStyleSheet("background:#090B10; border:1px solid #303647; border-radius:10px;")
            preview.installEventFilter(self)
            self.preview_labels.append(preview)
            self.tabs.addTab(preview, label)
        layout.addWidget(self.tabs, 1)

        controls = QtWidgets.QHBoxLayout()
        auto_cutout = QtWidgets.QPushButton("✨ 자동 누끼")
        auto_cutout.setToolTip("가운데 있는 손·아이콘 같은 전경을 자동 분리하고 배경을 투명하게 만듭니다.")
        auto_cutout.clicked.connect(self._auto_cutout)
        self.manual_button = QtWidgets.QPushButton("✎ 수동 상세 편집")
        self.manual_button.setToolTip(
            "현재 결과를 그대로 이어서 크게 보며 지우개, 색상 제거, 투명화, 흑백, 이진화, 밝기와 대비를 편집합니다."
        )
        self.manual_button.clicked.connect(self._manual_edit)
        area_button = QtWidgets.QPushButton("▣ 사용할 이미지 영역 지정")
        area_button.setToolTip("현재 이미지 안에서 실제 이미지 서치에 사용할 부분만 드래그해 잘라냅니다.")
        area_button.clicked.connect(self._select_image_area)
        reset = QtWidgets.QPushButton("원본으로 되돌리기")
        self._original = self.image.copy()
        reset.clicked.connect(self._reset)
        self.precise = QtWidgets.QCheckBox("컬러·흑백·윤곽 3단계 정밀 검색 사용")
        self.precise.setChecked(bool(precise))
        self.precise.setToolTip("화면은 한 번만 캡처하고 세 분석 채널을 같은 위치에서 비교하므로 별도 캡처를 세 번 수행하지 않습니다.")
        self.info = QtWidgets.QLabel()
        self.info.setObjectName("Muted")
        controls.addWidget(auto_cutout)
        controls.addWidget(self.manual_button)
        controls.addWidget(area_button)
        controls.addWidget(reset)
        controls.addWidget(self.precise)
        controls.addStretch(1)
        controls.addWidget(self.info)
        layout.addLayout(controls)

        click_controls = QtWidgets.QHBoxLayout()
        self.click_pick_button = QtWidgets.QPushButton("⌖ 클릭 위치 지정")
        self.click_pick_button.setCheckable(True)
        self.click_pick_button.setToolTip("찾은 이미지의 중심 대신 실제로 클릭할 지점을 이미지 위에서 지정합니다.")
        self.click_pick_button.toggled.connect(self._toggle_click_picker)
        reset_click = QtWidgets.QPushButton("중심 클릭으로 되돌리기")
        reset_click.clicked.connect(self._reset_click_point)
        self.click_point_label = QtWidgets.QLabel()
        self.click_point_label.setStyleSheet("font-weight:800; color:#41D9D2;")
        click_hint = QtWidgets.QLabel("손가락 끝처럼 실제 클릭할 지점을 지정하면 숫자 오프셋을 따로 계산할 필요가 없습니다.")
        click_hint.setObjectName("Muted")
        click_controls.addWidget(self.click_pick_button)
        click_controls.addWidget(reset_click)
        click_controls.addWidget(self.click_point_label)
        click_controls.addSpacing(12)
        click_controls.addWidget(click_hint, 1)
        layout.addLayout(click_controls)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("상세 편집 적용")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_previews()

    @staticmethod
    def _has_transparent_pixels(image: QtGui.QImage) -> bool:
        rgba = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        raw = bytes(rgba.constBits())
        return any(alpha < 250 for alpha in raw[3::4])

    @staticmethod
    def _apply_alpha(source: QtGui.QImage, target: QtGui.QImage) -> QtGui.QImage:
        result = target.convertToFormat(QtGui.QImage.Format_ARGB32)
        painter = QtGui.QPainter(result)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, source.convertToFormat(QtGui.QImage.Format_ARGB32))
        painter.end()
        return result

    @classmethod
    def _grayscale_preview(cls, image: QtGui.QImage) -> QtGui.QImage:
        gray = image.convertToFormat(QtGui.QImage.Format_Grayscale8).convertToFormat(QtGui.QImage.Format_ARGB32)
        return cls._apply_alpha(image, gray)

    @staticmethod
    def _checkerboard_preview(image: QtGui.QImage) -> QtGui.QImage:
        canvas = QtGui.QImage(image.size(), QtGui.QImage.Format_RGB32)
        canvas.fill(QtGui.QColor("#1A1D25"))
        painter = QtGui.QPainter(canvas)
        tile = max(8, min(18, min(image.width(), image.height()) // 18))
        light = QtGui.QColor("#303543")
        dark = QtGui.QColor("#1B1F29")
        for y in range(0, image.height(), tile):
            for x in range(0, image.width(), tile):
                painter.fillRect(x, y, tile, tile, light if (x // tile + y // tile) % 2 == 0 else dark)
        painter.drawImage(0, 0, image)
        painter.end()
        return canvas

    @classmethod
    def _edge_preview(cls, image: QtGui.QImage) -> QtGui.QImage:
        gray = image.convertToFormat(QtGui.QImage.Format_Grayscale8)
        edge = QtGui.QImage(gray.size(), QtGui.QImage.Format_ARGB32)
        edge.fill(QtGui.QColor("#000000"))
        for y in range(1, gray.height() - 1):
            for x in range(1, gray.width() - 1):
                center = QtGui.QColor(gray.pixel(x, y)).red()
                right = QtGui.QColor(gray.pixel(x + 1, y)).red()
                down = QtGui.QColor(gray.pixel(x, y + 1)).red()
                value = min(255, (abs(center - right) + abs(center - down)) * 3)
                edge.setPixelColor(x, y, QtGui.QColor(value, value, value))
        return cls._apply_alpha(image, edge)

    def _variants(self) -> list[QtGui.QImage]:
        return [
            self.image,
            self._grayscale_preview(self.image),
            self._edge_preview(self.image),
        ]

    def _refresh_previews(self) -> None:
        for label, image in zip(self.preview_labels, self._variants()):
            display = image.convertToFormat(QtGui.QImage.Format_ARGB32)
            if self.click_point is not None:
                display = display.copy()
                painter = QtGui.QPainter(display)
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                painter.setPen(QtGui.QPen(QtGui.QColor("#00E6E0"), max(2, min(display.width(), display.height()) // 80)))
                painter.setBrush(QtGui.QColor(0, 230, 224, 70))
                painter.drawEllipse(self.click_point, 9, 9)
                painter.drawLine(self.click_point + QtCore.QPoint(-15, 0), self.click_point + QtCore.QPoint(15, 0))
                painter.drawLine(self.click_point + QtCore.QPoint(0, -15), self.click_point + QtCore.QPoint(0, 15))
                painter.end()
            display = self._checkerboard_preview(display)
            target = label.size() - QtCore.QSize(24, 24)
            label.setPixmap(
                QtGui.QPixmap.fromImage(display).scaled(
                    max(120, target.width()),
                    max(90, target.height()),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            )
        suffix = " · 누끼 마스크 적용 · 추가 편집 가능" if self._cutout_applied else ""
        self.info.setText(f"저장 표본 {self.image.width()} × {self.image.height()} px{suffix}")
        self.manual_button.setText("✎ 누끼 결과 추가 편집" if self._cutout_applied else "✎ 수동 상세 편집")
        self.tabs.setTabText(1, "2 · 흑백 구조 · 누끼 적용" if self._cutout_applied else "2 · 흑백 구조")
        self.tabs.setTabText(2, "3 · 윤곽 특징 · 누끼 적용" if self._cutout_applied else "3 · 윤곽 특징")
        self._update_click_label()

    def _toggle_click_picker(self, enabled: bool) -> None:
        self.click_pick_active = bool(enabled)
        if enabled:
            self.tabs.setCurrentIndex(0)
        for label in self.preview_labels:
            label.setCursor(QtCore.Qt.CrossCursor if enabled else QtCore.Qt.ArrowCursor)

    def _reset_click_point(self) -> None:
        self.click_point = None
        self.click_pick_button.setChecked(False)
        self._refresh_previews()

    def _update_click_label(self) -> None:
        offset = self.click_offset()
        if offset is None:
            self.click_point_label.setText("클릭: 이미지 중심")
        else:
            self.click_point_label.setText(
                f"클릭: {self.click_point.x()}, {self.click_point.y()} · 중심에서 {offset.x():+d}, {offset.y():+d}"
            )

    def eventFilter(self, watched, event):
        if (
            watched in self.preview_labels
            and self.click_pick_active
            and event.type() == QtCore.QEvent.MouseButtonPress
            and event.button() == QtCore.Qt.LeftButton
        ):
            label = watched
            pixmap = label.pixmap()
            if pixmap is None or pixmap.isNull():
                return True
            position = event.position().toPoint()
            left = (label.width() - pixmap.width()) // 2
            top = (label.height() - pixmap.height()) // 2
            local = position - QtCore.QPoint(left, top)
            if 0 <= local.x() < pixmap.width() and 0 <= local.y() < pixmap.height():
                x = round(local.x() * self.image.width() / max(1, pixmap.width()))
                y = round(local.y() * self.image.height() / max(1, pixmap.height()))
                self.click_point = QtCore.QPoint(
                    max(0, min(self.image.width() - 1, x)),
                    max(0, min(self.image.height() - 1, y)),
                )
                self.click_pick_button.setChecked(False)
                self._refresh_previews()
            return True
        return super().eventFilter(watched, event)

    def _manual_edit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="macrorelay-image-detail-") as directory:
            root = Path(directory)
            path = root / "recorded-template.png"
            if not self.image.save(str(path), "PNG"):
                QtWidgets.QMessageBox.warning(self, "이미지 상세 편집", "임시 편집 이미지를 만들지 못했습니다.")
                return
            editor = ImageEditorDialog(path, "스마트 녹화 검색 이미지", root / "history", self)
            editor.setWindowTitle("스마트 녹화 · 이미지 상세 편집")
            # Moving/cropping the bitmap would change the recorded click
            # anchor. Keep geometry fixed here and expose pixel cleanup tools.
            blocked = {"자르기", "↶ 왼쪽 회전", "↷ 오른쪽 회전", "↔ 좌우 반전", "↕ 상하 반전", "복사본 저장"}
            for button in editor.findChildren(QtWidgets.QPushButton):
                if button.text() in blocked:
                    button.hide()
                elif button.text() == "원본에 저장":
                    button.setText("편집 내용 적용")
            if editor.exec() != QtWidgets.QDialog.Accepted:
                return
            edited = QtGui.QImage(str(path))
            if not edited.isNull():
                self.image = edited.convertToFormat(QtGui.QImage.Format_ARGB32)
                self._cutout_applied = self._has_transparent_pixels(self.image)
                self._refresh_previews()

    def _select_image_area(self) -> None:
        if self.image.isNull():
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("상세 이미지 편집 · 사용할 영역 지정")
        dialog.resize(860, 680)
        layout = QtWidgets.QVBoxLayout(dialog)
        title = QtWidgets.QLabel("이미지 서치에 사용할 영역만 남기세요.")
        title.setStyleSheet("font-size:14pt; font-weight:800;")
        hint = QtWidgets.QLabel(
            "영역 안쪽 드래그: 이동 · 테두리/모서리 드래그: 크기 조절 · 휠 또는 +/−: 확대·축소"
        )
        hint.setObjectName("Muted")
        canvas = RecordedCropCanvas(self.image, QtCore.QPoint(self.image.width() // 2, self.image.height() // 2))
        canvas.set_crop_rect(self.image.rect())
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(canvas, 1)
        size_label = QtWidgets.QLabel()
        size_label.setStyleSheet("font-weight:800; color:#41D9D2;")
        canvas.selection_changed.connect(
            lambda rect: size_label.setText(f"선택 영역 {rect.width()} × {rect.height()} px")
        )
        size_label.setText(f"선택 영역 {canvas.crop_rect.width()} × {canvas.crop_rect.height()} px")
        layout.addWidget(size_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("이 영역만 사용")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        rect = canvas.crop_rect.normalized().intersected(self.image.rect())
        if not rect.isValid() or rect == self.image.rect():
            return
        old_click = QtCore.QPoint(self.click_point) if self.click_point is not None else None
        self.image = self.image.copy(rect).convertToFormat(QtGui.QImage.Format_ARGB32)
        if old_click is not None and rect.contains(old_click):
            self.click_point = old_click - rect.topLeft()
        else:
            self.click_point = None
        self._cutout_applied = self._has_transparent_pixels(self.image)
        self._refresh_previews()

    def _auto_cutout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="macrorelay-auto-cutout-") as directory:
            root = Path(directory)
            path = root / "auto-cutout.png"
            if not self.image.save(str(path), "PNG"):
                return
            editor = ImageEditorDialog(path, "자동 누끼", root / "history", self)
            before = editor.image.copy()
            editor.auto_cutout()
            if editor.image != before:
                self.image = editor.image.copy().convertToFormat(QtGui.QImage.Format_ARGB32)
                self._cutout_applied = self._has_transparent_pixels(self.image)
                self._refresh_previews()
            editor.close()

    def _reset(self) -> None:
        self.image = self._original.copy()
        self._cutout_applied = self._has_transparent_pixels(self.image)
        self._refresh_previews()

    def edited_image(self) -> QtGui.QImage:
        return self.image.copy()

    def precise_search_enabled(self) -> bool:
        return self.precise.isChecked()

    def click_offset(self) -> QtCore.QPoint | None:
        if self.click_point is None:
            return None
        return self.click_point - QtCore.QPoint(self.image.width() // 2, self.image.height() // 2)


class RecordedImageCropDialog(QtWidgets.QDialog):
    def __init__(
        self,
        image: QtGui.QImage,
        initial_size: QtCore.QSize | None = None,
        parent=None,
        *,
        anchor: QtCore.QPoint | None = None,
        initial_rect: QtCore.QRect | None = None,
        initial_detail_image: QtGui.QImage | None = None,
        precise_search: bool = False,
        initial_click_offset: QtCore.QPoint | None = None,
    ) -> None:
        super().__init__(parent)
        self._detail_image = initial_detail_image.copy() if initial_detail_image is not None else QtGui.QImage()
        self._detail_rect = QtCore.QRect(initial_rect) if initial_rect is not None else QtCore.QRect()
        self._precise_search = bool(precise_search)
        self._detail_click_offset = QtCore.QPoint(initial_click_offset) if initial_click_offset is not None else None
        self.setWindowTitle("스마트 녹화 · 이미지 캡처 영역")
        self.resize(760, 620)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("선택 영역을 옮기거나 8개 조절점을 드래그해 이미지 서치 표본을 정합니다.")
        title.setStyleSheet("font-size:14pt; font-weight:800;")
        hint = QtWidgets.QLabel(
            "영역 안쪽 드래그: 위치 이동 · 테두리/모서리 드래그: 크기 조절 · "
            "휠: 커서와 가까운 테두리 방향만 조절 · 영역 중앙 휠: 비율 확대·축소 · + / - 키 지원"
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(title)
        layout.addWidget(hint)
        self.canvas = RecordedCropCanvas(image, anchor)
        if initial_size is not None:
            size = QtCore.QSize(
                min(image.width(), max(16, initial_size.width())),
                min(image.height(), max(16, initial_size.height())),
            )
            self.canvas.crop_size = size
            self.canvas.crop_rect = _centered_crop_rect(image, self.canvas.anchor, size)
        if initial_rect is not None and initial_rect.isValid():
            self.canvas.set_crop_rect(initial_rect)
        self.canvas.selection_changed.connect(self._selection_changed)
        layout.addWidget(self.canvas, 1)
        controls = QtWidgets.QHBoxLayout()
        self.detail_button = QtWidgets.QPushButton("✨ 이미지 상세 편집")
        self.detail_button.setToolTip("실제 저장 이미지를 크게 확인하고 자동 3단계 분석 또는 수동 누끼·흑백·대비 편집을 적용합니다.")
        self.detail_button.clicked.connect(self._open_detail_editor)
        self.detail_state = QtWidgets.QLabel()
        self.detail_state.setObjectName("Muted")
        self.shape_combo = QtWidgets.QComboBox()
        self.shape_combo.addItem("□ 사각형 가이드", "rect")
        self.shape_combo.addItem("○ 원형 가이드", "circle")
        self.shape_combo.currentIndexChanged.connect(self._shape_changed)
        minus = QtWidgets.QPushButton("−  영역 축소")
        plus = QtWidgets.QPushButton("＋  영역 확대")
        minus.clicked.connect(lambda: self._resize_crop(-1))
        plus.clicked.connect(lambda: self._resize_crop(1))
        self.size_label = QtWidgets.QLabel()
        self.size_label.setStyleSheet("font-weight:800; color:#41D9D2;")
        controls.addWidget(self.shape_combo)
        controls.addWidget(self.detail_button)
        controls.addWidget(self.detail_state)
        controls.addStretch(1)
        controls.addWidget(minus)
        controls.addWidget(self.size_label)
        controls.addWidget(plus)
        layout.addLayout(controls)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("이 영역 사용")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if not self._detail_image.isNull() and not self._detail_rect.isValid():
            self._detail_rect = self.canvas.crop_rect
        self._update_size_label()
        self._update_detail_state()

    def _selection_changed(self, rect: QtCore.QRect) -> None:
        if not self._detail_image.isNull() and self._detail_rect.isValid() and rect != self._detail_rect:
            self._detail_image = QtGui.QImage()
            self._precise_search = False
            self._detail_click_offset = None
            self._update_detail_state("영역이 변경되어 이전 상세 편집을 해제했습니다.")
        self._update_size_label()

    def _update_detail_state(self, message: str = "") -> None:
        if message:
            self.detail_state.setText(message)
            return
        if self._detail_image.isNull():
            self.detail_state.setText("")
            return
        click_text = " · 클릭점 지정" if self._detail_click_offset is not None else ""
        self.detail_state.setText(("상세 편집 적용됨 · 정밀 검색" if self._precise_search else "상세 편집 적용됨") + click_text)

    def _open_detail_editor(self) -> None:
        rect = self.canvas.crop_rect.intersected(self.canvas.image.rect())
        source = self._detail_image if not self._detail_image.isNull() and rect == self._detail_rect else self.canvas.image.copy(rect)
        if source.isNull():
            return
        dialog = RecordedImageDetailDialog(
            source,
            self,
            precise=True,
            initial_click_offset=self._detail_click_offset,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self._detail_image = dialog.edited_image()
        self._detail_rect = QtCore.QRect(rect)
        self._precise_search = dialog.precise_search_enabled()
        self._detail_click_offset = dialog.click_offset()
        self._update_detail_state()

    def _shape_changed(self, _index: int = -1) -> None:
        self.canvas.guide_shape = str(self.shape_combo.currentData() or "rect")
        self.canvas.update()

    def _resize_crop(self, direction: int) -> None:
        width_step, height_step = (20, 20) if self.canvas.guide_shape == "circle" else (24, 16)
        current = self.canvas.crop_size
        width = max(24, min(self.canvas.image.width(), current.width() + direction * width_step))
        height = max(24, min(self.canvas.image.height(), current.height() + direction * height_step))
        if self.canvas.guide_shape == "circle":
            diameter = min(width, height, self.canvas.image.width(), self.canvas.image.height())
            width = height = diameter
        self.canvas.crop_size = QtCore.QSize(width, height)
        self.canvas.crop_rect = _centered_crop_rect(self.canvas.image, self.canvas.crop_rect.center(), self.canvas.crop_size)
        self._update_size_label()
        self.canvas.update()

    def _update_size_label(self) -> None:
        size = self.canvas.crop_size
        self.size_label.setText(f"{size.width()} × {size.height()}")

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in {QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal}:
            self._resize_crop(1)
            return
        if event.key() in {QtCore.Qt.Key_Minus, QtCore.Qt.Key_Underscore}:
            self._resize_crop(-1)
            return
        super().keyPressEvent(event)

    def crop_size(self) -> QtCore.QSize:
        return QtCore.QSize(self.canvas.crop_size)

    def crop_rect(self) -> QtCore.QRect:
        return QtCore.QRect(self.canvas.crop_rect)

    def guide_shape(self) -> str:
        return self.canvas.guide_shape

    def detail_image(self) -> QtGui.QImage:
        if self._detail_rect == self.canvas.crop_rect:
            return self._detail_image.copy()
        return QtGui.QImage()

    def precise_search_enabled(self) -> bool:
        return self._precise_search and not self.detail_image().isNull()

    def detail_click_offset(self) -> QtCore.QPoint | None:
        return QtCore.QPoint(self._detail_click_offset) if self._detail_click_offset is not None else None


class FindTextClickDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, window_title=""):
        super().__init__(parent)
        self.setWindowTitle("🎯 텍스트 찾아 클릭 빠른 추가")
        self.setFixedWidth(460)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        info = QtWidgets.QLabel("화면에서 인식하여 클릭할 텍스트를 입력하세요.")
        info.setStyleSheet("font-weight: 700; font-size: 10pt; color: #FFFFFF;")
        layout.addWidget(info)

        self.text_edit = QtWidgets.QLineEdit()
        self.text_edit.setPlaceholderText("예: 확인, 다음, 로그인, 구매하기 등")
        self.text_edit.setMinimumHeight(38)
        self.text_edit.setStyleSheet("font-size: 10.5pt;")
        layout.addWidget(self.text_edit)

        scope_box = QtWidgets.QGroupBox("탐색 대상 범위")
        scope_layout = QtWidgets.QVBoxLayout(scope_box)
        win_display = f"'{window_title[:22]}...' 창 내부" if window_title else "현재 대상 프로그램 창 내부"
        self.radio_client = QtWidgets.QRadioButton(f"🎯 {win_display} (권장 · 빠르고 정확함)")
        self.radio_client.setChecked(True)
        self.radio_screen = QtWidgets.QRadioButton("🖥️ 전체 모니터 화면 (화면 전체에서 탐색)")
        scope_layout.addWidget(self.radio_client)
        scope_layout.addWidget(self.radio_screen)
        layout.addWidget(scope_box)

        hint = QtWidgets.QLabel("※ 추가 후 상세 설정에서 클릭 오프셋, 타임아웃을 미세조정할 수 있습니다.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("노드 추가")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.text_edit.text().strip()

    def region_mode(self) -> str:
        return "client" if self.radio_client.isChecked() else "screen"


class SmartRecordingController(QtCore.QObject):
    completed = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.host = parent
        self.process: QtCore.QProcess | None = None
        self.bar: RecordingBar | None = None
        self.output = repository.root / ".automation" / f"recording-{uuid.uuid4().hex}.jsonl"
        self._manual_stop = False
        self._capture_in_progress = False
        self._seen_capture_requests: set[str] = set()
        self._last_gate_state = False
        self._last_record_mode = "action"
        self._manual_captures: list[dict[str, Any]] = []
        self._deleted_event_ids: set[str] = set()
        self._capture_poll = QtCore.QTimer(self)
        self._capture_poll.setInterval(140)
        self._capture_poll.timeout.connect(self._poll_capture_requests)

    def start(self) -> None:
        helper = self.repository.root / "smart_recorder.py"
        if not helper.is_file():
            self.failed.emit("스마트 녹화 도우미 파일을 찾을 수 없습니다.")
            return
        if self.host is not None and hasattr(self.host, "showMinimized"):
            self.host.showMinimized()
        self.process = QtCore.QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments(
            [
                str(helper), "--out", str(self.output), "--exclude-pid", str(os.getpid()), "--delay", "2",
                "--capture-vk", str(0x77), "--branch-vk", str(0x76), "--verify-vk", str(0x74),
                "--wait-vk", str(0x75), "--mode-vk", str(0x7A), "--toggle-vk", str(0x7B),
                "--stop-vk", str(0x79), "--hold-vk", str(0xC0),
                "--right-click-condition", "--rolling-preframes",
            ]
        )
        self.process.setWorkingDirectory(str(self.repository.root))
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.bar = RecordingBar(self.repository, None)
        self.bar.enable_workflow_branches()
        self.bar.stop_requested.connect(self.stop)
        self.bar.capture_requested.connect(self.request_image_capture)
        self.bar.branch_requested.connect(self.request_workflow_branch)
        self.bar.events_deleted.connect(self._delete_recorded_events)
        self.bar.manual_node_requested.connect(self._add_manual_node)
        self.bar.show()
        self.bar.raise_()
        self.bar.activateWindow()
        self.process.start()
        self._capture_poll.start()

    def _add_manual_node(self, kind: str) -> None:
        if self._capture_in_progress:
            return
        self._capture_in_progress = True
        try:
            event_time = int(max(0, self.bar.elapsed.elapsed() - 2000) if self.bar else 0)
            event_id = f"manual-{kind}-{uuid.uuid4().hex}"
            window = self._latest_target_window()
            event: dict[str, Any] = {
                "type": kind,
                "event_id": event_id,
                "t": event_time,
                "record_mode": self.bar.record_mode if self.bar else "action",
                "workflow_id": f"workflow-{self.bar.workflow_index:02d}" if self.bar else "workflow-01",
                "window": window,
            }

            if kind == "ocr":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        hint_text = "[ OCR 인식 영역 지정 ] 드래그 선택 후 Enter (Esc 취소)"
                        picker = ScreenCaptureDialog(pixmap, geometry, hint_text=hint_text)
                        if picker.exec() == QtWidgets.QDialog.Accepted:
                            screen_rect = picker.selected_screen_rect()
                            image = picker.captured_image()
                            if not image.isNull() and screen_rect.isValid():
                                payload = QtCore.QByteArray()
                                buffer = QtCore.QBuffer(payload)
                                buffer.open(QtCore.QIODevice.WriteOnly)
                                image.save(buffer, "PNG")
                                buffer.close()
                                client_rect = _resolve_live_client_rect(window)
                                search_region = [
                                    screen_rect.left() - client_rect.left() if client_rect.isValid() else screen_rect.left(),
                                    screen_rect.top() - client_rect.top() if client_rect.isValid() else screen_rect.top(),
                                    screen_rect.right() - client_rect.left() if client_rect.isValid() else screen_rect.right(),
                                    screen_rect.bottom() - client_rect.top() if client_rect.isValid() else screen_rect.bottom(),
                                ]
                                alias = f"ocr-sample-{datetime.now():%Y%m%d-%H%M%S}"
                                self.repository.add_asset_image(image, alias)
                                event.update({
                                    "asset": alias,
                                    "selected_screen_rect": [screen_rect.x(), screen_rect.y(), screen_rect.width(), screen_rect.height()],
                                    "image_sample_bmp": base64.b64encode(bytes(payload)).decode("ascii"),
                                    "image_sample_size": [image.width(), image.height()],
                                    "search_region": search_region,
                                    "region": search_region,
                                    "_review_search_region": search_region,
                                    "detail": f"OCR 영역 인식 ({image.width()}×{image.height()})",
                                })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "browser_action":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    ignored = {int(self.bar.winId())} if self.bar else set()
                    picker = WindowPickerDialog(parent=None, ignored_hwnds=ignored)
                    if picker.exec() == QtWidgets.QDialog.Accepted:
                        w_token = picker.window_token
                        w_exe = picker.exe_name
                        w_hwnd = picker.window_hwnd
                        client_pt = picker.selected_client_point() or QtCore.QPoint(0, 0)
                        screen_pt = picker.selected_screen_point()
                        window_dict = {
                            "title": w_token,
                            "exe": w_exe,
                            "hwnd": w_hwnd,
                        }
                        event.update({
                            "window": window_dict,
                            "client_x": client_pt.x(),
                            "client_y": client_pt.y(),
                            "x": screen_pt.x(),
                            "y": screen_pt.y(),
                            "detail": f"브라우저 요소 ({w_exe if w_exe else 'Web'})",
                        })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "pixel_search":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        picker = PixelColorPickerDialog(pixmap, geometry, parent=None)
                        if picker.exec() == QtWidgets.QDialog.Accepted:
                            color = picker.selected_color()
                            pt = picker.selected_point() or QtCore.QPoint(0, 0)
                            color_hex = color.name().upper() if color else "#FF0000"
                            # Region selection (optional drag select)
                            region_picker = ScreenCaptureDialog(
                                pixmap, geometry,
                                hint_text="[ 검색 영역 지정 ] 드래그로 검색 범위 선택 (전체 화면은 Enter, 취소는 Esc)"
                            )
                            search_region = [0, 0, 0, 0]
                            if region_picker.exec() == QtWidgets.QDialog.Accepted:
                                s_rect = region_picker.selected_screen_rect()
                                if s_rect.isValid() and s_rect.width() > 5 and s_rect.height() > 5:
                                    client_rect = _resolve_live_client_rect(window)
                                    if client_rect.isValid():
                                        search_region = [
                                            max(0, s_rect.left() - client_rect.left()),
                                            max(0, s_rect.top() - client_rect.top()),
                                            s_rect.right() - client_rect.left(),
                                            s_rect.bottom() - client_rect.top(),
                                        ]
                                    else:
                                        search_region = [s_rect.left(), s_rect.top(), s_rect.right(), s_rect.bottom()]
                            event.update({
                                "color": color_hex,
                                "search_region": search_region,
                                "x": pt.x(),
                                "y": pt.y(),
                                "detail": f"색상 서치 ({color_hex})",
                            })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "ocr_tracking":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        # Step 1: Capture tracking target template
                        picker1 = ScreenCaptureDialog(
                            pixmap, geometry,
                            hint_text="[ OCR 추적: 1단계 ] 추적할 기준 이미지/박스 드래그 선택 후 Enter (Esc 취소)"
                        )
                        if picker1.exec() == QtWidgets.QDialog.Accepted:
                            track_rect = picker1.selected_screen_rect()
                            track_img = picker1.captured_image()
                            if not track_img.isNull() and track_rect.isValid():
                                alias = f"track-target-{datetime.now():%Y%m%d-%H%M%S}"
                                self.repository.add_asset_image(track_img, alias)
                                # Step 2: Capture OCR area relative to tracking target
                                picker2 = ScreenCaptureDialog(
                                    pixmap, geometry,
                                    hint_text="[ OCR 추적: 2단계 ] OCR 수행할 텍스트 영역 드래그 선택 후 Enter (Esc 취소)"
                                )
                                ocr_rect = track_rect
                                if picker2.exec() == QtWidgets.QDialog.Accepted:
                                    sel_ocr = picker2.selected_screen_rect()
                                    if sel_ocr.isValid() and sel_ocr.width() > 2:
                                        ocr_rect = sel_ocr
                                offset_x = ocr_rect.left() - track_rect.left()
                                offset_y = ocr_rect.top() - track_rect.top()
                                event.update({
                                    "tracking_asset": alias,
                                    "ocr_offset_x": offset_x,
                                    "ocr_offset_y": offset_y,
                                    "ocr_width": ocr_rect.width(),
                                    "ocr_height": ocr_rect.height(),
                                    "detail": f"OCR 추적 ({alias})",
                                })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "multi_pixel_check":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        picker = MultiPixelPickerDialog(pixmap, geometry, parent=None)
                        if picker.exec() == QtWidgets.QDialog.Accepted:
                            pts = picker.selected_points()
                            if pts:
                                import json
                                event.update({
                                    "pixels": json.dumps(pts, ensure_ascii=False),
                                    "match_policy": "all",
                                    "detail": f"다중 픽셀 체크 ({len(pts)}개 점)",
                                })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "wait_color":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        picker = PixelColorPickerDialog(pixmap, geometry, parent=None)
                        if picker.exec() == QtWidgets.QDialog.Accepted:
                            color = picker.selected_color()
                            pt = picker.selected_point() or QtCore.QPoint(0, 0)
                            color_hex = color.name().upper() if color else "#FF0000"
                            event.update({
                                "target_color": color_hex,
                                "x": pt.x(),
                                "y": pt.y(),
                                "condition": "become",
                                "detail": f"색상 대기 ({color_hex} @ {pt.x()},{pt.y()})",
                            })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "color_ratio":
                if self.bar is not None:
                    self.bar._restore_position = self.bar.pos()
                    self.bar.hide()
                try:
                    pixmap, geometry = capture_virtual_desktop()
                    if not pixmap.isNull() and geometry.isValid():
                        picker = PixelColorPickerDialog(pixmap, geometry, parent=None)
                        if picker.exec() == QtWidgets.QDialog.Accepted:
                            color = picker.selected_color()
                            color_hex = color.name().upper() if color else "#FF0000"
                            region_picker = ScreenCaptureDialog(
                                pixmap, geometry,
                                hint_text="[ 게이지 영역 지정 ] 체력바/게이지 영역을 드래그 선택 후 Enter (Esc 취소)"
                            )
                            search_region = [0, 0, 0, 0]
                            if region_picker.exec() == QtWidgets.QDialog.Accepted:
                                s_rect = region_picker.selected_screen_rect()
                                if s_rect.isValid() and s_rect.width() > 4 and s_rect.height() > 4:
                                    client_rect = _resolve_live_client_rect(window)
                                    if client_rect.isValid():
                                        search_region = [
                                            max(0, s_rect.left() - client_rect.left()),
                                            max(0, s_rect.top() - client_rect.top()),
                                            s_rect.right() - client_rect.left(),
                                            s_rect.bottom() - client_rect.top(),
                                        ]
                                    else:
                                        search_region = [s_rect.left(), s_rect.top(), s_rect.right(), s_rect.bottom()]
                            event.update({
                                "target_color": color_hex,
                                "search_region": search_region,
                                "detail": f"색상 비율 게이지 ({color_hex})",
                            })
                finally:
                    if self.bar is not None:
                        self.bar.show()
            elif kind == "find_text_click":
                win_title = str((window or {}).get("title") or "")
                dlg = FindTextClickDialog(self.bar, win_title)
                if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.text():
                    rmode = dlg.region_mode()
                    event.update({
                        "find_text": dlg.text(),
                        "ocr_action": "find_click",
                        "region_mode": rmode,
                        "capture_mode": rmode,
                        "detail": f"텍스트 찾아 클릭 · '{dlg.text()}' ({'창 내부' if rmode == 'client' else '전체 화면'})",
                    })

            title = ACTION_TITLES.get(kind, kind)
            step_template: dict[str, Any] = {
                "action": "type_text" if kind in {"text", "key", "type_text"} else ("ocr" if kind == "find_text_click" else kind),
                "label": str(event.get("detail") or title),
            }
            if kind == "wait":
                step_template["duration"] = 1000
            elif kind in {"text", "key", "type_text"}:
                step_template["text"] = str(event.get("text") or "")
            elif kind in {"ocr", "find_text_click"}:
                step_template["asset"] = str(event.get("asset") or "")
                reg = event.get("region") or event.get("search_region")
                if reg:
                    step_template["region"] = list(reg)
                    step_template["search_region"] = list(reg)
                    step_template["regions"] = [list(reg)]
                step_template["engine"] = "tesseract"
                step_template["lang"] = "kor+eng"
                if kind == "find_text_click":
                    step_template["find_text"] = str(event.get("find_text") or "")
                    step_template["ocr_action"] = "find_click"
                    step_template["region_mode"] = str(event.get("region_mode") or "client")
                    step_template["match_mode"] = "contains"
                    step_template["engine_preference"] = "auto"
            elif kind == "browser_action":
                window_info = event.get("window") if isinstance(event.get("window"), dict) else {}
                step_template["window"] = str(window_info.get("title") or "")
                step_template["window_exe"] = str(window_info.get("exe") or "")
                step_template["window_hwnd"] = int(window_info.get("hwnd") or 0)
                step_template["x"] = int(event.get("client_x") or 0)
                step_template["y"] = int(event.get("client_y") or 0)
            elif kind == "pixel_search":
                step_template["color"] = str(event.get("color") or "#FF0000")
                step_template["tolerance"] = 10
                step_template["search_region"] = list(event.get("search_region") or [0, 0, 0, 0])
                step_template["action_on_found"] = "click"
                step_template["timeout"] = 3000
                step_template["poll_delay"] = 50
            elif kind == "ocr_tracking":
                step_template["tracking_asset"] = str(event.get("tracking_asset") or "")
                step_template["tracking_confidence"] = 80
                step_template["ocr_offset_x"] = int(event.get("ocr_offset_x") or 0)
                step_template["ocr_offset_y"] = int(event.get("ocr_offset_y") or 0)
                step_template["ocr_width"] = int(event.get("ocr_width") or 200)
                step_template["ocr_height"] = int(event.get("ocr_height") or 50)
                step_template["lang"] = "eng+kor"
                step_template["engine_preference"] = "auto"
                step_template["ocr_action"] = "extract"
                step_template["timeout"] = 5000
                step_template["poll_delay"] = 100
            elif kind == "multi_pixel_check":
                step_template["pixels"] = str(event.get("pixels") or "[]")
                step_template["match_policy"] = "all"
                step_template["tolerance"] = 10
                step_template["action_on_found"] = "branch"
                step_template["timeout"] = 1000
                step_template["poll_delay"] = 50
            elif kind == "wait_color":
                step_template["x"] = int(event.get("x") or 0)
                step_template["y"] = int(event.get("y") or 0)
                step_template["target_color"] = str(event.get("target_color") or "#FF0000")
                step_template["condition"] = "become"
                step_template["tolerance"] = 10
                step_template["timeout"] = 5000
                step_template["poll_delay"] = 50
            elif kind == "color_ratio":
                step_template["target_color"] = str(event.get("target_color") or "#FF0000")
                step_template["tolerance"] = 15
                step_template["search_region"] = list(event.get("search_region") or [0, 0, 0, 0])
                step_template["store_ratio_var"] = "GaugePercent"
                step_template["store_count_var"] = "PixelCount"
                step_template["condition_operator"] = "none"
                step_template["condition_value"] = 30
            elif kind == "find_text_click":
                step_template["action"] = "ocr"
                step_template["ocr_action"] = "find_click"
                step_template["find_text"] = str(event.get("find_text") or "")
                step_template["capture_mode"] = "screen"
                step_template["match_mode"] = "contains"
                step_template["lang"] = "kor+eng"
                step_template["engine_preference"] = "auto"

            # Popup detailed settings editor for the added node
            from .builder import ActionEditorDialog
            dlg = ActionEditorDialog(self.repository, step_template, parent=self.bar)
            if dlg.exec() == QtWidgets.QDialog.Accepted:
                edited_payload = dlg.payload()
                if isinstance(edited_payload, dict):
                    event["_step_payload"] = edited_payload
                    if edited_payload.get("label"):
                        event["detail"] = str(edited_payload["label"])

            self._manual_captures.append(event)
            if self.bar is not None:
                live_events = sorted(
                    [*load_recording(self.output), *self._manual_captures],
                    key=lambda item: int(item.get("t") or 0),
                )
                self.bar.update_live_events(live_events)
        finally:
            self._capture_in_progress = False

    def _delete_recorded_events(self, event_ids: list[str]) -> None:
        for identifier in event_ids:
            self._deleted_event_ids.add(str(identifier))
        self._manual_captures = [
            e for e in self._manual_captures
            if str((e if isinstance(e, dict) else {}).get("event_id") or "") not in self._deleted_event_ids
        ]

    def stop(self) -> None:
        self._manual_stop = True
        if self.process is not None and self.process.state() != QtCore.QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1200):
                self.process.kill()

    @QtCore.Slot()
    def request_workflow_branch(self) -> None:
        if sys.platform != "win32" or self.process is None or self.process.state() == QtCore.QProcess.NotRunning:
            return
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(0x76, 0, 0, 0)
            user32.keybd_event(0x76, 0, 0x0002, 0)
        except Exception:
            return

    def _read_capture_requests(self) -> list[dict[str, Any]]:
        if not self.output.is_file():
            return []
        try:
            lines = self.output.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return []
        requests: list[dict[str, Any]] = []
        latest_gate_state: bool | None = None
        latest_record_mode: str | None = None
        latest_workflow_index: int | None = None
        for line in lines:
            try:
                payload = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                if payload.get("type") == "capture_request":
                    requests.append(payload)
                elif payload.get("type") == "gate_state":
                    latest_gate_state = bool(payload.get("active"))
                    latest_record_mode = str(payload.get("mode") or latest_record_mode or "action")
                elif payload.get("type") == "mode_state":
                    latest_record_mode = str(payload.get("mode") or "action")
                elif payload.get("type") == "workflow_branch":
                    latest_workflow_index = max(1, int(payload.get("workflow_index") or 1))
        if latest_gate_state is not None and latest_gate_state != self._last_gate_state:
            self._last_gate_state = latest_gate_state
            if self.bar is not None:
                self.bar.set_gate_active(latest_gate_state)
        if latest_record_mode is not None:
            normalized_mode = "branch" if latest_record_mode.lower() == "branch" else "action"
            if normalized_mode != self._last_record_mode:
                self._last_record_mode = normalized_mode
                if self.bar is not None:
                    self.bar.set_record_mode(normalized_mode)
        if latest_workflow_index is not None and self.bar is not None:
            self.bar.set_workflow_index(latest_workflow_index)
        if self.bar is not None:
            live_events = sorted(
                [*load_recording(self.output), *self._manual_captures],
                key=lambda item: int(item.get("t") or 0),
            )
            self.bar.update_live_events(live_events)
        return requests

    def _poll_capture_requests(self) -> None:
        for request in self._read_capture_requests():
            window = request.get("window") if isinstance(request.get("window"), dict) else {}
            key = str(request.get("request_id") or f"{int(request.get('t') or 0)}-{int(window.get('hwnd') or 0)}")
            if key in self._seen_capture_requests:
                continue
            self._seen_capture_requests.add(key)
            self.request_image_capture(request)
            break

    def _latest_target_window(self) -> dict[str, Any]:
        events = load_recording(self.output)
        for event in reversed(events):
            window = event.get("window")
            if isinstance(window, dict) and window:
                return dict(window)
        return {}

    @QtCore.Slot()
    def request_image_capture(self, request: dict[str, Any] | None = None) -> None:
        if self._capture_in_progress:
            return
        self._capture_in_progress = True
        request = request if isinstance(request, dict) else {}
        window = request.get("window") if isinstance(request.get("window"), dict) else self._latest_target_window()
        record_mode = "branch" if str(request.get("mode") or self._last_record_mode).lower() == "branch" else "action"
        event_time = int(request.get("t") or (max(0, self.bar.elapsed.elapsed() - 2000) if self.bar else 0))
        prompt_search_region = bool(request.get("with_search_region") or int(request.get("vk") or 0) == 0x73)
        if self.bar is not None:
            self.bar._restore_position = self.bar.pos()
            self.bar.hide()
        QtCore.QTimer.singleShot(
            180,
            lambda: self._perform_image_capture(dict(window or {}), event_time, record_mode, prompt_search_region=prompt_search_region),
        )

    def _perform_image_capture(self, window: dict[str, Any], event_time: int, record_mode: str = "action", prompt_search_region: bool = False) -> None:
        try:
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                return
            user32 = ctypes.windll.user32
            if prompt_search_region:
                # F4 Instant Capture: Zero drag overlay dialogs, 100% instant 1-click snapshot at cursor position
                point = wintypes.POINT()
                if not user32.GetCursorPos(ctypes.byref(point)):
                    point.x, point.y = geometry.center().x(), geometry.center().y()
                screen_rect = QtCore.QRect(point.x - 48, point.y - 32, 96, 64).intersected(geometry)
                local_rect = screen_rect.translated(-geometry.left(), -geometry.top())
                image = pixmap.copy(local_rect).toImage()
                selected_center = QtCore.QPoint(point.x, point.y)
            else:
                hint1 = "[ 수동 이미지 영역 지정 ] 드래그 선택 후 Enter (Esc 취소)"
                picker = ScreenCaptureDialog(pixmap, geometry, hint_text=hint1)
                if picker.exec() != QtWidgets.QDialog.Accepted:
                    return
                screen_rect = picker.selected_screen_rect()
                image = picker.captured_image()
                if image.isNull() or not screen_rect.isValid():
                    return
                selected_center = screen_rect.center()

                minimum_width, minimum_height = 64, 48
                if screen_rect.width() < minimum_width or screen_rect.height() < minimum_height:
                    expanded = _expanded_capture_rect(screen_rect, geometry, QtCore.QSize(minimum_width, minimum_height))
                    local_rect = expanded.translated(-geometry.left(), -geometry.top())
                    image = pixmap.copy(local_rect).toImage()
                    screen_rect = expanded

            ignored_hwnds: set[int] = set()
            try:
                for widget in QtWidgets.QApplication.topLevelWidgets():
                    root = int(user32.GetAncestor(int(widget.winId()), 2) or int(widget.winId()))
                    if root:
                        ignored_hwnds.add(root)
            except Exception:
                ignored_hwnds.clear()
            detected = ActionEditor._window_target_at(selected_center, ignored_hwnds)
            if detected:
                window = {key: value for key, value in detected.items() if key != "rect"}
            elif str(window.get("exe") or "").casefold() in {"python.exe", "pythonw.exe"}:
                window = {}

            search_region: list[int] = []
            if prompt_search_region:
                client_rect = _resolve_live_client_rect(window)
                if client_rect.isValid():
                    search_region = [
                        max(0, selected_center.x() - client_rect.left() - 220),
                        max(0, selected_center.y() - client_rect.top() - 160),
                        min(client_rect.width(), selected_center.x() - client_rect.left() + 220),
                        min(client_rect.height(), selected_center.y() - client_rect.top() + 160),
                    ]
                else:
                    search_region = [
                        max(0, selected_center.x() - 220),
                        max(0, selected_center.y() - 160),
                        selected_center.x() + 220,
                        selected_center.y() + 160,
                    ]

            payload = QtCore.QByteArray()
            buffer = QtCore.QBuffer(payload)
            buffer.open(QtCore.QIODevice.WriteOnly)
            image.save(buffer, "PNG")
            buffer.close()
            center = selected_center
            origin = window.get("capture_origin") if isinstance(window.get("capture_origin"), list) else window.get("client_origin")
            if not isinstance(origin, list):
                origin = [0, 0]
            capture_event: dict[str, Any] = {
                "type": "capture",
                "event_id": f"capture-{uuid.uuid4().hex}",
                "t": int(event_time),
                "x": int(center.x()),
                "y": int(center.y()),
                "client_x": int(center.x() - int(origin[0] or 0)),
                "client_y": int(center.y() - int(origin[1] or 0)),
                "button": "Left",
                "record_mode": "branch" if record_mode == "branch" else "action",
                "window": window,
                "selected_screen_rect": [screen_rect.x(), screen_rect.y(), screen_rect.width(), screen_rect.height()],
                "image_sample_bmp": base64.b64encode(bytes(payload)).decode("ascii"),
                "image_sample_size": [image.width(), image.height()],
                "image_anchor": [int(center.x() - screen_rect.left()), int(center.y() - screen_rect.top())],
            }
            if search_region:
                capture_event["search_region"] = search_region
                capture_event["_review_search_region"] = search_region
            self._manual_captures.append(capture_event)

            if self.bar is not None:
                self.bar.show_capture_result(image.width(), image.height(), search_region_set=bool(search_region))
                live_events = sorted(
                    [*load_recording(self.output), *self._manual_captures],
                    key=lambda item: int(item.get("t") or 0),
                )
                self.bar.update_live_events(live_events)
        finally:
            self._capture_in_progress = False
            if self.bar is not None:
                self.bar.show()
                self.bar.raise_()

    def _process_error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error == QtCore.QProcess.FailedToStart:
            self.failed.emit("스마트 녹화 프로세스를 시작하지 못했습니다.")

    def _finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self._capture_poll.stop()
        live_positions = self.bar.event_positions() if self.bar is not None else {}
        live_links = self.bar.event_links() if self.bar is not None else {}
        live_multi_groups = self.bar.multi_groups() if self.bar is not None else {}
        if self.bar is not None:
            self.bar.close()
            self.bar.deleteLater()
            self.bar = None
        if self.host is not None and hasattr(self.host, "showNormal"):
            self.host.showNormal()
            self.host.raise_()
            self.host.activateWindow()
        raw_events = [*load_recording(self.output), *self._manual_captures]
        events = sorted(
            [e for e in raw_events if str((e if isinstance(e, dict) else {}).get("event_id") or "") not in self._deleted_event_ids],
            key=lambda item: int(item.get("t") or 0),
        )
        for event in events:
            event_id = str(event.get("event_id") or "")
            if event_id in live_positions:
                event["_live_position"] = list(live_positions[event_id])
            if event_id in live_multi_groups:
                event["_review_multi_group"] = live_multi_groups[event_id]
            event_links: dict[str, Any] = {}
            for kind in ("success", "fail"):
                key = (event_id, kind)
                if key in live_links:
                    value = live_links[key]
                    cand_key = "success_candidates" if kind == "success" else "fail_candidates"
                    if isinstance(value, list):
                        event_links[cand_key] = list(value)
                    else:
                        event_links[kind] = value
            if event_links:
                event["_live_links"] = event_links
        try:
            self.output.unlink(missing_ok=True)
        except OSError:
            pass
        if not events:
            detail = "기록된 동작이 없습니다. F9로 세션을 연 뒤 ` 키로 기록을 켜고 작업한 다음 F10으로 종료하세요."
            if exit_code not in {0, 15} and not self._manual_stop:
                detail += f" (종료 코드 {exit_code})"
            self.failed.emit(detail)
            self.deleteLater()
            return
        self.completed.emit(events)
        self.deleteLater()


class RecordingReviewDialog(QtWidgets.QDialog):
    events_changed = QtCore.Signal(list)

    def __init__(self, events: list[dict[str, Any]], repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.events = events
        self.drafts = recording_drafts(events, include_waits=False)
        self.crop_sizes: dict[int, QtCore.QSize] = {}
        self.crop_rects: dict[int, QtCore.QRect] = {}
        self.crop_shapes: dict[int, str] = {}
        self.detail_images: dict[int, QtGui.QImage] = {}
        self.detail_precise_rows: set[int] = set()
        self.detail_click_offsets: dict[int, QtCore.QPoint] = {}
        self.search_regions: dict[int, list[int]] = {}
        self.preview_labels: dict[int, QtWidgets.QLabel] = {}
        self.preview_buttons: dict[int, QtWidgets.QPushButton] = {}
        self.search_region_buttons: dict[int, QtWidgets.QPushButton] = {}
        for row, draft in enumerate(self.drafts):
            event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
            saved_rect = event.get("_review_crop_rect") if isinstance(event.get("_review_crop_rect"), list) else []
            if len(saved_rect) >= 4:
                rect = QtCore.QRect(*(int(value or 0) for value in saved_rect[:4]))
                if rect.isValid():
                    self.crop_rects[row] = rect
                    self.crop_sizes[row] = rect.size()
            if str(event.get("_review_crop_shape") or "") in {"rect", "circle"}:
                self.crop_shapes[row] = str(event["_review_crop_shape"])
            detail = _recorded_detail_image(event)
            if not detail.isNull():
                self.detail_images[row] = detail
                if bool(event.get("_review_detail_precise", True)):
                    self.detail_precise_rows.add(row)
            click_offset = event.get("_review_detail_click_offset") if isinstance(event.get("_review_detail_click_offset"), list) else []
            if len(click_offset) >= 2:
                self.detail_click_offsets[row] = QtCore.QPoint(int(click_offset[0] or 0), int(click_offset[1] or 0))
            saved_search_region = event.get("_review_search_region") if isinstance(event.get("_review_search_region"), list) else []
            if len(saved_search_region) >= 4:
                values = [int(value or 0) for value in saved_search_region[:4]]
                if values[2] > values[0] and values[3] > values[1]:
                    self.search_regions[row] = values
        self.setWindowTitle("스마트 녹화 검토")
        self.resize(1540, 840)
        self.setMinimumSize(1280, 700)
        layout = QtWidgets.QVBoxLayout(self)
        self.title_label = QtWidgets.QLabel()
        self.title_label.setStyleSheet("font-size:15pt; font-weight:800;")
        self._update_title()
        hint = QtWidgets.QLabel(
            "불필요한 행은 여러 개 선택해 제거할 수 있습니다. 내용은 행을 더블클릭하거나 ‘선택 내용 편집’을 사용하세요."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(self.title_label)
        layout.addWidget(hint)

        row_tools = QtWidgets.QHBoxLayout()
        self.remove_rows_button = QtWidgets.QPushButton("－ 선택 행 제거")
        self.remove_rows_button.setToolTip("여러 행을 Ctrl/Shift로 선택한 뒤 한 번에 제거합니다. Delete 키도 사용할 수 있습니다.")
        self.remove_rows_button.clicked.connect(self._remove_selected_rows)
        self.edit_content_button = QtWidgets.QPushButton("✎ 선택 내용 편집")
        self.edit_content_button.setToolTip("텍스트, 키 입력 또는 대기 시간을 넓은 편집창에서 수정합니다.")
        self.edit_content_button.clicked.connect(self._edit_selected_row)
        self.merge_multi_image_button = QtWidgets.QPushButton("▦ 선택 이미지를 멀티 서치로 묶기")
        self.merge_multi_image_button.setToolTip(
            "Ctrl/Shift로 이미지 캡처·이미지 인식 행을 2개 이상 선택하면 한 개의 멀티 이미지 서치 노드로 생성합니다."
        )
        self.merge_multi_image_button.clicked.connect(self._merge_selected_images)
        selection_hint = QtWidgets.QLabel("Ctrl/Shift 다중 선택 · Delete 제거 · 내용 더블클릭 편집")
        selection_hint.setObjectName("Muted")
        row_tools.addWidget(self.remove_rows_button)
        row_tools.addWidget(self.edit_content_button)
        row_tools.addWidget(self.merge_multi_image_button)
        row_tools.addWidget(selection_hint)
        row_tools.addStretch(1)
        layout.addLayout(row_tools)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["사용", "동작", "내용", "대상", "인식 방식", "이미지 영역"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(54)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.Interactive)
        self.table.setColumnWidth(0, 62)
        self.table.setColumnWidth(1, 82)
        self.table.setColumnWidth(3, 250)
        self.table.setColumnWidth(4, 240)
        self.table.setColumnWidth(5, 420)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.delete_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Delete, self)
        self.delete_shortcut.setContext(QtCore.Qt.WindowShortcut)
        self.delete_shortcut.activated.connect(self._remove_rows_from_shortcut)
        self._populate_table()
        layout.addWidget(self.table, 1)

        options = QtWidgets.QGridLayout()
        self.include_text = QtWidgets.QCheckBox("입력한 텍스트를 노드에 저장")
        self.include_text.setChecked(True)
        self.include_text.setToolTip("암호나 개인정보를 입력했다면 체크를 해제하세요.")
        self.connect_steps = QtWidgets.QCheckBox("작업 내부 자동 연결 · 첫 조건끼리 우선순위 검사")
        self.connect_steps.setChecked(True)
        self.connect_steps.setToolTip(
            "F7로 나눈 각 작업 안쪽만 순서대로 연결합니다. 작업 1·2·3의 첫 노드는 위에서부터 검사하고, "
            "최초로 성공한 작업 하나만 끝까지 실행합니다. 전부 실패하면 종료합니다."
        )
        self.background_clicks = QtWidgets.QCheckBox("모든 클릭을 백그라운드 클릭으로 생성")
        self.background_clicks.setChecked(True)
        self.background_clicks.setToolTip(
            "ControlClick/PostMessage를 이용해 창을 활성화하지 않고 클릭합니다. 일부 게임·브라우저 캔버스는 비활성 입력을 차단할 수 있습니다."
        )
        self.background_clicks.toggled.connect(self._apply_background_click_mode)
        self.precise_images = QtWidgets.QCheckBox("크기 변화까지 정밀 보정")
        self.precise_images.setChecked(False)
        self.precise_images.setToolTip("꺼도 AHK 고속 확인 후 OpenCV 1배율 보정이 자동 실행됩니다. 대상 크기가 달라질 때만 켜세요.")
        options.addWidget(self.include_text, 0, 0)
        options.addWidget(self.connect_steps, 0, 1)
        options.addWidget(self.background_clicks, 1, 0)
        options.addWidget(self.precise_images, 1, 1)
        options.setColumnStretch(2, 1)
        layout.addLayout(options)
        self.target_mode = "append"
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(8)

        self.btn_append = QtWidgets.QPushButton("✚ 기존 매크로에 추가")
        self.btn_append.setToolTip("현재 편집 중인 매크로 뒤에 녹화 노드를 추가합니다.")
        self.btn_append.clicked.connect(self._accept_append)

        self.btn_create_new = QtWidgets.QPushButton("✨ 새 매크로 생성")
        self.btn_create_new.setToolTip("녹화된 노드로 새로운 매크로 파일(빌더)을 생성합니다.")
        self.btn_create_new.setStyleSheet(
            f"background:{COLORS['accent']}; color:#000000; font-weight:800; padding:6px 14px;"
        )
        self.btn_create_new.clicked.connect(self._accept_create_new)

        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_cancel.clicked.connect(self.reject)

        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_append)
        button_layout.addWidget(self.btn_create_new)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)
        self._apply_background_click_mode(True)

    def _accept_append(self) -> None:
        self.target_mode = "append"
        self.accept()

    def _accept_create_new(self) -> None:
        self.target_mode = "new"
        self.accept()

    def _update_title(self) -> None:
        self.title_label.setText(f"기록된 동작 {len(self.events)}개를 자동화 노드 {len(self.drafts)}개로 정리했습니다.")

    def _display_content(self, draft: dict[str, Any]) -> str:
        kind = str(draft.get("kind") or "")
        if kind == "text":
            return str(draft.get("text") or "")
        if kind == "key":
            return "{" + str(draft.get("token") or "") + "}"
        return str(draft.get("detail") or "")

    def _populate_table(self) -> None:
        self.table.clearContents()
        self.table.setRowCount(len(self.drafts))
        self.preview_labels.clear()
        self.preview_buttons.clear()
        self.search_region_buttons.clear()
        kind_labels = {
            "mouse": "클릭",
            "mouse_drag": "드래그",
            "image_capture": "이미지",
            "screen_condition": "화면 조건",
            "screen_verification": "결과 확인",
            "text": "텍스트",
            "key": "키",
            "wait": "대기",
        }
        for row, draft in enumerate(self.drafts):
            use_item = QtWidgets.QTableWidgetItem()
            use_item.setCheckState(QtCore.Qt.Checked if draft.get("_review_enabled", True) else QtCore.Qt.Unchecked)
            self.table.setItem(row, 0, use_item)
            kind_label = kind_labels.get(str(draft.get("kind")), "동작")
            if str(draft.get("record_mode") or "action") == "branch":
                kind_label = "분기 " + kind_label
            if draft.get("_review_multi_group"):
                kind_label = "멀티 " + kind_label
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(kind_label))
            if draft.get("kind") == "wait":
                duration = WheelSafeSpinBox()
                duration.setRange(0, 3_600_000)
                duration.setSingleStep(100)
                duration.setSuffix(" ms")
                duration.setMinimumWidth(180)
                duration.setMinimumHeight(36)
                duration.setValue(int(draft.get("duration") or 500))
                duration.setToolTip("대기 시간을 밀리초 단위로 직접 입력합니다. 1000 ms = 1초")
                duration.valueChanged.connect(
                    lambda value, target=draft: self._set_wait_duration(target, value)
                )
                self.table.setCellWidget(row, 2, duration)
            else:
                content_item = QtWidgets.QTableWidgetItem(self._display_content(draft))
                content_item.setToolTip(self._display_content(draft))
                self.table.setItem(row, 2, content_item)
            target_text = str(draft.get("target") or "")
            profile = draft.get("handle_profile") if isinstance(draft.get("handle_profile"), dict) else {}
            if profile:
                handle_label = str(profile.get("target_control") or profile.get("target_hwnd") or "최상위 창")
                target_text = f"{target_text}  ⌑ {handle_label} · {int(profile.get('x') or 0)}, {int(profile.get('y') or 0)}"
            target_item = QtWidgets.QTableWidgetItem(target_text)
            if profile:
                target_item.setToolTip(
                    "시험 완료된 비활성 클릭 데이터\n"
                    f"ClassNN: {profile.get('target_control') or '(최상위 창)'}\n"
                    f"HWND: {profile.get('target_hwnd') or '-'}\n"
                    f"대상 창 좌표: {int(profile.get('x') or 0)}, {int(profile.get('y') or 0)}"
                )
            self.table.setItem(row, 3, target_item)
            if draft.get("kind") in {"mouse", "mouse_drag", "image_capture", "screen_condition", "screen_verification"}:
                combo = QtWidgets.QComboBox()
                if draft.get("kind") in {"screen_condition", "screen_verification"}:
                    combo.addItem("이미지가 보이는지 확인 · 클릭 안 함", "image")
                    event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
                    image = _recorded_sample_image(event)
                    if not image.isNull() and row not in self.crop_rects:
                        self.crop_sizes[row] = QtCore.QSize(min(128, image.width()), min(88, image.height()))
                elif draft.get("kind") == "image_capture":
                    combo.addItem("직접 캡처 이미지 찾아 클릭", "image")
                    event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
                    image = _recorded_sample_image(event)
                    if not image.isNull() and row not in self.crop_rects:
                        self.crop_sizes[row] = image.size()
                        self.crop_rects[row] = image.rect()
                elif draft.get("kind") == "mouse_drag":
                    combo.addItem("창 활성화 후 드래그 · 위치 변경 대응", "window")
                    combo.addItem("백그라운드 드래그 · 호환 앱 전용", "inactive")
                    combo.addItem("화면 절대 좌표 드래그", "screen")
                else:
                    combo.addItem("창 활성화 후 클릭 · 권장", "window")
                    combo.addItem("이미지 찾아 클릭 · 위치 변경 대응", "image")
                    if profile:
                        combo.addItem("핸들 엔진 · 시험 저장됨", "handle_probe")
                    combo.addItem("백그라운드 클릭 · 호환 앱 전용", "inactive")
                    combo.addItem("화면 절대 좌표 · 그대로 재현", "screen")
                event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
                if event.get("with_search_region") or str(event.get("source_control") or "") == "F4_Click":
                    saved_strategy = "image"
                    if row not in self.search_regions:
                        search_reg = event.get("search_region")
                        if isinstance(search_reg, list) and len(search_reg) >= 4:
                            self.search_regions[row] = [int(search_reg[0]), int(search_reg[1]), int(search_reg[2]), int(search_reg[3])]
                        else:
                            cx = int(event.get("client_x") or 0)
                            cy = int(event.get("client_y") or 0)
                            self.search_regions[row] = [max(0, cx - 220), max(0, cy - 160), cx + 220, cy + 160]
                else:
                    saved_strategy = str(draft.get("_review_strategy") or "")
                    if not saved_strategy:
                        sample = _recorded_sample_image(event)
                        if not sample.isNull() or draft.get("kind") == "image_capture" or event.get("image_sample_bmp") or event.get("sample_image"):
                            saved_strategy = "image"
                        else:
                            saved_strategy = str(draft.get("strategy") or "window")
                saved_index = combo.findData(saved_strategy)
                if saved_index >= 0:
                    combo.setCurrentIndex(saved_index)
                self.table.setCellWidget(row, 4, combo)
                event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
                is_wheel = str(event.get("button") or "") in {"WheelUp", "WheelDown"}
                if draft.get("kind") == "mouse_drag" or is_wheel:
                    self.table.setItem(row, 5, QtWidgets.QTableWidgetItem("—"))
                else:
                    self.table.setCellWidget(row, 5, self._build_image_preview_cell(row))
                    self.table.setRowHeight(row, 62)
            else:
                self.table.setItem(row, 4, QtWidgets.QTableWidgetItem("자동 정리"))
                self.table.setItem(row, 5, QtWidgets.QTableWidgetItem("—"))

    def _set_wait_duration(self, draft: dict[str, Any], value: int) -> None:
        draft["duration"] = int(value)
        draft["detail"] = f"화면 반응 대기 {int(value)} ms"

    def _sync_table_state(self) -> None:
        """Keep per-row choices intact when editing or removing causes a table rebuild."""
        for row, draft in enumerate(self.drafts):
            use_item = self.table.item(row, 0)
            if use_item is not None:
                draft["_review_enabled"] = use_item.checkState() == QtCore.Qt.Checked
            strategy = self.table.cellWidget(row, 4)
            if isinstance(strategy, QtWidgets.QComboBox):
                draft["_review_strategy"] = str(strategy.currentData() or "window")

    def _selected_rows(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not rows and self.table.currentRow() >= 0:
            rows.add(self.table.currentRow())
        return sorted(rows)

    def _apply_handle_profile(self, row: int, payload: dict[str, Any], strategy: str | None = None) -> None:
        if not 0 <= row < len(self.drafts) or not payload:
            return
        self._sync_table_state()
        draft = self.drafts[row]
        profile = dict(payload)
        draft["handle_profile"] = profile
        event = draft.get("event") if isinstance(draft.get("event"), dict) else None
        if event is not None:
            event["_handle_profile"] = dict(profile)
        if strategy:
            draft["_review_strategy"] = strategy
        elif str(draft.get("_review_strategy") or "") != "image":
            draft["_review_strategy"] = "handle_probe"
        self._populate_table()
        self._apply_background_click_mode(self.background_clicks.isChecked())
        self.table.selectRow(row)
        self.events_changed.emit(self.events)

    def _remove_rows_from_shortcut(self) -> None:
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)):
            return
        self._remove_selected_rows()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self._remove_rows_from_shortcut()
            event.accept()
            return
        super().keyPressEvent(event)

    def _merge_selected_images(self) -> None:
        rows = self._selected_rows()
        if len(rows) < 2:
            QtWidgets.QMessageBox.information(self, "멀티 이미지 서치", "이미지 행을 2개 이상 선택해 주세요.")
            return
        self._sync_table_state()
        invalid = [
            row for row in rows
            if self.drafts[row].get("kind") not in {"mouse", "image_capture"}
            or str(self.drafts[row].get("_review_strategy") or "") != "image"
        ]
        if invalid:
            QtWidgets.QMessageBox.information(
                self,
                "멀티 이미지 서치",
                "선택한 모든 행의 인식 방식을 ‘이미지 찾아 클릭’으로 먼저 바꿔 주세요.",
            )
            return
        group_id = f"review-multi-{datetime.now():%Y%m%d%H%M%S%f}"
        selected_set = set(rows)
        for row, draft in enumerate(self.drafts):
            if row in selected_set:
                draft["_review_multi_group"] = group_id
                event = draft.get("event") if isinstance(draft.get("event"), dict) else None
                if event is not None:
                    event["_review_multi_group"] = group_id
            elif draft.get("_review_multi_group") == group_id:
                draft.pop("_review_multi_group", None)
        self._populate_table()
        for row in rows:
            self.table.selectRow(row)
        self.events_changed.emit(self.events)

    def _remove_selected_rows(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "행 제거", "제거할 행을 먼저 선택해 주세요.")
            return
        self._sync_table_state()
        removed = set(rows)
        old_crop_sizes = dict(self.crop_sizes)
        old_crop_rects = dict(self.crop_rects)
        old_crop_shapes = dict(self.crop_shapes)
        old_detail_images = dict(self.detail_images)
        old_detail_precise = set(self.detail_precise_rows)
        old_detail_click_offsets = dict(self.detail_click_offsets)
        old_search_regions = dict(self.search_regions)
        remaining: list[dict[str, Any]] = []
        self.crop_sizes = {}
        self.crop_rects = {}
        self.crop_shapes = {}
        self.detail_images = {}
        self.detail_precise_rows = set()
        self.detail_click_offsets = {}
        self.search_regions = {}
        new_row = 0
        for old_row, draft in enumerate(self.drafts):
            if old_row in removed:
                continue
            remaining.append(draft)
            if old_row in old_crop_sizes:
                self.crop_sizes[new_row] = old_crop_sizes[old_row]
            if old_row in old_crop_rects:
                self.crop_rects[new_row] = old_crop_rects[old_row]
            if old_row in old_crop_shapes:
                self.crop_shapes[new_row] = old_crop_shapes[old_row]
            if old_row in old_detail_images:
                self.detail_images[new_row] = old_detail_images[old_row]
            if old_row in old_detail_precise:
                self.detail_precise_rows.add(new_row)
            if old_row in old_detail_click_offsets:
                self.detail_click_offsets[new_row] = old_detail_click_offsets[old_row]
            if old_row in old_search_regions:
                self.search_regions[new_row] = old_search_regions[old_row]
            new_row += 1
        self.drafts = remaining
        self._populate_table()
        self._apply_background_click_mode(self.background_clicks.isChecked())
        self._update_title()
        if self.drafts:
            self.table.selectRow(min(rows[0], len(self.drafts) - 1))
        self.events_changed.emit(self.events)

    def _edit_selected_row(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "내용 편집", "편집할 행을 먼저 선택해 주세요.")
            return
        self._edit_row_details(rows[0])

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        self._edit_row_details(row)

    def _edit_row_details(self, row: int) -> None:
        if not 0 <= row < len(self.drafts):
            return
        self._sync_table_state()
        draft = self.drafts[row]
        kind = str(draft.get("kind") or "")
        event = draft.get("event") if isinstance(draft.get("event"), dict) else {}

        from .action_editor import ActionEditorDialog

        step = dict(draft.get("step") or {})
        if not step:
            action_type = "type_text" if kind in {"text", "key", "type_text"} else ("ocr" if kind == "find_text_click" else kind)
            if kind in {"mouse", "image_capture"}:
                action_type = "image_search"
            elif kind in {"screen_verification", "screen_condition"}:
                action_type = "screen_condition"
            elif kind == "mouse_drag":
                action_type = "inactive_click"

            step = {
                "action": action_type,
                "label": str(draft.get("detail") or kind),
            }
            if kind == "wait":
                step["duration"] = int(draft.get("duration") or event.get("duration") or 500)
            elif kind in {"text", "type_text"}:
                step["text"] = str(draft.get("text") or event.get("text") or "")
            elif kind == "key":
                step["text"] = "{" + str(draft.get("token") or event.get("token") or "") + "}"
            elif kind in {"mouse", "image_capture", "screen_condition", "screen_verification"}:
                alias = str(event.get("asset") or draft.get("asset") or "")
                if alias:
                    step["asset"] = alias
                    step["assets"] = [alias]

            for k, v in event.items():
                if not k.startswith("_") and k not in step:
                    step[k] = v

            reg = event.get("region") or event.get("search_region") or event.get("_review_search_region")
            if reg:
                step["region"] = list(reg)
                step["search_region"] = list(reg)

        dialog = ActionEditorDialog(self.repository, step, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            updated = dialog.step()
            draft["step"] = updated
            draft["detail"] = updated.get("label", draft.get("detail"))
            if updated.get("text") and kind in {"text", "type_text"}:
                draft["text"] = updated["text"]
            if updated.get("duration") and kind == "wait":
                draft["duration"] = updated["duration"]
                self._set_wait_duration(draft, updated["duration"])
            if isinstance(event, dict):
                event.update(updated)
                if updated.get("region"):
                    event["_review_search_region"] = list(updated["region"])
                    self.search_regions[row] = list(updated["region"])
            self._populate_table()
            self.table.selectRow(row)

    def _apply_background_click_mode(self, enabled: bool) -> None:
        """Apply one recording-wide click policy without hiding per-row overrides."""
        for row, draft in enumerate(self.drafts):
            if draft.get("kind") not in {"mouse", "mouse_drag"}:
                continue
            combo = self.table.cellWidget(row, 4)
            if not isinstance(combo, QtWidgets.QComboBox):
                continue
            profile = draft.get("handle_profile") if isinstance(draft.get("handle_profile"), dict) else {}
            current = str(combo.currentData() or "")
            event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
            sample = _recorded_sample_image(event)
            has_image = not sample.isNull() or draft.get("kind") == "image_capture" or bool(event.get("image_sample_bmp")) or bool(event.get("sample_image"))
            requested = str(draft.get("_review_strategy") or "")
            if profile and (current == "handle_probe" or requested == "handle_probe"):
                target = "handle_probe"
            elif current == "image" or has_image:
                target = "image"
            elif profile:
                target = "handle_probe"
            else:
                target = "inactive" if enabled else "window"
            index = combo.findData(target)
            if index >= 0:
                combo.setCurrentIndex(index)
                draft["_review_strategy"] = target

    def _build_image_preview_cell(self, row: int) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(holder)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)
        preview = QtWidgets.QLabel("미리보기")
        preview.setFixedSize(72, 48)
        preview.setAlignment(QtCore.Qt.AlignCenter)
        preview.setStyleSheet("background:#0D1017; border:1px solid #3B4355; border-radius:6px; color:#8F99AD;")
        edit = QtWidgets.QPushButton("확인 이미지 편집")
        edit.setToolTip("실제로 저장될 이미지 표본을 확인하고 자르기·누끼·클릭 오프셋을 편집합니다.")
        edit.clicked.connect(lambda _checked=False, target_row=row: self._adjust_image_crop(target_row))
        search_region = QtWidgets.QPushButton("서치 영역 지정")
        search_region.setToolTip(
            "현재 대상 프로그램의 클라이언트 화면에서 검색할 범위를 드래그합니다. 창을 이동해도 상대 영역이 유지됩니다."
        )
        search_region.clicked.connect(lambda _checked=False, target_row=row: self._select_search_region(target_row))
        layout.addWidget(preview)
        layout.addWidget(edit)
        layout.addWidget(search_region)
        self.preview_labels[row] = preview
        self.preview_buttons[row] = edit
        self.search_region_buttons[row] = search_region
        if row in self.search_regions:
            region = self.search_regions[row]
            search_region.setText(f"서치 영역 {region[2] - region[0]}×{region[3] - region[1]}")
        QtCore.QTimer.singleShot(0, lambda target_row=row: self._refresh_row_preview(target_row))
        return holder

    @staticmethod
    def _live_client_rect(window: dict[str, Any]) -> QtCore.QRect:
        """Resolve the current client rectangle instead of trusting recording-time screen coordinates."""
        if sys.platform != "win32" or not window:
            return QtCore.QRect()
        try:
            from .image_search_test import _find_window

            saved_hwnd = int(window.get("hwnd") or 0)
            user32 = ctypes.windll.user32
            hwnd = saved_hwnd if saved_hwnd and user32.IsWindow(saved_hwnd) else _find_window(
                str(window.get("exe") or ""), _window_token(window)
            )
            if not hwnd:
                return QtCore.QRect()
            rect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                return QtCore.QRect()
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            return QtCore.QRect(int(origin.x), int(origin.y), width, height) if width >= 4 and height >= 4 else QtCore.QRect()
        except Exception:
            return QtCore.QRect()

    def _select_search_region(self, row: int) -> None:
        if not 0 <= row < len(self.drafts):
            return
        draft = self.drafts[row]
        event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
        window = event.get("window") if isinstance(event.get("window"), dict) else {}
        client_rect = self._live_client_rect(window)
        if not client_rect.isValid():
            QtWidgets.QMessageBox.warning(
                self,
                "서치 영역 지정",
                "녹화한 대상 프로그램의 현재 창을 찾지 못했습니다. 대상 프로그램을 실행한 뒤 다시 눌러 주세요.",
            )
            return
        self.hide()
        QtWidgets.QApplication.processEvents()
        pixmap, desktop_geometry = capture_virtual_desktop()
        if pixmap.isNull() or not desktop_geometry.isValid():
            self.show()
            return
        clipped = client_rect.intersected(desktop_geometry)
        if not clipped.isValid():
            self.show()
            QtWidgets.QMessageBox.warning(self, "서치 영역 지정", "대상 프로그램의 클라이언트 화면이 현재 모니터에 보이지 않습니다.")
            return
        local = clipped.translated(-desktop_geometry.topLeft())
        client_pixmap = pixmap.copy(local)
        picker = ScreenCaptureDialog(client_pixmap, clipped, accept_on_release=False)
        try:
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return
            selected = picker.selected_screen_rect().intersected(client_rect)
            if selected.width() < 4 or selected.height() < 4:
                return
            region = [
                selected.left() - client_rect.left(),
                selected.top() - client_rect.top(),
                selected.left() - client_rect.left() + selected.width(),
                selected.top() - client_rect.top() + selected.height(),
            ]
            self.search_regions[row] = region
            event["_review_search_region"] = list(region)
            combo = self.table.cellWidget(row, 4)
            if isinstance(combo, QtWidgets.QComboBox):
                image_index = combo.findData("image")
                if image_index >= 0:
                    combo.setCurrentIndex(image_index)
                    draft["_review_strategy"] = "image"
            button = self.search_region_buttons.get(row)
            if button is not None:
                button.setText(f"서치 영역 {region[2] - region[0]}×{region[3] - region[1]}")
                button.setToolTip(
                    f"클라이언트 상대 범위 · 왼쪽 {region[0]}, 위 {region[1]}, 오른쪽 {region[2]}, 아래 {region[3]}\n"
                    "다시 클릭하면 현재 창에서 범위를 재지정합니다."
                )
            self.events_changed.emit(self.events)
        finally:
            picker.deleteLater()
            self.show()
            self.raise_()
            self.activateWindow()

    def _refresh_row_preview(self, row: int) -> None:
        if not 0 <= row < len(self.drafts):
            return
        event = self.drafts[row].get("event") if isinstance(self.drafts[row].get("event"), dict) else {}
        image = _recorded_sample_image(event)
        label = self.preview_labels.get(row)
        if label is None or image.isNull():
            return
        detail = self.detail_images.get(row)
        if detail is not None and not detail.isNull():
            label.setPixmap(
                QtGui.QPixmap.fromImage(detail).scaled(label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            )
            label.setToolTip(
                f"상세 편집된 저장 이미지 · {detail.width()}×{detail.height()} · "
                f"{'3단계 정밀 검색' if row in self.detail_precise_rows else '일반 검색'}"
            )
            return
        anchor_values = event.get("image_anchor") if isinstance(event.get("image_anchor"), list) else []
        anchor = QtCore.QPoint(
            int(anchor_values[0]) if len(anchor_values) >= 2 else image.width() // 2,
            int(anchor_values[1]) if len(anchor_values) >= 2 else image.height() // 2,
        )
        size = self.crop_sizes.get(row, QtCore.QSize(96, 64))
        crop_rect = self.crop_rects.get(row, _centered_crop_rect(image, anchor, size))
        cropped = image.copy(crop_rect.intersected(image.rect()))
        label.setPixmap(QtGui.QPixmap.fromImage(cropped).scaled(label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        label.setToolTip(f"저장 예정 이미지 · {cropped.width()}×{cropped.height()}")

    def _adjust_image_crop(self, row: int) -> None:
        if not 0 <= row < len(self.drafts):
            return
        draft = self.drafts[row]
        event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
        image = _recorded_sample_image(event)
        if image.isNull():
            QtWidgets.QMessageBox.warning(self, "이미지 영역", "이 클릭의 녹화 화면을 불러오지 못했습니다. 새 버전에서 다시 녹화해 주세요.")
            return
        initial = self.crop_sizes.get(row)
        if initial is None and draft.get("kind") == "image_capture":
            initial = image.size()
        anchor_values = event.get("image_anchor") if isinstance(event.get("image_anchor"), list) else []
        anchor = QtCore.QPoint(
            int(anchor_values[0]) if len(anchor_values) >= 2 else image.width() // 2,
            int(anchor_values[1]) if len(anchor_values) >= 2 else image.height() // 2,
        )
        dialog = RecordedImageCropDialog(
            image,
            initial,
            self,
            anchor=anchor,
            initial_rect=self.crop_rects.get(row),
            initial_detail_image=self.detail_images.get(row),
            precise_search=row in self.detail_precise_rows,
            initial_click_offset=self.detail_click_offsets.get(row),
        )
        previous_shape = self.crop_shapes.get(row, "rect")
        shape_index = dialog.shape_combo.findData(previous_shape)
        dialog.shape_combo.setCurrentIndex(max(0, shape_index))
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self.crop_sizes[row] = dialog.crop_size()
        self.crop_rects[row] = dialog.crop_rect()
        self.crop_shapes[row] = dialog.guide_shape()
        detail = dialog.detail_image()
        if detail.isNull():
            self.detail_images.pop(row, None)
            self.detail_precise_rows.discard(row)
            self.detail_click_offsets.pop(row, None)
            event.pop("_review_edited_image_bmp", None)
            event.pop("_review_detail_precise", None)
            event.pop("_review_detail_click_offset", None)
        else:
            self.detail_images[row] = detail
            if dialog.precise_search_enabled():
                self.detail_precise_rows.add(row)
            else:
                self.detail_precise_rows.discard(row)
            event["_review_edited_image_bmp"] = _encoded_png(detail)
            event["_review_detail_precise"] = dialog.precise_search_enabled()
            click_offset = dialog.detail_click_offset()
            if click_offset is None:
                self.detail_click_offsets.pop(row, None)
                event.pop("_review_detail_click_offset", None)
            else:
                self.detail_click_offsets[row] = click_offset
                event["_review_detail_click_offset"] = [click_offset.x(), click_offset.y()]
        rect = dialog.crop_rect()
        event["_review_crop_rect"] = [rect.x(), rect.y(), rect.width(), rect.height()]
        event["_review_crop_shape"] = dialog.guide_shape()
        strategy = self.table.cellWidget(row, 4)
        if isinstance(strategy, QtWidgets.QComboBox):
            strategy.setCurrentIndex(strategy.findData("image"))
        button = self.preview_buttons.get(row)
        if button is not None:
            size = dialog.crop_size()
            suffix = " · 상세" if row in self.detail_images else ""
            button.setText(f"{size.width()}×{size.height()}{suffix}")
        self._refresh_row_preview(row)
        self.events_changed.emit(self.events)

    def build_steps(self) -> list[dict[str, Any]]:
        self._sync_table_state()
        selected: list[tuple[int, dict[str, Any], str]] = []
        for row, draft in enumerate(self.drafts):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != QtCore.Qt.Checked:
                continue
            if draft.get("kind") == "text" and not self.include_text.isChecked():
                continue
            widget = self.table.cellWidget(row, 4)
            strategy = str(widget.currentData() or "window") if isinstance(widget, QtWidgets.QComboBox) else "auto"
            selected.append((row, draft, strategy))
        need_capture = any(
            draft.get("kind") in {"mouse", "image_capture", "screen_condition", "screen_verification"}
            and strategy == "image"
            and not str((draft.get("event") or {}).get("image_sample_bmp") or "")
            for _row, draft, strategy in selected
        )
        screen_pixmap, screen_geometry = capture_virtual_desktop() if need_capture else (QtGui.QPixmap(), QtCore.QRect())
        steps: list[dict[str, Any]] = []
        for order, (row, draft, strategy) in enumerate(selected, start=1):
            kind = draft.get("kind")
            record_mode = "branch" if str(draft.get("record_mode") or "action") == "branch" else "action"
            if draft.get("step") and isinstance(draft["step"], dict):
                step_obj = dict(draft["step"])
                step_obj["_recording_mode"] = record_mode
                step_obj["workflow_id"] = str(draft.get("workflow_id") or "")
                step_obj["workflow_label"] = f"스마트 작업 {int(draft.get('workflow_index') or 1)}"
                steps.append(step_obj)
                continue
            if kind == "wait":
                steps.append(
                    {
                        "action": "wait",
                        "label": "화면 반응 대기",
                        "duration": int(draft.get("duration") or 500),
                        "_recording_mode": record_mode,
                        "workflow_id": str(draft.get("workflow_id") or ""),
                        "workflow_label": f"스마트 작업 {int(draft.get('workflow_index') or 1)}",
                        "_live_position": list(((draft.get("event") or {}).get("_live_position") or []))[:2],
                        "_recording_event_ids": [str(((draft.get("event") or {}).get("event_id") or ""))],
                        "_live_links": dict(((draft.get("event") or {}).get("_live_links") or {})),
                    }
                )
                continue
            if kind == "text":
                steps.append(
                    {
                        "action": "type_text",
                        "label": f"녹화 텍스트 {order}",
                        "text": str(draft.get("text") or ""),
                        "send_mode": "raw",
                        "mode": "active",
                        "_recording_mode": record_mode,
                        "workflow_id": str(draft.get("workflow_id") or ""),
                        "workflow_label": f"스마트 작업 {int(draft.get('workflow_index') or 1)}",
                        "_live_position": list(((draft.get("event") or {}).get("_live_position") or []))[:2],
                        "_recording_event_ids": [str(((draft.get("event") or {}).get("event_id") or ""))],
                        "_live_links": dict(((draft.get("event") or {}).get("_live_links") or {})),
                    }
                )
                continue
            if kind == "key":
                token = str(draft.get("token") or "")
                steps.append(
                    {
                        "action": "type_text",
                        "label": f"{token} 키 입력",
                        "text": "{" + token + "}",
                        "send_mode": "input",
                        "mode": "active",
                        "_recording_mode": record_mode,
                        "workflow_id": str(draft.get("workflow_id") or ""),
                        "workflow_label": f"스마트 작업 {int(draft.get('workflow_index') or 1)}",
                        "_live_position": list(((draft.get("event") or {}).get("_live_position") or []))[:2],
                        "_recording_event_ids": [str(((draft.get("event") or {}).get("event_id") or ""))],
                        "_live_links": dict(((draft.get("event") or {}).get("_live_links") or {})),
                    }
                )
                continue
            event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            profile = draft.get("handle_profile") if isinstance(draft.get("handle_profile"), dict) else {}
            button = str(event.get("button") or "Left")
            count = int(draft.get("count") or 1)
            if kind == "mouse_drag":
                start_screen = event.get("from_screen") if isinstance(event.get("from_screen"), list) else [0, 0]
                end_screen = event.get("to_screen") if isinstance(event.get("to_screen"), list) else [0, 0]
                start_client = event.get("from_client") if isinstance(event.get("from_client"), list) else []
                end_client = event.get("to_client") if isinstance(event.get("to_client"), list) else []
                origin = window.get("client_origin") if isinstance(window.get("client_origin"), list) else [0, 0]
                if len(start_client) < 2:
                    start_client = [int(start_screen[0] or 0) - int(origin[0] or 0), int(start_screen[1] or 0) - int(origin[1] or 0)]
                if len(end_client) < 2:
                    end_client = [int(end_screen[0] or 0) - int(origin[0] or 0), int(end_screen[1] or 0) - int(origin[1] or 0)]
                if strategy == "screen" or not window:
                    step = {
                        "action": "mouse_click",
                        "label": f"화면 드래그 {order}",
                        "coordinate_scope": "screen",
                        "x": int(start_screen[0] or 0),
                        "y": int(start_screen[1] or 0),
                        "button": button,
                        "count": 1,
                        "action_type": "drag",
                        "drag_to": [int(end_screen[0] or 0), int(end_screen[1] or 0)],
                    }
                elif strategy == "inactive" or self.background_clicks.isChecked():
                    step = {
                        "action": "inactive_click",
                        "label": f"백그라운드 드래그 {order}",
                        "window": _window_token(window),
                        "window_exe": str(window.get("exe") or ""),
                        "x": int(start_client[0] or 0),
                        "y": int(start_client[1] or 0),
                        "button": button,
                        "clicks": 1,
                        "method": "auto",
                        "target_class": str(window.get("class") or ""),
                        "options": "NA",
                        "action_type": "drag",
                        "drag_to": [int(end_client[0] or 0), int(end_client[1] or 0)],
                        "drag_click_after": False,
                        "retry_count": 2,
                        "retry_delay": 100,
                    }
                else:
                    step = {
                        "action": "mouse_click",
                        "label": f"창 활성화 후 드래그 {order}",
                        "window": _window_token(window),
                        "window_exe": str(window.get("exe") or ""),
                        "window_hwnd": int(window.get("hwnd") or 0),
                        "coordinate_scope": "client",
                        "x": int(start_client[0] or 0),
                        "y": int(start_client[1] or 0),
                        "button": button,
                        "count": 1,
                        "action_type": "drag",
                        "drag_to": [int(end_client[0] or 0), int(end_client[1] or 0)],
                        "activate_timeout": 1200,
                    }
                step["_automation"] = {
                    "recorded_drag_screen": [
                        int(start_screen[0] or 0), int(start_screen[1] or 0),
                        int(end_screen[0] or 0), int(end_screen[1] or 0),
                    ],
                    "recorded_window": window,
                }
            elif strategy == "screen":
                step = {
                    "action": "mouse_click",
                    "label": f"녹화 클릭 {order}",
                    "x": int(event.get("x") or 0),
                    "y": int(event.get("y") or 0),
                    "button": button,
                    "count": count,
                }
            elif strategy == "image":
                source_image = _recorded_sample_image(event)
                anchor_values = event.get("image_anchor") if isinstance(event.get("image_anchor"), list) else []
                anchor = QtCore.QPoint(
                    int(anchor_values[0]) if len(anchor_values) >= 2 else source_image.width() // 2,
                    int(anchor_values[1]) if len(anchor_values) >= 2 else source_image.height() // 2,
                )
                if source_image.isNull() and not screen_pixmap.isNull():
                    point = QtCore.QPoint(int(event.get("x") or 0), int(event.get("y") or 0)) - screen_geometry.topLeft()
                    source_image = screen_pixmap.toImage()
                    anchor = point
                if source_image.isNull():
                    raise RuntimeError(f"{order}번 클릭의 이미지 표본을 캡처하지 못했습니다. 창 클릭 방식을 사용해 주세요.")
                crop_size = self.crop_sizes.get(row, QtCore.QSize(96, 64))
                crop_rect = self.crop_rects.get(row, _centered_crop_rect(source_image, anchor, crop_size))
                crop_rect = crop_rect.intersected(source_image.rect())
                detail = self.detail_images.get(row)
                image = detail.copy() if detail is not None and not detail.isNull() else source_image.copy(crop_rect)
                base_alias = f"자동녹화-{datetime.now():%Y%m%d-%H%M%S}-{order}"
                alias = base_alias
                suffix = 2
                while self.repository.asset_path(alias) is not None:
                    alias = f"{base_alias}-{suffix}"
                    suffix += 1
                self.repository.add_asset_image(image, alias)
                capture_size = window.get("capture_size") if isinstance(window.get("capture_size"), list) else window.get("client_size")
                if not isinstance(capture_size, list):
                    capture_size = [0, 0]
                capture_scope = str(window.get("capture_scope") or "client") if window else "screen"
                full_region = [0, 0, int(capture_size[0] or 0), int(capture_size[1] or 0)]
                custom_region = self.search_regions.get(row)
                regions: list[list[int]] = [list(custom_region)] if custom_region else []
                if not custom_region and window and full_region[2] > 0 and full_region[3] > 0:
                    center_x = int(event.get("client_x") or 0)
                    center_y = int(event.get("client_y") or 0)
                    local_region = [
                        max(0, center_x - 220),
                        max(0, center_y - 160),
                        min(full_region[2], center_x + 220),
                        min(full_region[3], center_y + 160),
                    ]
                    if local_region[2] > local_region[0] and local_region[3] > local_region[1] and local_region != full_region:
                        regions = [local_region]
                selected_click_offset = self.detail_click_offsets.get(row)
                if selected_click_offset is not None:
                    click_offset_values = [selected_click_offset.x(), selected_click_offset.y()]
                    click_at_center = selected_click_offset.isNull()
                else:
                    click_offset_values = [anchor.x() - crop_rect.center().x(), anchor.y() - crop_rect.center().y()]
                    click_at_center = crop_rect.center() == anchor
                click = {
                    "mode": "inactive" if self.background_clicks.isChecked() and window else "active",
                    "method": "auto",
                    "target_class": str(window.get("class") or ""),
                    # Moving the capture region must not move the intended
                    # recorded click point. Preserve it as an offset from
                    # the found template centre.
                    "click_image": click_at_center,
                    "click_offset": not click_at_center,
                    "offset": click_offset_values,
                    "count": count,
                    "window": _window_token(window) if window else "",
                    "window_exe": str(window.get("exe") or ""),
                }
                if profile:
                    click.update(
                        {
                            "mode": "inactive",
                            "method": "handle_probe",
                            "window": str(profile.get("window") or _window_token(window)),
                            "window_exe": str(profile.get("window_exe") or window.get("exe") or ""),
                            "target_control": str(profile.get("target_control") or ""),
                            "target_hwnd": str(profile.get("target_hwnd") or ""),
                            "target_child_class": str(profile.get("target_child_class") or ""),
                        }
                    )
                step = {
                    "action": "image_search",
                    "label": f"이미지 찾아 클릭 {order}",
                    "asset": alias,
                    # OpenCV steps already perform an in-process AHK exact-size
                    # probe first, so this is a fast path with a robust fallback.
                    "engine": "opencv",
                    "search_profile": "precise" if self.precise_images.isChecked() or row in self.detail_precise_rows else "fast",
                    "confidence": 84,
                    "timeout": 800,
                    "poll_delay": 40,
                    "region_mode": capture_scope if window else "screen",
                    "region_coords": "relative" if window else "screen",
                    "region_window": _window_token(window) if window else "",
                    "region_window_exe": str(window.get("exe") or ""),
                    "regions": regions if window else [],
                    "fallback_full_region": bool(window) and not bool(custom_region),
                    "click_enabled": True,
                    "click": click,
                    "abort_on_fail": False,
                }
                if kind in {"screen_condition", "screen_verification"}:
                    step["action"] = "screen_condition"
                    step["label"] = "화면 이미지 확인"
                    step["click_enabled"] = False
                    step.pop("click", None)
                    step["timeout"] = 3000
                    step["poll_delay"] = 80
                    step["abort_on_fail"] = True
                    automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
                    automation.update(
                        {
                            "smart_recording_control": str(draft.get("source_control") or "F5/RightClick"),
                            "missing_behavior": "stop",
                        }
                    )
                    step["_automation"] = automation
            elif strategy == "handle_probe" and profile:
                step = {
                    "action": "inactive_click",
                    "label": f"검증 핸들 클릭 {order}",
                    "window": str(profile.get("window") or _window_token(window)),
                    "window_exe": str(profile.get("window_exe") or window.get("exe") or ""),
                    "x": int(profile.get("x") or 0),
                    "y": int(profile.get("y") or 0),
                    "button": button,
                    "clicks": count,
                    "method": "handle_probe",
                    "target_control": str(profile.get("target_control") or ""),
                    "target_hwnd": str(profile.get("target_hwnd") or ""),
                    "target_child_class": str(profile.get("target_child_class") or ""),
                    "options": "NA",
                    "retry_count": 2,
                    "retry_delay": 100,
                    "_automation": {
                        "recorded_screen": [int(event.get("x") or 0), int(event.get("y") or 0)],
                        "recorded_window": window,
                        "handle_tested": True,
                    },
                }
            elif kind in {
                "wait", "datetime_condition", "browser_action", "ocr", "table_store",
                "table_copy", "table_paste", "table_excel_read", "table_excel_write",
                "set_var", "calc_var", "coord_mode", "call_submacro", "flow_control",
                "text_condition", "run_program", "terminate_program"
            } or strategy == "custom":
                step = {
                    "action": kind,
                    "label": str(draft.get("detail") or ACTION_TITLES.get(kind, kind)),
                }
                if kind == "wait":
                    step["duration"] = int(event.get("duration") or 1000)
                elif kind in {"text", "key", "type_text"}:
                    step["text"] = str(event.get("text") or "")
            elif strategy == "inactive":
                step = {
                    "action": "inactive_click",
                    "label": f"창 기준 클릭 {order}",
                    "window": _window_token(window),
                    "window_exe": str(window.get("exe") or ""),
                    "x": int(event.get("client_x") or 0),
                    "y": int(event.get("client_y") or 0),
                    "button": button,
                    "clicks": count,
                    "method": "auto",
                    "target_class": str(window.get("class") or ""),
                    "options": "NA",
                    "retry_count": 2,
                    "retry_delay": 100,
                    "_automation": {
                        "recorded_screen": [int(event.get("x") or 0), int(event.get("y") or 0)],
                        "recorded_window": window,
                    },
                }
            else:
                step = {
                    "action": "mouse_click",
                    "label": f"창 활성화 후 클릭 {order}",
                    "window": _window_token(window),
                    "window_exe": str(window.get("exe") or ""),
                    "window_hwnd": int(window.get("hwnd") or 0),
                    "coordinate_scope": "client",
                    "x": int(event.get("client_x") or 0),
                    "y": int(event.get("client_y") or 0),
                    "button": button,
                    "count": count,
                    "activate_timeout": 1200,
                    "_automation": {
                        "recorded_screen": [int(event.get("x") or 0), int(event.get("y") or 0)],
                        "recorded_window": window,
                    },
                }
            step["_recording_mode"] = record_mode
            workflow_id = str(draft.get("workflow_id") or "").strip()
            if workflow_id:
                step["workflow_id"] = workflow_id
                step["workflow_label"] = f"스마트 작업 {int(draft.get('workflow_index') or 1)}"
            live_position = event.get("_live_position") if isinstance(event, dict) else None
            if isinstance(live_position, (list, tuple)) and len(live_position) >= 2:
                step["_live_position"] = [float(live_position[0]), float(live_position[1])]
            event_id = str(event.get("event_id") or "")
            step["_recording_event_ids"] = [event_id] if event_id else []
            step["_live_links"] = dict(event.get("_live_links") or {}) if isinstance(event.get("_live_links"), dict) else {}
            multi_group = str(draft.get("_review_multi_group") or "")
            if multi_group:
                step["_recording_multi_group"] = multi_group
            steps.append(step)
        multi_groups: dict[str, list[int]] = {}
        for index, step in enumerate(steps):
            group = str(step.get("_recording_multi_group") or "")
            if group and step.get("action") == "image_search":
                multi_groups.setdefault(group, []).append(index)
        removed_indexes: set[int] = set()
        for group, indexes in multi_groups.items():
            if len(indexes) < 2:
                continue
            primary = steps[indexes[0]]
            members = [steps[index] for index in indexes]
            aliases = [str(member.get("asset") or "") for member in members if str(member.get("asset") or "")]
            primary["assets"] = list(dict.fromkeys(aliases))
            primary["asset"] = primary["assets"][0]
            primary["asset_offsets"] = {
                str(member.get("asset")): list((member.get("click") or {}).get("offset") or [0, 0])[:2]
                for member in members
                if str(member.get("asset") or "") and isinstance(member.get("click"), dict)
            }
            primary["engine"] = "opencv"
            primary["label"] = f"멀티 이미지 서치 {len(primary['assets'])}개"
            primary["_recording_event_ids"] = [
                event_id
                for member in members
                for event_id in member.get("_recording_event_ids") or []
                if str(event_id)
            ]
            primary["_recording_mode"] = "action"
            primary.pop("_recording_multi_group", None)
            automation = primary.get("_automation") if isinstance(primary.get("_automation"), dict) else {}
            automation.update({"recording_multi_group": group, "image_count": len(primary["assets"])})
            primary["_automation"] = automation
            removed_indexes.update(indexes[1:])
        if removed_indexes:
            steps = [step for index, step in enumerate(steps) if index not in removed_indexes]
        for step in steps:
            step.pop("_recording_multi_group", None)
        if self.connect_steps.isChecked():
            workflow_groups: list[list[int]] = []
            workflow_lookup: dict[str, int] = {}
            for index, step in enumerate(steps):
                workflow_id = str(step.get("workflow_id") or "").strip() or "workflow-01"
                if workflow_id not in workflow_lookup:
                    workflow_lookup[workflow_id] = len(workflow_groups)
                    workflow_groups.append([])
                workflow_groups[workflow_lookup[workflow_id]].append(index)

            if len(workflow_groups) > 1:
                # F7 starts independent jobs.  Their first nodes form a
                # priority condition chain; success enters only that job,
                # while failure tries the next job.  Finishing one job never
                # falls through into the next lane.
                first_indexes = [indexes[0] for indexes in workflow_groups if indexes]
                for indexes in workflow_groups:
                    for position, step_index in enumerate(indexes):
                        step = steps[step_index]
                        step.pop("on_success", None)
                        step.pop("stop_on_success", None)
                        if position + 1 < len(indexes) and step.get("action") != "flow_control":
                            step["on_success"] = indexes[position + 1] + 1
                        elif step.get("action") != "flow_control":
                            step["stop_on_success"] = True
                for position, step_index in enumerate(first_indexes):
                    step = steps[step_index]
                    step["_start_search_candidate"] = True
                    step["abort_on_fail"] = True
                    if position + 1 < len(first_indexes):
                        step["on_fail"] = first_indexes[position + 1] + 1
                        step["abort_on_fail"] = False
                    else:
                        step.pop("on_fail", None)
                    automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
                    automation.update(
                        {
                            "start_workflow_candidate": True,
                            "candidate_position": position + 1,
                            "candidate_count": len(first_indexes),
                            "all_failed_behavior": "stop",
                            "hide_candidate_fail_edge": True,
                        }
                    )
                    step["_automation"] = automation
            else:
                for index, step in enumerate(steps[:-1]):
                    if step.get("action") != "flow_control":
                        step["on_success"] = index + 2
            # A normal image capture followed by one or more branch-mode F8
            # captures becomes a priority fallback chain. Success from any
            # candidate joins the recorded common path; failure alone advances
            # to the next candidate.
            group_start = 0
            while group_start < len(steps):
                primary = steps[group_start]
                if primary.get("action") != "image_search" or primary.get("_recording_mode") == "branch":
                    group_start += 1
                    continue
                group_end = group_start
                while (
                    group_end + 1 < len(steps)
                    and steps[group_end + 1].get("action") == "image_search"
                    and steps[group_end + 1].get("_recording_mode") == "branch"
                ):
                    group_end += 1
                if group_end == group_start:
                    group_start += 1
                    continue
                common_success = group_end + 2 if group_end + 1 < len(steps) else 0
                group_id = f"recorded-image-fallback-{group_start + 1}"
                for candidate in range(group_start, group_end + 1):
                    step = steps[candidate]
                    position = candidate - group_start + 1
                    total = group_end - group_start + 1
                    step["label"] = f"순차 이미지 후보 {position}/{total}"
                    step["abort_on_fail"] = True
                    if common_success:
                        step["on_success"] = common_success
                    else:
                        step.pop("on_success", None)
                    if candidate < group_end:
                        step["on_fail"] = candidate + 2
                    else:
                        step.pop("on_fail", None)
                    automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
                    automation.update(
                        {
                            "recording_mode": str(step.get("_recording_mode") or "action"),
                            "sequential_image_group": group_id,
                            "candidate_position": position,
                            "candidate_count": total,
                        }
                    )
                    step["_automation"] = automation
                group_start = group_end + 1
        event_to_step: dict[str, int] = {}
        for index, step in enumerate(steps, start=1):
            for event_id in step.get("_recording_event_ids") or []:
                if str(event_id):
                    event_to_step[str(event_id)] = index
        for step_index, step in enumerate(steps, start=1):
            links = step.pop("_live_links", {})
            step.pop("_recording_event_ids", None)
            if not isinstance(links, dict):
                continue
            if "success" in links:
                target_id = links.get("success")
                if target_id is None:
                    step.pop("on_success", None)
                    step["stop_on_success"] = True
                elif str(target_id) in event_to_step:
                    step["on_success"] = event_to_step[str(target_id)]
                    step.pop("stop_on_success", None)
            if isinstance(links.get("success_candidates"), list):
                configure_success_candidates(
                    steps,
                    step_index,
                    [event_to_step[str(value)] for value in links["success_candidates"] if str(value) in event_to_step],
                )
            if isinstance(links.get("fail_candidates"), list):
                configure_fail_candidates(
                    steps,
                    step_index,
                    [event_to_step[str(value)] for value in links["fail_candidates"] if str(value) in event_to_step],
                )
            if "fail" in links:
                target_id = links.get("fail")
                if target_id is None:
                    step.pop("on_fail", None)
                    step["abort_on_fail"] = True
                elif str(target_id) in event_to_step:
                    step["on_fail"] = event_to_step[str(target_id)]
                    step["abort_on_fail"] = False
        return steps


@dataclass(frozen=True)
class AutomationIssue:
    severity: str
    title: str
    detail: str
    step: int = 0
    fix: str = ""


class AutomationAnalyzer:
    @staticmethod
    def analyze(macro: dict[str, Any], assets: dict[str, Any]) -> list[AutomationIssue]:
        steps = macro.get("steps") or []
        if not steps:
            return [AutomationIssue("info", "빈 매크로", "스마트 녹화 또는 자동 설정으로 첫 노드를 추가하세요.")]
        issues: list[AutomationIssue] = []
        total = len(steps)
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                issues.append(AutomationIssue("error", "손상된 노드", "노드 데이터가 객체가 아닙니다.", index))
                continue
            action = str(step.get("action") or "")
            if not action:
                issues.append(AutomationIssue("error", "액션 없음", "실행할 액션이 지정되지 않았습니다.", index))
            for field in ("on_success", "on_fail"):
                target = int(step.get(field) or 0)
                if target and not 1 <= target <= total:
                    issues.append(
                        AutomationIssue("error", "잘못된 연결", f"{field} 목적지 {target}번이 없습니다.", index, f"clear:{index}:{field}")
                    )
            candidates = step.get("success_candidates") or []
            if isinstance(candidates, list):
                for target in candidates:
                    try:
                        candidate_target = int(target)
                    except (TypeError, ValueError):
                        candidate_target = 0
                    if not 1 <= candidate_target <= total:
                        issues.append(
                            AutomationIssue(
                                "error",
                                "잘못된 성공 후보",
                                f"성공 후보 목적지 {target}번이 없습니다.",
                                index,
                            )
                        )
            if action == "image_search":
                image_aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
                alias = str(step.get("asset") or "")
                if alias and alias not in image_aliases:
                    image_aliases.insert(0, alias)
                missing_aliases = [value for value in image_aliases or [alias] if not value or value not in assets]
                if missing_aliases:
                    issues.append(AutomationIssue("error", "검색 이미지 누락", "이미지 서치에 사용할 이미지가 없습니다.", index))
                target_exe = str(step.get("region_window_exe") or "").casefold()
                if alias.startswith("자동녹화-") and target_exe in {"python.exe", "pythonw.exe"}:
                    issues.append(
                        AutomationIssue(
                            "error",
                            "Studio가 검색 대상으로 기록됨",
                            "F8을 누를 때 Studio가 활성 창이어서 잘못 저장된 이전 기록입니다. 새 버전에서 이미지를 다시 캡처하세요.",
                            index,
                        )
                    )
                if int(step.get("timeout") or 0) <= 0:
                    issues.append(
                        AutomationIssue("warning", "검색 제한 시간 없음", "미탐지 시 즉시 끝날 수 있습니다.", index, f"image_defaults:{index}")
                    )
            if action == "inactive_click" and not (step.get("window") or step.get("window_exe")):
                issues.append(AutomationIssue("warning", "대상 창 없음", "비활성 클릭의 대상 창을 지정하세요.", index))
            if action == "inactive_click" and isinstance(step.get("_automation"), dict):
                issues.append(
                    AutomationIssue(
                        "warning",
                        "이전 스마트 녹화 클릭 방식",
                        "브라우저가 무시할 수 있는 비활성 클릭입니다. 창 활성화 후 상대 좌표 클릭으로 변환할 수 있습니다.",
                        index,
                        f"recorded_foreground:{index}",
                    )
                )
            if action == "wait" and int(step.get("duration") or 0) > 60_000:
                issues.append(AutomationIssue("warning", "긴 고정 대기", "조건 대기나 이미지 서치 제한 시간으로 바꾸는 것을 권장합니다.", index))
        start = int(macro.get("graph_start_step") or 1)
        reachable: set[int] = set()
        pending = [start] if 1 <= start <= total else [1]
        while pending:
            current = pending.pop()
            if current in reachable or not 1 <= current <= total:
                continue
            reachable.add(current)
            step = steps[current - 1] if isinstance(steps[current - 1], dict) else {}
            success = int(step.get("on_success") or (current + 1 if current < total else 0))
            failure = int(step.get("on_fail") or 0)
            if success:
                pending.append(success)
            if failure:
                pending.append(failure)
        for node in sorted(set(range(1, total + 1)) - reachable):
            issues.append(AutomationIssue("warning", "도달할 수 없는 노드", "현재 시작·연결 구조에서는 실행되지 않습니다.", node))
        if total > 1 and any(not int(step.get("on_success") or 0) for step in steps[:-1] if isinstance(step, dict)):
            issues.append(
                AutomationIssue("info", "순차 연결 보완 가능", "연결되지 않은 성공 포트를 다음 노드로 자동 연결할 수 있습니다.", fix="connect_sequential")
            )
        return issues

    @staticmethod
    def analyze_runtime_log(macro: dict[str, Any], log_text: str) -> list[AutomationIssue]:
        recent = "\n".join(log_text.splitlines()[-240:])
        issues: list[AutomationIssue] = []
        for index, step in enumerate(macro.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "")
            if action == "image_search":
                alias = str(step.get("asset") or "")
                if alias and (f"이미지 미탐지" in recent and alias in recent or f"image not found - {alias}" in recent):
                    issues.append(
                        AutomationIssue(
                            "warning",
                            "최근 실행에서 이미지 미탐지",
                            f"'{alias}' 검색을 정밀 모드·3초 재시도로 보완할 수 있습니다.",
                            index,
                            f"relax_image:{index}",
                        )
                    )
            if action == "inactive_click" and "inactive click failed" in recent:
                target = str(step.get("window_exe") or step.get("window") or "")
                if target and target in recent:
                    issues.append(
                        AutomationIssue("warning", "최근 실행에서 대상 창 클릭 실패", "대상 프로그램 실행 여부와 창 식별자를 다시 확인하세요.", index)
                    )
        if "opencv search error" in recent or "opencv image search failed" in recent:
            issues.append(AutomationIssue("error", "최근 OpenCV 실행 오류", "설정 > 구성요소에서 OpenCV 상태를 점검하세요."))
        return issues

    @staticmethod
    def apply_fixes(macro: dict[str, Any], fixes: list[str]) -> int:
        steps = macro.get("steps") or []
        changed = 0
        for fix in dict.fromkeys(fixes):
            if fix == "connect_sequential":
                for index, step in enumerate(steps[:-1]):
                    if isinstance(step, dict) and step.get("action") != "flow_control" and not int(step.get("on_success") or 0):
                        step["on_success"] = index + 2
                        changed += 1
            elif fix.startswith("clear:"):
                _, index_text, field = fix.split(":", 2)
                index = int(index_text) - 1
                if 0 <= index < len(steps) and isinstance(steps[index], dict) and field in steps[index]:
                    steps[index].pop(field, None)
                    changed += 1
            elif fix.startswith("image_defaults:"):
                index = int(fix.split(":", 1)[1]) - 1
                if 0 <= index < len(steps) and isinstance(steps[index], dict):
                    steps[index].setdefault("timeout", 3000)
                    steps[index].setdefault("poll_delay", 60)
                    steps[index].setdefault("confidence", 86)
                    changed += 1
            elif fix.startswith("relax_image:"):
                index = int(fix.split(":", 1)[1]) - 1
                if 0 <= index < len(steps) and isinstance(steps[index], dict):
                    step = steps[index]
                    step["search_profile"] = "precise"
                    step["timeout"] = max(3000, int(step.get("timeout") or 0))
                    step["poll_delay"] = min(80, max(40, int(step.get("poll_delay") or 60)))
                    step["confidence"] = max(72, int(step.get("confidence") or 86) - 3)
                    changed += 1
            elif fix.startswith("recorded_foreground:"):
                index = int(fix.split(":", 1)[1]) - 1
                if 0 <= index < len(steps) and isinstance(steps[index], dict):
                    old_step = steps[index]
                    automation = old_step.get("_automation") if isinstance(old_step.get("_automation"), dict) else {}
                    window = automation.get("recorded_window") if isinstance(automation.get("recorded_window"), dict) else {}
                    replacement = {
                        "action": "mouse_click",
                        "label": str(old_step.get("label") or f"창 활성화 후 클릭 {index + 1}").replace("창 기준", "창 활성화 후"),
                        "window": str(old_step.get("window") or _window_token(window)),
                        "window_exe": str(old_step.get("window_exe") or window.get("exe") or ""),
                        "window_hwnd": int(window.get("hwnd") or 0),
                        "coordinate_scope": "client",
                        "x": int(old_step.get("x") or 0),
                        "y": int(old_step.get("y") or 0),
                        "button": str(old_step.get("button") or "Left"),
                        "count": int(old_step.get("clicks") or 1),
                        "activate_timeout": 1200,
                        "_automation": automation,
                    }
                    for field in ("on_success", "on_fail", "edge_conditions", "sleep_after"):
                        if field in old_step:
                            replacement[field] = old_step[field]
                    steps[index] = replacement
                    changed += 1
        return changed


class DiagnosticsDialog(QtWidgets.QDialog):
    def __init__(self, issues: list[AutomationIssue], parent=None) -> None:
        super().__init__(parent)
        self.issues = issues
        self.setWindowTitle("자동화 진단")
        self.resize(820, 540)
        layout = QtWidgets.QVBoxLayout(self)
        errors = sum(issue.severity == "error" for issue in issues)
        warnings = sum(issue.severity == "warning" for issue in issues)
        title = QtWidgets.QLabel(f"오류 {errors}개 · 주의 {warnings}개 · 제안 {len(issues) - errors - warnings}개")
        title.setStyleSheet("font-size:15pt; font-weight:800;")
        layout.addWidget(title)
        self.table = QtWidgets.QTableWidget(len(issues), 5)
        self.table.setHorizontalHeaderLabels(["자동 수정", "상태", "노드", "항목", "설명"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        colors = {"error": "#FF5C73", "warning": "#F2B84B", "info": "#55C2FF"}
        labels = {"error": "오류", "warning": "주의", "info": "제안"}
        for row, issue in enumerate(issues):
            fix_item = QtWidgets.QTableWidgetItem()
            fix_item.setCheckState(QtCore.Qt.Checked if issue.fix else QtCore.Qt.Unchecked)
            if not issue.fix:
                fix_item.setFlags(fix_item.flags() & ~QtCore.Qt.ItemIsEnabled)
            self.table.setItem(row, 0, fix_item)
            status_item = QtWidgets.QTableWidgetItem(labels.get(issue.severity, issue.severity))
            status_item.setForeground(QtGui.QColor(colors.get(issue.severity, COLORS["muted"])))
            self.table.setItem(row, 1, status_item)
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(issue.step or "-")))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(issue.title))
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(issue.detail))
        layout.addWidget(self.table, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Close)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).setText("선택 항목 자동 수정")
        buttons.button(QtWidgets.QDialogButtonBox.Apply).setEnabled(any(issue.fix for issue in issues))
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_fixes(self) -> list[str]:
        return [
            issue.fix
            for row, issue in enumerate(self.issues)
            if issue.fix and self.table.item(row, 0).checkState() == QtCore.Qt.Checked
        ]


class AutomationOverlay(QtWidgets.QWidget):
    def __init__(self, points: list[QtCore.QPoint], regions: list[QtCore.QRect], parent=None) -> None:
        super().__init__(None)
        self.points = points
        self.regions = regions
        geometry = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        QtCore.QTimer.singleShot(1300, self.close)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        origin = self.geometry().topLeft()
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor("#29D8FF"), 4))
        for region in self.regions:
            painter.drawRoundedRect(region.translated(-origin), 8, 8)
        painter.setPen(QtGui.QPen(QtGui.QColor("#FFCC4D"), 4))
        for point in self.points:
            local = point - origin
            painter.drawEllipse(local, 15, 15)
            painter.drawLine(local.x() - 24, local.y(), local.x() + 24, local.y())
            painter.drawLine(local.x(), local.y() - 24, local.x(), local.y() + 24)

    @classmethod
    def show_step(cls, step: dict[str, Any], parent=None) -> "AutomationOverlay | None":
        points: list[QtCore.QPoint] = []
        regions: list[QtCore.QRect] = []
        action = str(step.get("action") or "")
        if action == "mouse_click":
            points.append(QtCore.QPoint(int(step.get("x") or 0), int(step.get("y") or 0)))
        elif action == "inactive_click":
            metadata = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            recorded = metadata.get("recorded_screen") if isinstance(metadata.get("recorded_screen"), list) else []
            if len(recorded) >= 2:
                points.append(QtCore.QPoint(int(recorded[0]), int(recorded[1])))
        elif action == "image_search":
            values = step.get("region") if isinstance(step.get("region"), list) else []
            if len(values) >= 4 and str(step.get("region_coords") or "screen") == "screen":
                left, top, right, bottom = (int(value or 0) for value in values[:4])
                if right > left and bottom > top:
                    regions.append(QtCore.QRect(left, top, right - left, bottom - top))
        if not points and not regions:
            return None
        overlay = cls(points, regions, parent)
        overlay.show()
        overlay.raise_()
        return overlay


class QuickActionWizard:
    @staticmethod
    def build(action: str, repository, parent=None) -> dict[str, Any] | None:
        step = action_template(action)
        if action == "mouse_click":
            picker = CoordinatePickerDialog(parent)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            step.update({
                "x": picker.point.x(),
                "y": picker.point.y(),
                "button": "left",
                "count": 1,
                "label": f"마우스 클릭 ({picker.point.x()}, {picker.point.y()})",
            })
            return step
        if action == "inactive_click":
            ignored = {int(parent.winId())} if parent else set()
            window_picker = WindowPickerDialog(
                parent,
                ignored_hwnds=ignored,
                hint_text="🎯 비활성 클릭할 버튼이나 아이콘을 마우스로 클릭하세요  ·  Esc 취소",
            )
            if window_picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            client_pt = window_picker.selected_client_point()
            screen_pt = window_picker.selected_screen_point()
            cx = client_pt.x() if client_pt else screen_pt.x()
            cy = client_pt.y() if client_pt else screen_pt.y()
            step.update(
                {
                    "window": window_picker.window_token,
                    "window_exe": window_picker.exe_name,
                    "x": cx,
                    "y": cy,
                    "button": "left",
                    "count": 1,
                    "method": "auto",
                    "retry_count": 2,
                    "label": f"{window_picker.exe_name or '비활성'} 클릭 ({cx}, {cy})",
                    "_automation": {"recorded_screen": [screen_pt.x(), screen_pt.y()]},
                }
            )
            return step
        if action in {"image_search", "screen_condition"}:
            QtWidgets.QApplication.processEvents()
            QtCore.QThread.msleep(150)
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                return None
            is_cond = (action == "screen_condition")
            hint = (
                "⬚ 확인할 조건 이미지 영역을 드래그하세요 (마우스 놓기 / Enter 확정, Esc 취소)"
                if is_cond
                else "⬚ 검색할 이미지를 마우스로 드래그하세요 (마우스 놓기 / Enter 확정, Esc 취소)"
            )
            dialog = ScreenCaptureDialog(
                pixmap,
                geometry,
                parent,
                accept_on_release=True,
                hint_text=hint,
            )
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                return None
            image = dialog.captured_image()
            rect = dialog.selected_screen_rect()
            if image.isNull() or not rect.isValid():
                return None
            prefix = "cond" if is_cond else "img"
            alias = f"{prefix}_{datetime.now():%m%d_%H%M%S}"
            alias = repository.add_asset_image(image, alias)
            search_region = [
                max(0, rect.left() - 100),
                max(0, rect.top() - 100),
                rect.right() + 100,
                rect.bottom() + 100,
            ]
            step.update(
                {
                    "asset": alias,
                    "assets": [alias],
                    "engine": "opencv",
                    "search_profile": "precise",
                    "confidence": 85,
                    "wait_condition": "appear",
                    "timeout": 3000,
                    "region_mode": "screen",
                    "region_coords": "screen",
                    "region": search_region,
                    "click_enabled": not is_cond,
                    "click": {
                        "mode": "active",
                        "click_image": not is_cond,
                        "click_offset": False,
                        "count": 1,
                        "button": "left",
                    },
                    "abort_on_fail": False,
                    "label": f"화면 조건 ({alias})" if is_cond else f"이미지 서치 ({alias})",
                }
            )
            return step
        if action == "ocr":
            QtWidgets.QApplication.processEvents()
            QtCore.QThread.msleep(150)
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                return None
            dialog = ScreenCaptureDialog(
                pixmap,
                geometry,
                parent,
                accept_on_release=True,
                hint_text="⬚ OCR 텍스트를 인식할 영역을 드래그하세요 (마우스 놓기 / Enter 확정, Esc 취소)",
            )
            if dialog.exec() != QtWidgets.QDialog.Accepted:
                return None
            image = dialog.captured_image()
            rect = dialog.selected_screen_rect()
            if image.isNull() or not rect.isValid():
                return None
            alias = f"ocr_sample_{datetime.now():%m%d_%H%M%S}"
            repository.add_asset_image(image, alias)
            search_region = [rect.left(), rect.top(), rect.right(), rect.bottom()]
            step.update(
                {
                    "asset": alias,
                    "search_region": search_region,
                    "region": search_region,
                    "lang": "eng+kor",
                    "engine_preference": "auto",
                    "ocr_action": "extract",
                    "label": f"OCR 텍스트 인식 ({rect.width()}×{rect.height()})",
                }
            )
            return step
        if action == "type_text":
            text, ok = QtWidgets.QInputDialog.getMultiLineText(parent, "텍스트 입력 자동 설정", "입력할 내용")
            if not ok:
                return None
            step.update({"text": text, "send_mode": "raw", "label": "자동 설정 텍스트 입력"})
            return step
        if action == "wait":
            duration, ok = QtWidgets.QInputDialog.getInt(parent, "대기 자동 설정", "대기 시간(ms)", 500, 0, 3_600_000, 100)
            if not ok:
                return None
            step.update({"duration": duration, "label": "화면 반응 대기"})
            return step
        if action == "run_program":
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(parent, "실행할 프로그램 선택", "", "실행 파일 (*.exe *.bat *.cmd);;모든 파일 (*)")
            if not filename:
                return None
            step.update({"command": filename, "label": f"{Path(filename).stem} 실행"})
            return step
        if action == "pixel_search":
            QtWidgets.QApplication.processEvents()
            QtCore.QThread.msleep(150)
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                return None
            picker = PixelColorPickerDialog(pixmap, geometry, parent)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            color = picker.selected_color()
            if not color:
                return None
            step.update({
                "color": color.name().upper(),
                "tolerance": 15,
                "action_on_found": "click",
                "label": f"색상 서치 ({color.name().upper()})",
            })
            return step
        if action == "ocr_tracking":
            pixmap, geometry = capture_virtual_desktop()
            p1 = ScreenCaptureDialog(pixmap, geometry, parent, hint_text="[ 1단계 ] 추적할 대상 이미지를 드래그 선택 후 Enter")
            if p1.exec() != QtWidgets.QDialog.Accepted:
                return None
            img = p1.captured_image()
            if img.isNull():
                return None
            alias = f"track-{datetime.now():%Y%m%d-%H%M%S}"
            repository.add_asset_image(img, alias)
            ref_rect = p1.selected_screen_rect()
            p2 = ScreenCaptureDialog(pixmap, geometry, parent, hint_text="[ 2단계 ] 인식할 OCR 텍스트 영역을 드래그 선택 후 Enter")
            if p2.exec() != QtWidgets.QDialog.Accepted:
                return None
            ocr_rect = p2.selected_screen_rect()
            step.update(
                {
                    "tracking_asset": alias,
                    "ocr_offset_x": ocr_rect.left() - ref_rect.left(),
                    "ocr_offset_y": ocr_rect.top() - ref_rect.top(),
                    "ocr_width": ocr_rect.width(),
                    "ocr_height": ocr_rect.height(),
                    "label": "자동 설정 OCR 추적",
                }
            )
            return step
        if action == "multi_pixel_check":
            pixmap, geometry = capture_virtual_desktop()
            picker = MultiPixelPickerDialog(pixmap, geometry, parent)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            pts = picker.selected_points()
            if not pts:
                return None
            import json
            step.update({"pixels": json.dumps(pts, ensure_ascii=False), "label": f"다중 픽셀 체크 ({len(pts)}개 점)"})
            return step
        if action == "wait_color":
            pixmap, geometry = capture_virtual_desktop()
            picker = PixelColorPickerDialog(pixmap, geometry, parent)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            color = picker.selected_color()
            pt = picker.selected_point()
            if not color or not pt:
                return None
            step.update({"x": pt.x(), "y": pt.y(), "target_color": color.name().upper(), "label": "자동 설정 색상 대기"})
            return step
        if action == "color_ratio":
            pixmap, geometry = capture_virtual_desktop()
            picker = PixelColorPickerDialog(pixmap, geometry, parent)
            if picker.exec() != QtWidgets.QDialog.Accepted:
                return None
            color = picker.selected_color()
            if not color:
                return None
            p2 = ScreenCaptureDialog(pixmap, geometry, parent, hint_text="게이지(체력바) 영역을 드래그 선택 후 Enter")
            if p2.exec() != QtWidgets.QDialog.Accepted or not p2.selected_screen_rect().isValid():
                return None
            r = p2.selected_screen_rect()
            step.update(
                {
                    "target_color": color.name().upper(),
                    "search_region": [r.left(), r.top(), r.right(), r.bottom()],
                    "label": "자동 설정 색상 비율 게이지",
                }
            )
            return step
        return step
