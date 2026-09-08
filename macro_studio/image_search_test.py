from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import re
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .screen_coordinates import display_coordinate_maps
from .theme import COLORS


def _virtual_screen_region() -> list[int]:
    geometry = QtCore.QRect()
    for item in display_coordinate_maps():
        geometry = geometry.united(item.native)
    if not geometry.isValid():
        for screen in QtGui.QGuiApplication.screens():
            geometry = geometry.united(screen.geometry())
    return [geometry.left(), geometry.top(), geometry.right() + 1, geometry.bottom() + 1]


def _find_window(
    exe_name: str,
    window_token: str,
    reference_rect: QtCore.QRect | None = None,
) -> int:
    """Resolve the intended top-level window, not merely the first matching EXE."""
    token = str(window_token or "").strip()
    id_match = re.search(r"ahk_id\s+([^\s,]+)", token, re.IGNORECASE)
    requested_hwnd = 0
    if id_match:
        try:
            requested_hwnd = int(id_match.group(1), 0)
        except ValueError:
            requested_hwnd = 0

    wanted_exe = Path(str(exe_name or "")).name.casefold()
    exe_match = re.search(r"ahk_exe\s+([^\s,]+)", token, re.IGNORECASE)
    if exe_match:
        wanted_exe = Path(exe_match.group(1)).name.casefold()
    class_match = re.search(r"ahk_class\s+([^\s,]+)", token, re.IGNORECASE)
    wanted_class = class_match.group(1).strip().casefold() if class_match else ""
    pid_match = re.search(r"ahk_pid\s+([^\s,]+)", token, re.IGNORECASE)
    try:
        wanted_pid = int(pid_match.group(1), 0) if pid_match else 0
    except ValueError:
        wanted_pid = 0
    wanted_title = re.sub(
        r"ahk_(?:id|pid|class|exe)\s+[^\s,]+", "", token, flags=re.IGNORECASE
    ).strip().casefold()
    if not any((requested_hwnd, wanted_exe, wanted_class, wanted_pid, wanted_title)):
        return 0
    matches: list[tuple[int, int]] = []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    def candidate_score(hwnd: int) -> int | None:
        if not hwnd or not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if wanted_pid and int(pid.value) != wanted_pid:
            return None
        class_buffer = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        current_class = class_buffer.value.strip().casefold()
        if wanted_class and current_class != wanted_class:
            return None
        title_buffer = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        current_title = title_buffer.value.strip().casefold()
        if wanted_title and wanted_title not in current_title:
            return None
        if wanted_exe:
            current_exe = ""
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    size = wintypes.DWORD(32768)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        current_exe = Path(buffer.value).name.casefold()
                finally:
                    kernel32.CloseHandle(handle)
            if current_exe != wanted_exe:
                return None
        client = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            return None
        width = int(client.right - client.left)
        height = int(client.bottom - client.top)
        if width < 30 or height < 30:
            return None
        score = min(100, max(0, width * height // 100_000))
        if current_title:
            score += 20
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            score += 150
        if reference_rect is not None and reference_rect.isValid():
            window_rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
                candidate_rect = QtCore.QRect(
                    int(window_rect.left), int(window_rect.top),
                    int(window_rect.right - window_rect.left), int(window_rect.bottom - window_rect.top),
                )
                if candidate_rect.contains(reference_rect.center()):
                    score += 2000
                elif candidate_rect.intersects(reference_rect):
                    score += 1000
                else:
                    score -= 1000
        return score

    if requested_hwnd:
        direct_score = candidate_score(requested_hwnd)
        if direct_score is not None and (reference_rect is None or direct_score >= 1000):
            return requested_hwnd

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        score = candidate_score(int(hwnd))
        if score is not None:
            matches.append((int(hwnd), score))
        return True

    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return 0
    if not matches:
        return 0
    matches.sort(key=lambda item: item[1], reverse=True)
    return matches[0][0]


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


def resolve_asset_test_regions(step: dict[str, Any], aliases: list[str]) -> list[list[list[int]]] | None:
    raw_asset_regs = step.get("asset_regions") if isinstance(step.get("asset_regions"), dict) else {}
    if not raw_asset_regs or not any(a in raw_asset_regs for a in aliases):
        return None

    mode = str(step.get("region_mode") or "screen").casefold()
    coordinate_mode = str(step.get("region_coords") or "screen").casefold()
    base_x, base_y = 0, 0
    if mode != "screen":
        hwnd = _find_window(
            str(step.get("region_window_exe") or (step.get("click") or {}).get("window_exe") or ""),
            str(step.get("region_window") or (step.get("click") or {}).get("window") or ""),
        )
        if hwnd:
            user32 = ctypes.windll.user32
            if mode == "client":
                origin = wintypes.POINT(0, 0)
                if user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                    base_x, base_y = int(origin.x), int(origin.y)
            else:
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    base_x, base_y = int(rect.left), int(rect.top)

    res: list[list[list[int]]] = []
    has_any = False
    for alias in aliases:
        cand = raw_asset_regs.get(alias)
        if isinstance(cand, (list, tuple)) and len(cand) >= 4:
            try:
                l, t, r, b = int(cand[0]), int(cand[1]), int(cand[2]), int(cand[3])
                if r > l and b > t:
                    if coordinate_mode == "relative":
                        l, t, r, b = base_x + l, base_y + t, base_x + r, base_y + b
                    res.append([[l, t, r, b]])
                    has_any = True
                    continue
            except (TypeError, ValueError):
                pass
        res.append([])
    return res if has_any else None


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
        self.visual_test_button = QtWidgets.QPushButton("🔍 영역 시각화 검사")
        self.visual_test_button.setStyleSheet("background: #065F46; border: 1px solid #059669; color: #A7F3D0; font-weight: 700; padding: 6px 12px; border-radius: 6px;")
        self.visual_test_button.setToolTip("실제 화면 캡처 위에 각 영역 사각형 박스를 표시하여 위치 왜곡을 실시간으로 확인하고 보정합니다.")
        self.visual_test_button.clicked.connect(self._open_visual_test_dialog)
        self.apply_button = QtWidgets.QPushButton("추천값 적용")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_recommendation)
        close_button = QtWidgets.QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.visual_test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def _open_visual_test_dialog(self) -> None:
        from .region_visual_test import RegionVisualTestDialog

        dlg = RegionVisualTestDialog(self.step, self.repository, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            updated = dlg.get_asset_regions()
            if updated:
                self.step["asset_regions"] = updated
                bounding = dlg.get_bounding_region()
                if bounding:
                    self.step["region"] = bounding
                    self.step["regions"] = [bounding]
            self.start_test()

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
        asset_regs = resolve_asset_test_regions(self.step, self._aliases)
        request = {
            "cmd": "benchmark",
            "images": [str(path) for path in self._paths if path is not None],
            "regions": regions,
            "threshold": max(0.5, min(0.99, float(self.step.get("confidence") or 86) / 100)),
            "profile": str(self.step.get("search_profile") or "balanced"),
        }
        if asset_regs:
            request["image_regions"] = asset_regs
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
