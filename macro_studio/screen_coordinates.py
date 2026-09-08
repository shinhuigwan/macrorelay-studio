from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
from typing import Iterable

from PySide6 import QtCore, QtGui


@dataclass(frozen=True)
class DisplayCoordinateMap:
    """Mapping between Qt logical pixels and Win32/OpenCV physical pixels."""

    name: str
    logical: QtCore.QRect
    native: QtCore.QRect


def _native_monitor_rects() -> dict[str, QtCore.QRect]:
    if sys.platform != "win32":
        return {}

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    result: dict[str, QtCore.QRect] = {}
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    @callback_type
    def callback(monitor, _dc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcMonitor
            result[str(info.szDevice).casefold()] = QtCore.QRect(
                int(rect.left),
                int(rect.top),
                int(rect.right - rect.left),
                int(rect.bottom - rect.top),
            )
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, None, callback, 0)
    except (AttributeError, OSError):
        return {}
    return result


def display_coordinate_maps() -> list[DisplayCoordinateMap]:
    native_by_name = _native_monitor_rects()
    maps: list[DisplayCoordinateMap] = []
    screens = list(QtGui.QGuiApplication.screens())
    unused_native = list(native_by_name.values())
    for screen in screens:
        logical = QtCore.QRect(screen.geometry())
        native = native_by_name.get(str(screen.name()).casefold())
        if native is not None:
            unused_native = [candidate for candidate in unused_native if candidate != native]
        elif screen.name() and len(screens) == len(native_by_name) and unused_native:
            # Device names are normally identical (\\.\DISPLAY1).  This
            # positional fallback only applies to unusual Qt display plugins.
            native = min(
                unused_native,
                key=lambda candidate: abs(candidate.center().x() - logical.center().x())
                + abs(candidate.center().y() - logical.center().y()),
            )
            unused_native.remove(native)
        else:
            ratio = max(0.1, float(screen.devicePixelRatio() or 1.0))
            native = QtCore.QRect(
                round(logical.left() * ratio),
                round(logical.top() * ratio),
                max(1, round(logical.width() * ratio)),
                max(1, round(logical.height() * ratio)),
            )
        maps.append(DisplayCoordinateMap(str(screen.name()), logical, native))
    return maps


def _distance_to_rect(point: QtCore.QPoint, rect: QtCore.QRect) -> int:
    dx = max(rect.left() - point.x(), 0, point.x() - (rect.left() + rect.width()))
    dy = max(rect.top() - point.y(), 0, point.y() - (rect.top() + rect.height()))
    return dx * dx + dy * dy


def _pick_map(
    point: QtCore.QPoint,
    maps: Iterable[DisplayCoordinateMap],
    *,
    native: bool,
) -> DisplayCoordinateMap | None:
    candidates = list(maps)
    if not candidates:
        return None
    for item in candidates:
        rect = item.native if native else item.logical
        if rect.contains(point):
            return item
    return min(candidates, key=lambda item: _distance_to_rect(point, item.native if native else item.logical))


def _map_point(point: QtCore.QPoint, source: QtCore.QRect, target: QtCore.QRect) -> QtCore.QPoint:
    if source.width() <= 0 or source.height() <= 0:
        return QtCore.QPoint(point)
    x = target.left() + round((point.x() - source.left()) * target.width() / source.width())
    y = target.top() + round((point.y() - source.top()) * target.height() / source.height())
    return QtCore.QPoint(x, y)


def logical_point_to_native(
    point: QtCore.QPoint,
    maps: Iterable[DisplayCoordinateMap] | None = None,
) -> QtCore.QPoint:
    available = list(maps) if maps is not None else display_coordinate_maps()
    item = _pick_map(point, available, native=False)
    return _map_point(point, item.logical, item.native) if item is not None else QtCore.QPoint(point)


def native_point_to_logical(
    point: QtCore.QPoint,
    maps: Iterable[DisplayCoordinateMap] | None = None,
) -> QtCore.QPoint:
    available = list(maps) if maps is not None else display_coordinate_maps()
    item = _pick_map(point, available, native=True)
    return _map_point(point, item.native, item.logical) if item is not None else QtCore.QPoint(point)


def logical_rect_to_native(
    rect: QtCore.QRect,
    maps: Iterable[DisplayCoordinateMap] | None = None,
) -> QtCore.QRect:
    """Convert a Qt rectangle while preserving exclusive right/bottom edges."""
    if not rect.isValid():
        return QtCore.QRect()
    available = list(maps) if maps is not None else display_coordinate_maps()
    item = _pick_map(rect.center(), available, native=False)
    if item is None:
        return QtCore.QRect(rect)
    top_left = _map_point(rect.topLeft(), item.logical, item.native)
    exclusive_end = _map_point(
        QtCore.QPoint(rect.x() + rect.width(), rect.y() + rect.height()), item.logical, item.native
    )
    return QtCore.QRect(
        top_left.x(),
        top_left.y(),
        max(1, exclusive_end.x() - top_left.x()),
        max(1, exclusive_end.y() - top_left.y()),
    )


def native_rect_to_logical(
    rect: QtCore.QRect,
    maps: Iterable[DisplayCoordinateMap] | None = None,
) -> QtCore.QRect:
    if not rect.isValid():
        return QtCore.QRect()
    available = list(maps) if maps is not None else display_coordinate_maps()
    item = _pick_map(rect.center(), available, native=True)
    if item is None:
        return QtCore.QRect(rect)
    top_left = _map_point(rect.topLeft(), item.native, item.logical)
    exclusive_end = _map_point(
        QtCore.QPoint(rect.x() + rect.width(), rect.y() + rect.height()), item.native, item.logical
    )
    return QtCore.QRect(
        top_left.x(),
        top_left.y(),
        max(1, exclusive_end.x() - top_left.x()),
        max(1, exclusive_end.y() - top_left.y()),
    )


def rect_to_exclusive_list(rect: QtCore.QRect) -> list[int]:
    return [rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()]
