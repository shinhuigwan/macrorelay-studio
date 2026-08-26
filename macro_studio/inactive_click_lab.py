from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets


GA_ROOT = 2
GW_HWNDNEXT = 2
CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPDISABLED = 0x0002
CWP_SKIPTRANSPARENT = 0x0004
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


@dataclass(frozen=True)
class HandleCandidate:
    hwnd: int
    class_name: str
    class_nn: str
    text: str
    rect: QtCore.QRect
    client_point: QtCore.QPoint
    depth: int
    source: str


@dataclass(frozen=True)
class HandleProbeResult:
    point: QtCore.QPoint
    root_hwnd: int
    window_token: str
    exe_name: str
    title: str
    class_name: str
    root_client_point: QtCore.QPoint
    candidates: tuple[HandleCandidate, ...]


def _class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _window_text(hwnd: int) -> str:
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(2048)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _window_rect(hwnd: int) -> QtCore.QRect:
    rect = wintypes.RECT()
    if hwnd and ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return QtCore.QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return QtCore.QRect()


def _screen_to_client(hwnd: int, point: QtCore.QPoint) -> QtCore.QPoint:
    native = wintypes.POINT(point.x(), point.y())
    if hwnd:
        ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(native))
    return QtCore.QPoint(int(native.x), int(native.y))


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name
    finally:
        ctypes.windll.kernel32.CloseHandle(process)
    return ""


def _enum_children(root: int) -> list[int]:
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        handles.append(int(hwnd))
        return True

    ctypes.windll.user32.EnumChildWindows(root, callback, 0)
    return handles


def _class_nn(root: int, target: int) -> str:
    if not target or target == root:
        return ""
    counts: dict[str, int] = {}
    for hwnd in _enum_children(root):
        name = _class_name(hwnd)
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        if hwnd == target:
            return f"{name}{counts[name]}"
    return ""


def _depth_from_root(root: int, hwnd: int) -> int:
    depth = 0
    current = hwnd
    while current and current != root and depth < 64:
        current = int(ctypes.windll.user32.GetParent(current) or 0)
        depth += 1
    return depth if current == root else 0


def _deepest_child(root: int, screen_point: QtCore.QPoint) -> int:
    user32 = ctypes.windll.user32
    child_from_point = user32.ChildWindowFromPointEx
    child_from_point.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
    child_from_point.restype = wintypes.HWND
    current = root
    visited = {root}
    while current:
        local = wintypes.POINT(screen_point.x(), screen_point.y())
        user32.ScreenToClient(current, ctypes.byref(local))
        child = int(
            child_from_point(
                current,
                local,
                CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT,
            )
            or 0
        )
        if not child or child == current or child in visited:
            break
        child_rect = _window_rect(child)
        if not child_rect.isValid() or not child_rect.contains(screen_point):
            break
        visited.add(child)
        current = child
    return current


def _root_under_overlay(overlay_hwnd: int, point: QtCore.QPoint, ignored_hwnds: set[int]) -> int:
    user32 = ctypes.windll.user32
    own_root = int(user32.GetAncestor(overlay_hwnd, GA_ROOT) or overlay_hwnd)
    candidate = int(user32.GetWindow(own_root, GW_HWNDNEXT) or 0)
    while candidate:
        root = int(user32.GetAncestor(candidate, GA_ROOT) or candidate)
        rect = _window_rect(root)
        if (
            root != own_root
            and root not in ignored_hwnds
            and user32.IsWindowVisible(root)
            and rect.isValid()
            and rect.contains(point)
        ):
            return root
        candidate = int(user32.GetWindow(candidate, GW_HWNDNEXT) or 0)
    return 0


def probe_window_handles(root: int, point: QtCore.QPoint) -> HandleProbeResult:
    user32 = ctypes.windll.user32
    deepest = _deepest_child(root, point)
    ordered: list[tuple[int, str]] = []
    seen: set[int] = set()

    current = deepest
    while current and current not in seen:
        ordered.append((current, "커서 경로"))
        seen.add(current)
        if current == root:
            break
        current = int(user32.GetParent(current) or 0)

    containing: list[int] = []
    for hwnd in _enum_children(root):
        rect = _window_rect(hwnd)
        if user32.IsWindowVisible(hwnd) and rect.isValid() and rect.contains(point):
            containing.append(hwnd)
    containing.sort(key=lambda hwnd: max(1, _window_rect(hwnd).width() * _window_rect(hwnd).height()))
    for hwnd in containing:
        if hwnd not in seen:
            ordered.append((hwnd, "겹친 자식 창"))
            seen.add(hwnd)
    if root not in seen:
        ordered.append((root, "최상위 창"))

    candidates: list[HandleCandidate] = []
    for hwnd, source in ordered:
        candidates.append(
            HandleCandidate(
                hwnd=hwnd,
                class_name=_class_name(hwnd),
                class_nn=_class_nn(root, hwnd),
                text=_window_text(hwnd),
                rect=_window_rect(hwnd),
                client_point=_screen_to_client(hwnd, point),
                depth=_depth_from_root(root, hwnd),
                source=source,
            )
        )

    title = _window_text(root)
    class_name = _class_name(root)
    exe_name = _process_name(root)
    if title and exe_name:
        token = f"{title} ahk_exe {exe_name}"
    elif class_name and exe_name:
        token = f"ahk_class {class_name} ahk_exe {exe_name}"
    elif exe_name:
        token = f"ahk_exe {exe_name}"
    else:
        token = f"ahk_id 0x{root:X}"
    return HandleProbeResult(
        point=QtCore.QPoint(point),
        root_hwnd=root,
        window_token=token,
        exe_name=exe_name,
        title=title,
        class_name=class_name,
        root_client_point=_screen_to_client(root, point),
        candidates=tuple(candidates),
    )


def post_inactive_test_click(hwnd: int, screen_point: QtCore.QPoint) -> tuple[bool, QtCore.QPoint]:
    if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
        return False, QtCore.QPoint()
    client = _screen_to_client(hwnd, screen_point)
    lparam = ((client.y() & 0xFFFF) << 16) | (client.x() & 0xFFFF)
    moved = bool(ctypes.windll.user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam))
    down = bool(ctypes.windll.user32.PostMessageW(hwnd, WM_LBUTTONDOWN, 1, lparam))
    time.sleep(0.015)
    up = bool(ctypes.windll.user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam))
    return moved and down and up, client


class HandlePointPicker(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        ignored_hwnds: set[int] | None = None,
        *,
        required_root: int = 0,
    ) -> None:
        super().__init__(parent)
        self.probe_result: HandleProbeResult | None = None
        self.selected_point: QtCore.QPoint | None = None
        self._ignored_hwnds = set(ignored_hwnds or ())
        self._required_root = int(required_root or 0)
        self._root_hwnd = 0
        self._root_rect = QtCore.QRect()
        self._child_rect = QtCore.QRect()
        self._cursor_point = QtCore.QPoint()
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        geometry = QtCore.QRect()
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QtWidgets.QLabel(
            "시험 클릭 좌표를 지정하세요  ·  파란색=지정한 대상 창  ·  Esc 취소"
            if self._required_root
            else "핸들을 수집할 대상 위치를 클릭하세요  ·  파란색=대상 창  청록색=자식 핸들  ·  Esc 취소"
        )
        self.hint.setAlignment(QtCore.Qt.AlignCenter)
        self.hint.setStyleSheet(
            "background:rgba(12,14,20,235); color:white; padding:14px; font-size:13pt; font-weight:700;"
        )
        layout.addWidget(self.hint, 0, QtCore.Qt.AlignTop)
        layout.addStretch(1)

    def _update_target(self, point: QtCore.QPoint) -> None:
        root = _root_under_overlay(int(self.winId()), point, self._ignored_hwnds)
        self._cursor_point = QtCore.QPoint(point)
        self._root_hwnd = root
        display_root = self._required_root or root
        self._root_rect = _window_rect(display_root)
        child = _deepest_child(root, point) if root and (not self._required_root or root == self._required_root) else 0
        self._child_rect = _window_rect(child) if child and child != root else QtCore.QRect()
        if self._required_root and root == self._required_root:
            client = _screen_to_client(self._required_root, point)
            self.hint.setText(
                f"이 위치를 클릭해 시험 좌표 저장 · 화면 {point.x()}, {point.y()} · 대상 창 {client.x()}, {client.y()} · Esc 취소"
            )
        elif self._required_root:
            self.hint.setText("파란 테두리 안에서 시험 클릭 좌표를 지정하세요 · Esc 취소")
        elif root:
            self.hint.setText(
                f"클릭해서 핸들 후보 수집 · HWND 0x{child:X} · {_class_name(child)} · Esc 취소"
            )
        else:
            self.hint.setText("대상 창을 찾지 못했습니다 · 다른 위치로 이동하세요 · Esc 취소")
        self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._update_target(event.globalPosition().toPoint())
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            point = event.globalPosition().toPoint()
            self._update_target(point)
            if self._required_root:
                if self._root_hwnd == self._required_root:
                    self.selected_point = QtCore.QPoint(point)
                    self.accept()
                else:
                    self.hint.setText("지정된 대상 창의 파란 테두리 안을 클릭하세요 · Esc 취소")
                event.accept()
                return
            if self._root_hwnd:
                try:
                    self.probe_result = probe_window_handles(self._root_hwnd, point)
                except Exception:
                    self.probe_result = None
                if self.probe_result and self.probe_result.candidates:
                    self.accept()
                    return
            self.hint.setText("핸들 후보를 수집하지 못했습니다 · 다시 클릭하거나 Esc로 취소하세요")
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(20, 25, 36, 38))
        origin = self.geometry().topLeft()
        if self._root_rect.isValid():
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor("#2997FF"), 5))
            painter.drawRect(self._root_rect.translated(-origin).adjusted(2, 2, -2, -2))
        if self._child_rect.isValid():
            painter.setPen(QtGui.QPen(QtGui.QColor("#41D9D2"), 4))
            painter.drawRect(self._child_rect.translated(-origin).adjusted(3, 3, -3, -3))
        if self._required_root and self._root_hwnd == self._required_root:
            local_point = self._cursor_point - origin
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFCA5C"), 3))
            painter.drawEllipse(local_point, 10, 10)
            painter.drawLine(local_point + QtCore.QPoint(-16, 0), local_point + QtCore.QPoint(16, 0))
            painter.drawLine(local_point + QtCore.QPoint(0, -16), local_point + QtCore.QPoint(0, 16))
        painter.end()
        super().paintEvent(event)


class InactiveClickLabDialog(QtWidgets.QDialog):
    def __init__(self, result: HandleProbeResult, parent=None) -> None:
        super().__init__(parent)
        self.probe = result
        self.selected_candidate: HandleCandidate | None = None
        self.test_point: QtCore.QPoint | None = None
        self.setWindowTitle("비활성 클릭 핸들 실험실")
        self.resize(1120, 690)
        self.setMinimumSize(940, 560)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("후보 핸들마다 시험 클릭을 보내 실제로 반응하는 대상을 찾습니다.")
        title.setStyleSheet("font-size:15pt; font-weight:800;")
        description = QtWidgets.QLabel(
            "Window Spy와 같은 방식으로 최상위 HWND·자식 HWND·ClassNN을 수집했습니다. "
            "시험 클릭은 창을 활성화하지 않으며, 화면의 실제 반응을 직접 확인해야 합니다."
        )
        description.setWordWrap(True)
        description.setObjectName("Muted")
        target = QtWidgets.QLabel(
            f"대상: {result.title or '(제목 없음)'}  ·  {result.exe_name or '프로세스 확인 불가'}  ·  "
            f"ROOT 0x{result.root_hwnd:X}  ·  화면 {result.point.x()}, {result.point.y()}"
        )
        target.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(target)

        self.table = QtWidgets.QTableWidget(len(result.candidates), 7)
        self.table.setHorizontalHeaderLabels(["순서", "HWND", "ClassNN", "클래스", "깊이", "클라이언트 좌표", "범위"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        for column in (0, 1, 4, 5, 6):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Interactive)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.table.setColumnWidth(2, 210)
        for row, candidate in enumerate(result.candidates):
            values = [
                f"{row + 1} · {candidate.source}",
                f"0x{candidate.hwnd:X}",
                candidate.class_nn or "최상위 창",
                candidate.class_name or "—",
                str(candidate.depth),
                f"{candidate.client_point.x()}, {candidate.client_point.y()}",
                f"{candidate.rect.width()}×{candidate.rect.height()}",
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if candidate.text:
                    item.setToolTip(candidate.text)
                self.table.setItem(row, column, item)
        self.table.selectRow(0)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._test_row(row))
        layout.addWidget(self.table, 1)

        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.coordinate_label = QtWidgets.QLabel("시험 좌표: 지정되지 않음")
        self.coordinate_label.setStyleSheet("font-weight:800; color:#FFCA5C;")
        self.status = QtWidgets.QLabel("먼저 ‘시험 클릭 좌표 지정’을 눌러 대상 창 안의 정확한 위치를 선택하세요.")
        self.status.setObjectName("Muted")
        layout.addWidget(self.detail)
        layout.addWidget(self.coordinate_label)
        layout.addWidget(self.status)

        controls = QtWidgets.QHBoxLayout()
        pick_coordinate = QtWidgets.QPushButton("⌖ 시험 클릭 좌표 지정")
        pick_coordinate.clicked.connect(self._pick_test_point)
        self.test_button = QtWidgets.QPushButton("▶ 선택 핸들 시험 클릭")
        self.test_button.setEnabled(False)
        self.test_button.clicked.connect(self._test_selected)
        self.next_test_button = QtWidgets.QPushButton("다음 후보 시험")
        self.next_test_button.setEnabled(False)
        self.next_test_button.clicked.connect(self._test_next)
        controls.addWidget(pick_coordinate)
        controls.addWidget(self.test_button)
        controls.addWidget(self.next_test_button)
        controls.addStretch(1)
        self.use_button = QtWidgets.QPushButton("이 핸들·좌표 사용")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._accept_selected)
        cancel = QtWidgets.QPushButton("취소")
        cancel.clicked.connect(self.reject)
        controls.addWidget(self.use_button)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self._selection_changed()

    def _current_row(self) -> int:
        return max(0, self.table.currentRow())

    def _selection_changed(self) -> None:
        row = self._current_row()
        if not 0 <= row < len(self.probe.candidates):
            return
        candidate = self.probe.candidates[row]
        self.detail.setText(
            f"선택 HWND 0x{candidate.hwnd:X}  ·  ClassNN {candidate.class_nn or '(최상위)'}  ·  "
            f"클래스 {candidate.class_name or '—'}  ·  텍스트 {candidate.text or '—'}"
        )

    def _host_windows_for_picker(self) -> list[tuple[QtWidgets.QWidget, bool, float]]:
        hosts: list[QtWidgets.QWidget] = []
        current = self.parentWidget()
        while isinstance(current, QtWidgets.QWidget):
            window = current.window()
            if window not in hosts and window is not self:
                hosts.append(window)
            current = current.parentWidget()
        states: list[tuple[QtWidgets.QWidget, bool, float]] = []
        for host in hosts:
            states.append((host, host.isVisible(), host.windowOpacity()))
            host.hide()
        return states

    @staticmethod
    def _restore_picker_hosts(states: list[tuple[QtWidgets.QWidget, bool, float]]) -> None:
        for host, was_visible, opacity in reversed(states):
            if was_visible:
                host.show()
            host.setWindowOpacity(opacity)

    def _pick_test_point(self) -> None:
        old_opacity = self.windowOpacity()
        self.setWindowOpacity(0.0)
        host_states = self._host_windows_for_picker()
        wait_loop = QtCore.QEventLoop(self)
        QtCore.QTimer.singleShot(140, wait_loop.quit)
        wait_loop.exec()
        ignored = {int(self.winId())}
        ignored.update(int(host.winId()) for host, _visible, _opacity in host_states)
        picker = HandlePointPicker(ignored_hwnds=ignored, required_root=self.probe.root_hwnd)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted and picker.selected_point is not None
        self._restore_picker_hosts(host_states)
        self.setWindowOpacity(old_opacity)
        self.show()
        self.raise_()
        self.activateWindow()
        if accepted and picker.selected_point is not None:
            self.set_test_point(picker.selected_point)

    def set_test_point(self, point: QtCore.QPoint) -> None:
        self.test_point = QtCore.QPoint(point)
        client = _screen_to_client(self.probe.root_hwnd, self.test_point)
        self.coordinate_label.setText(
            f"시험 좌표 · 화면 {self.test_point.x()}, {self.test_point.y()} · 대상 창 client {client.x()}, {client.y()}"
        )
        self.status.setText("좌표가 저장되었습니다. 후보 핸들을 선택하고 시험 클릭해 실제 반응을 확인하세요.")
        self.status.setStyleSheet("")
        self.test_button.setEnabled(True)
        self.next_test_button.setEnabled(True)
        self.use_button.setEnabled(True)

    def _test_row(self, row: int) -> None:
        if not 0 <= row < len(self.probe.candidates):
            return
        if self.test_point is None:
            self.status.setText("시험 클릭 좌표를 먼저 지정하세요.")
            self.status.setStyleSheet("color:#FF667A; font-weight:700;")
            return
        self.table.selectRow(row)
        candidate = self.probe.candidates[row]
        sent, client = post_inactive_test_click(candidate.hwnd, self.test_point)
        if sent:
            self.status.setText(
                f"메시지 전송 완료 · HWND 0x{candidate.hwnd:X} · client {client.x()}, {client.y()} · 실제 반응을 확인하세요."
            )
            self.status.setStyleSheet("color:#41D9D2; font-weight:700;")
        else:
            self.status.setText("메시지 전송 실패 · 핸들이 닫혔거나 권한 수준이 다를 수 있습니다.")
            self.status.setStyleSheet("color:#FF667A; font-weight:700;")

    def _test_selected(self) -> None:
        self._test_row(self._current_row())

    def _test_next(self) -> None:
        if not self.probe.candidates:
            return
        row = (self._current_row() + 1) % len(self.probe.candidates)
        self._test_row(row)

    def _accept_selected(self) -> None:
        row = self._current_row()
        if not 0 <= row < len(self.probe.candidates) or self.test_point is None:
            return
        self.selected_candidate = self.probe.candidates[row]
        self.accept()

    def selected_payload(self) -> dict[str, Any]:
        candidate = self.selected_candidate
        if candidate is None or self.test_point is None:
            return {}
        root_client_point = _screen_to_client(self.probe.root_hwnd, self.test_point)
        return {
            "window": self.probe.window_token,
            "window_exe": self.probe.exe_name,
            "x": root_client_point.x(),
            "y": root_client_point.y(),
            "method": "handle_probe",
            "target_control": candidate.class_nn,
            "target_hwnd": f"0x{candidate.hwnd:X}",
            "target_child_class": candidate.class_name,
            "target_root_hwnd": f"0x{self.probe.root_hwnd:X}",
            "target_root_class": self.probe.class_name,
        }
