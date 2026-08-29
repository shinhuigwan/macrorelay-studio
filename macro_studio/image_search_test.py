from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .theme import COLORS


def _virtual_screen_region() -> list[int]:
    geometry = QtCore.QRect()
    for screen in QtGui.QGuiApplication.screens():
        geometry = geometry.united(screen.geometry())
    return [geometry.left(), geometry.top(), geometry.right() + 1, geometry.bottom() + 1]


def _find_window(exe_name: str, window_token: str) -> int:
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


def resolve_test_regions(step: dict[str, Any]) -> tuple[list[list[int]], str]:
    mode = str(step.get("region_mode") or "screen").casefold()
    coordinate_mode = str(step.get("region_coords") or "screen").casefold()
    raw_regions = step.get("regions") if isinstance(step.get("regions"), list) else []
    if not raw_regions and isinstance(step.get("region"), list):
        raw_regions = [step["region"]]
    valid: list[list[int]] = []
    for raw in raw_regions:
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            continue
        values = [int(value or 0) for value in raw[:4]]
        if values[2] > values[0] and values[3] > values[1]:
            valid.append(values)
    if mode == "screen":
        return (valid or [_virtual_screen_region()]), "화면"
    hwnd = _find_window(
        str(step.get("region_window_exe") or (step.get("click") or {}).get("window_exe") or ""),
        str(step.get("region_window") or (step.get("click") or {}).get("window") or ""),
    )
    if not hwnd:
        raise RuntimeError("대상 창을 찾지 못했습니다. 대상 프로그램을 다시 지정하세요.")
    user32 = ctypes.windll.user32
    if mode == "client":
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise RuntimeError("대상 창의 클라이언트 좌표를 읽지 못했습니다.")
        base_x, base_y = int(origin.x), int(origin.y)
        width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    else:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("대상 창의 화면 영역을 읽지 못했습니다.")
        base_x, base_y = int(rect.left), int(rect.top)
        width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    if coordinate_mode == "relative" and valid:
        translated = [[base_x + left, base_y + top, base_x + right, base_y + bottom] for left, top, right, bottom in valid]
    elif valid:
        translated = valid
    else:
        translated = [[base_x, base_y, base_x + width, base_y + height]]
    return translated, f"{'클라이언트' if mode == 'client' else '창'} · {base_x},{base_y} · {width}×{height}"


class BenchmarkWorker(QtCore.QObject):
    completed = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__()
        self.request = request

    @QtCore.Slot()
    def run(self) -> None:
        try:
            from vision_engine import VisionState

            state = VisionState()
            try:
                result = state.benchmark(self.request)
            finally:
                state.close()
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ImageSearchTestDialog(QtWidgets.QDialog):
    def __init__(self, repository: MacroRepository, step: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.step = dict(step)
        self.applied_settings: dict[str, Any] = {}
        self._aliases = self._step_aliases(step)
        self._paths = [repository.asset_path(alias) for alias in self._aliases]
        self._thread: QtCore.QThread | None = None
        self._worker: BenchmarkWorker | None = None
        self.setWindowTitle("이미지 서치 테스트 센터")
        self.setMinimumSize(900, 610)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("이미지 서치 테스트 센터")
        title.setStyleSheet("font-size:17pt; font-weight:750;")
        subtitle = QtWidgets.QLabel("현재 화면을 한 번 캡처해 이미지별 정확도·속도·좌표를 비교하고 실패 원인을 구분합니다.")
        subtitle.setObjectName("Muted")
        root.addWidget(title)
        root.addWidget(subtitle)
        self.region_label = QtWidgets.QLabel("검색 범위를 확인하는 중…")
        self.region_label.setObjectName("Muted")
        root.addWidget(self.region_label)
        self.table = QtWidgets.QTableWidget(len(self._aliases), 7)
        self.table.setHorizontalHeaderLabels(["이미지", "결과", "정확도", "시간", "탐지 좌표", "배율", "진단"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QtCore.QSize(64, 44))
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        for row, (alias, path) in enumerate(zip(self._aliases, self._paths)):
            item = QtWidgets.QTableWidgetItem(alias)
            if path is not None:
                item.setIcon(QtGui.QIcon(str(path)))
                item.setToolTip(str(path))
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("대기"))
        root.addWidget(self.table, 1)
        self.recommendation = QtWidgets.QLabel("테스트 후 추천 설정이 여기에 표시됩니다.")
        self.recommendation.setWordWrap(True)
        self.recommendation.setStyleSheet("background:#101722; border:1px solid #2E3B50; border-radius:8px; padding:10px;")
        root.addWidget(self.recommendation)
        buttons = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("▶ 현재 화면 테스트")
        self.test_button.clicked.connect(self.start_test)
        self.apply_button = QtWidgets.QPushButton("추천값 적용")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_recommendation)
        close_button = QtWidgets.QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    @staticmethod
    def _step_aliases(step: dict[str, Any]) -> list[str]:
        aliases = [str(value) for value in step.get("assets", []) if str(value).strip()] if isinstance(step.get("assets"), list) else []
        primary = str(step.get("asset") or "").strip()
        if primary and primary not in aliases:
            aliases.insert(0, primary)
        return list(dict.fromkeys(aliases))

    def start_test(self) -> None:
        if self._thread is not None:
            return
        if not self._aliases:
            QtWidgets.QMessageBox.warning(self, "테스트 불가", "검색 이미지가 선택되지 않았습니다.")
            return
        missing = [alias for alias, path in zip(self._aliases, self._paths) if path is None or not path.is_file()]
        if missing:
            QtWidgets.QMessageBox.warning(self, "테스트 불가", "이미지 파일이 없습니다: " + ", ".join(missing))
            return
        try:
            regions, description = resolve_test_regions(self.step)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "검색 범위 오류", str(exc))
            return
        self.region_label.setText(f"검색 범위: {description} · {len(regions)}개 영역 · 화면은 테스트 시작 시 한 번만 캡처")
        request = {
            "cmd": "benchmark",
            "images": [str(path) for path in self._paths if path is not None],
            "regions": regions,
            "threshold": max(0.5, min(0.99, float(self.step.get("confidence") or 86) / 100)),
            "profile": str(self.step.get("search_profile") or "balanced"),
        }
        self.test_button.setEnabled(False)
        self.test_button.setText("테스트 중…")
        self.apply_button.setEnabled(False)
        self._thread = QtCore.QThread(self)
        self._worker = BenchmarkWorker(request)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.completed.connect(self._show_results)
        self._worker.failed.connect(self._show_failure)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    @QtCore.Slot(dict)
    def _show_results(self, report: dict[str, Any]) -> None:
        selected_index = int(report.get("selected_index") or 0)
        results = report.get("results") if isinstance(report.get("results"), list) else []
        best: dict[str, Any] | None = None
        threshold = float(report.get("threshold") or 0.86)
        for row, result in enumerate(results):
            if not isinstance(result, dict) or row >= self.table.rowCount():
                continue
            found = bool(result.get("found"))
            score = float(result.get("score") or result.get("best_score") or 0)
            selected = int(result.get("index") or 0) == selected_index
            status = "✓ 선택됨" if selected else "탐지" if found else "실패"
            diagnosis = self._diagnosis(found, score, threshold, result)
            values = [
                status,
                f"{score:.1%}",
                f"{float(result.get('elapsed_ms') or 0):.1f} ms",
                f"X {result.get('x')}, Y {result.get('y')}" if found else "—",
                f"{float(result.get('scale_x') or 0):.2f}×{float(result.get('scale_y') or 0):.2f}" if found else "—",
                diagnosis,
            ]
            for offset, value in enumerate(values, start=1):
                item = QtWidgets.QTableWidgetItem(value)
                if offset == 1:
                    item.setForeground(QtGui.QColor(COLORS["success"] if found else COLORS["danger"]))
                self.table.setItem(row, offset, item)
            if selected:
                best = result
                self.table.selectRow(row)
        self.applied_settings = self._recommend(best, threshold)
        if best is None:
            highest = max((float(item.get("best_score") or 0) for item in results if isinstance(item, dict)), default=0)
            self.recommendation.setText(
                f"전부 실패 · 최고 유사도 {highest:.1%}\n검색 범위와 대상 창을 먼저 확인하고, 이미지 크기가 달라질 수 있으면 정밀 프로필을 사용하세요."
            )
            self.apply_button.setEnabled(False)
        else:
            profile = self.applied_settings["search_profile"]
            confidence = self.applied_settings["confidence"]
            self.recommendation.setText(
                f"추천: 검색 품질 {profile} · 최소 신뢰도 {confidence}% · 선택 이미지 {self._aliases[selected_index - 1]}\n"
                f"전체 비교 {float(report.get('elapsed_ms') or 0):.1f} ms · 실제 탐지 좌표 X {best.get('x')}, Y {best.get('y')}"
            )
            self.apply_button.setEnabled(True)

    @staticmethod
    def _diagnosis(found: bool, score: float, threshold: float, result: dict[str, Any]) -> str:
        if found:
            scale_x = float(result.get("scale_x") or 1)
            scale_y = float(result.get("scale_y") or 1)
            if abs(scale_x - 1) > 0.12 or abs(scale_y - 1) > 0.12:
                return "탐지 성공 · 원본과 크기 차이 있음"
            return "정상 탐지"
        if score >= threshold - 0.05:
            return "거의 일치 · 신뢰도 또는 배율 조정 필요"
        if score >= 0.35:
            return "부분 유사 · 크기·색상·투명 배경 확인"
        return "유사도 낮음 · 검색 범위 또는 이미지 내용 확인"

    @staticmethod
    def _recommend(best: dict[str, Any] | None, current_threshold: float) -> dict[str, Any]:
        if best is None:
            return {}
        score = float(best.get("score") or current_threshold)
        scale_x = float(best.get("scale_x") or 1)
        scale_y = float(best.get("scale_y") or 1)
        scale_gap = max(abs(scale_x - 1), abs(scale_y - 1))
        profile = "fast" if score >= 0.93 and scale_gap <= 0.04 else "balanced" if scale_gap <= 0.15 else "precise"
        presets = {"fast": (30, 30), "balanced": (16, 60), "precise": (6, 90)}
        variation, poll_delay = presets[profile]
        return {
            "search_profile": profile,
            "confidence": max(50, min(97, round((score - 0.03) * 100))),
            "variation": variation,
            "poll_delay": poll_delay,
        }

    @QtCore.Slot(str)
    def _show_failure(self, detail: str) -> None:
        self.recommendation.setText(f"테스트 실패: {detail}")
        QtWidgets.QMessageBox.warning(self, "이미지 서치 테스트 실패", detail)

    def _apply_recommendation(self) -> None:
        if self.applied_settings:
            self.accept()

    @QtCore.Slot()
    def _thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self.test_button.setEnabled(True)
        self.test_button.setText("▶ 현재 화면 테스트")

