from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .diagnostics import build_diagnostic_bundle
from .theme import COLORS


class MacroLogDialog(QtWidgets.QDialog):
    """가벼운 파일 감시 방식으로 Studio와 매크로 실행 로그를 보여줍니다."""

    MAX_BYTES = 512 * 1024

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("실행 로그 · MacroRelay Studio")
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(980, 760)

        self.sources: list[tuple[str, tuple[Path, ...]]] = [
            (
                "통합 로그",
                (
                    repository.exports_dir / "studio_run.log",
                    repository.exports_dir / "macro_log.txt",
                ),
            ),
            ("Studio 실행", (repository.exports_dir / "studio_run.log",)),
            ("매크로 실행", (repository.exports_dir / "macro_log.txt",)),
            ("단계 실행 추적", (repository.exports_dir / "execution_trace.log",)),
            ("Quick Slots", (repository.root / "runtime" / "runner.log",)),
            ("브라우저", (repository.exports_dir / "browser_action_log.txt",)),
            ("비활성 클릭", (repository.exports_dir / "inactive_click_test.log",)),
        ]
        self._signature: tuple[tuple[str, int, int], ...] = ()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QtWidgets.QLabel("실행 로그")
        title.setStyleSheet("font-size: 17pt; font-weight: 750;")
        subtitle = QtWidgets.QLabel("실행 요청, 시작 PID, 종료 코드와 매크로 단계별 기록을 자동으로 갱신합니다.")
        subtitle.setObjectName("Muted")
        root.addWidget(title)
        root.addWidget(subtitle)

        debugger = QtWidgets.QGroupBox("실행 디버거")
        debugger_layout = QtWidgets.QVBoxLayout(debugger)
        debugger_header = QtWidgets.QHBoxLayout()
        self.debug_status = QtWidgets.QLabel("실행 대기")
        self.debug_status.setStyleSheet("font-weight:700; color:#9DA7BA;")
        pause_btn = QtWidgets.QPushButton("Ⅱ 일시정지")
        step_btn = QtWidgets.QPushButton("▷ 한 단계")
        resume_btn = QtWidgets.QPushButton("▶ 재개")
        stop_btn = QtWidgets.QPushButton("■ 중단")
        pause_btn.clicked.connect(lambda: self._invoke_host("pause_running_macros"))
        step_btn.clicked.connect(lambda: self._invoke_host("step_running_macros"))
        resume_btn.clicked.connect(lambda: self._invoke_host("resume_running_macros"))
        stop_btn.clicked.connect(lambda: self._invoke_host("stop_running_macros"))
        self.debug_control_buttons = (pause_btn, step_btn, resume_btn, stop_btn)
        debugger_header.addWidget(self.debug_status, 1)
        debugger_header.addWidget(pause_btn)
        debugger_header.addWidget(step_btn)
        debugger_header.addWidget(resume_btn)
        debugger_header.addWidget(stop_btn)
        debugger_layout.addLayout(debugger_header)
        debugger_split = QtWidgets.QSplitter()
        self.timeline_table = QtWidgets.QTableWidget(0, 5)
        self.timeline_table.setHorizontalHeaderLabels(["노드", "동작", "상태", "소요 시간", "결과 상세"])
        self.timeline_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.timeline_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.variable_table = QtWidgets.QTableWidget(0, 2)
        self.variable_table.setHorizontalHeaderLabels(["변수", "현재 값"])
        self.variable_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.variable_table.verticalHeader().setVisible(False)
        self.variable_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.variable_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        debugger_split.addWidget(self.timeline_table)
        debugger_split.addWidget(self.variable_table)
        debugger_split.setSizes([680, 240])
        debugger_layout.addWidget(debugger_split)
        root.addWidget(debugger, 1)

        controls = QtWidgets.QHBoxLayout()
        self.source_combo = QtWidgets.QComboBox()
        for label, _paths in self.sources:
            self.source_combo.addItem(label)
        self.source_combo.currentIndexChanged.connect(self.refresh)
        refresh_btn = QtWidgets.QPushButton("새로고침")
        refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        clear_btn = QtWidgets.QPushButton("현재 로그 지우기")
        clear_btn.clicked.connect(self._clear_current)
        bundle_btn = QtWidgets.QPushButton("진단 자료 저장")
        bundle_btn.setToolTip("로그·실행 추적·엔진 상태를 개인정보 제거 후 ZIP으로 저장")
        bundle_btn.clicked.connect(self._save_diagnostic_bundle)
        controls.addWidget(self.source_combo, 1)
        controls.addWidget(refresh_btn)
        controls.addWidget(clear_btn)
        controls.addWidget(bundle_btn)
        root.addLayout(controls)

        self.path_label = QtWidgets.QLabel()
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        root.addWidget(self.path_label)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        fixed_font.setPointSize(10)
        self.log_view.setFont(fixed_font)
        self.log_view.setStyleSheet(
            f"QPlainTextEdit {{ background:#0C0F15; border-color:{COLORS['border']}; color:#DCE5F2; }}"
        )
        self.log_view.setMaximumHeight(230)
        root.addWidget(self.log_view)

        hint = QtWidgets.QLabel("로그가 비어 있으면 실행 버튼을 누른 뒤 이 창에서 실패 지점과 종료 코드를 확인하세요.")
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh(force=True)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._place_next_to_parent()
        self.refresh(force=True)

    def _place_next_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        host = parent.window().frameGeometry()
        screen = QtGui.QGuiApplication.screenAt(host.center()) or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        size = self.frameGeometry().size()
        x = host.right() + 12
        if x + size.width() > available.right() + 1:
            x = host.left() - size.width() - 12
        if x < available.left():
            x = available.right() - size.width() + 1
        y = max(available.top(), min(host.top() + 48, available.bottom() - size.height() + 1))
        self.move(x, y)

    def _current_paths(self) -> tuple[Path, ...]:
        index = max(0, min(self.source_combo.currentIndex(), len(self.sources) - 1))
        return self.sources[index][1]

    def _file_signature(self, paths: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
        result: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
                result.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                result.append((str(path), 0, 0))
        return tuple(result)

    @QtCore.Slot()
    def refresh(self, force: bool = False) -> None:
        self._refresh_debugger()
        paths = self._current_paths()
        signature = self._file_signature(paths)
        if not force and signature == self._signature:
            return
        self._signature = signature
        self.path_label.setText("  ·  ".join(str(path) for path in paths))
        blocks: list[str] = []
        for path in paths:
            label = next((name for name, source_paths in self.sources[1:] if path in source_paths), path.name)
            try:
                with path.open("rb") as handle:
                    handle.seek(max(0, path.stat().st_size - self.MAX_BYTES))
                    raw = handle.read()
                content = raw.decode("utf-8-sig", errors="replace").strip()
            except OSError:
                content = "(아직 기록된 로그가 없습니다.)"
            blocks.append(f"[{label}]\n{content or '(로그가 비어 있습니다.)'}")
        self.log_view.setPlainText("\n\n".join(blocks))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    def _invoke_host(self, method_name: str) -> None:
        host = self.parentWidget()
        method = getattr(host, method_name, None)
        if callable(method):
            method()
        self._refresh_debugger()

    @staticmethod
    def _parse_trace_timestamp(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _refresh_debugger(self) -> None:
        host = self.parentWidget()
        running = bool(getattr(host, "_running_macro_processes", {}))
        for button in self.debug_control_buttons:
            button.setEnabled(running)
        path = self.repository.exports_dir / "execution_trace.log"
        try:
            raw_lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            raw_lines = []
        rows: list[dict[str, object]] = []
        open_rows: dict[int, int] = {}
        variables: dict[str, str] = {}
        captures: list[str] = []
        for raw in raw_lines:
            parts = raw.split("|", 4)
            if len(parts) != 5:
                continue
            stamp_text, step_text, status_text, label, detail = parts
            try:
                step = int(step_text)
            except ValueError:
                continue
            status = status_text.strip().upper()
            stamp = self._parse_trace_timestamp(stamp_text)
            if status == "START" and step > 0:
                rows.append(
                    {"step": step, "label": label, "status": "RUNNING", "start": stamp, "duration": 0, "detail": ""}
                )
                open_rows[step] = len(rows) - 1
            elif status in {"SUCCESS", "FAIL"} and step > 0:
                row_index = open_rows.get(step)
                if row_index is None:
                    rows.append(
                        {"step": step, "label": label, "status": status, "start": stamp, "duration": 0, "detail": ""}
                    )
                    row_index = len(rows) - 1
                row = rows[row_index]
                started = row.get("start")
                duration = (
                    max(0, round((stamp - started).total_seconds() * 1000))
                    if isinstance(stamp, datetime) and isinstance(started, datetime)
                    else 0
                )
                row["status"] = status
                row["duration"] = duration
                open_rows.pop(step, None)
            elif status == "DETAIL" and step > 0:
                row_index = open_rows.get(step)
                if row_index is None:
                    row_index = next((index for index in range(len(rows) - 1, -1, -1) if rows[index]["step"] == step), None)
                if row_index is not None:
                    existing = str(rows[row_index].get("detail") or "")
                    rows[row_index]["detail"] = f"{existing} · {detail}".strip(" ·")
                marker = "; var:"
                variable_payload = detail.split(marker, 1)[1] if marker in detail else detail[4:] if detail.startswith("var:") else ""
                if "=" in variable_payload:
                    name, value = variable_payload.split("=", 1)
                    if name.strip():
                        variables[name.strip()] = value.strip()
            elif status == "CAPTURE" and detail:
                captures.append(detail)

        self.timeline_table.setRowCount(len(rows))
        status_labels = {"RUNNING": "실행 중", "SUCCESS": "성공", "FAIL": "실패"}
        status_colors = {"RUNNING": "#38E7FF", "SUCCESS": COLORS["success"], "FAIL": COLORS["danger"]}
        for row_index, row in enumerate(rows):
            status = str(row.get("status") or "")
            values = [
                str(row.get("step") or ""),
                str(row.get("label") or ""),
                status_labels.get(status, status),
                f"{int(row.get('duration') or 0)} ms" if status != "RUNNING" else "—",
                str(row.get("detail") or ""),
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(QtGui.QColor(status_colors.get(status, COLORS["muted"])))
                self.timeline_table.setItem(row_index, column, item)
        self.variable_table.setRowCount(len(variables))
        for row_index, (name, value) in enumerate(sorted(variables.items())):
            self.variable_table.setItem(row_index, 0, QtWidgets.QTableWidgetItem(name))
            self.variable_table.setItem(row_index, 1, QtWidgets.QTableWidgetItem(value))
        current = next((row for row in reversed(rows) if row.get("status") == "RUNNING"), None)
        if current is not None:
            self.debug_status.setText(f"● {current['step']}번 노드 실행 중 · {current['label']}")
            self.debug_status.setStyleSheet("font-weight:700; color:#38E7FF;")
        elif rows:
            last = rows[-1]
            label = status_labels.get(str(last.get("status") or ""), str(last.get("status") or ""))
            capture_note = f" · 실패 화면 {len(captures)}장" if captures else ""
            self.debug_status.setText(f"{last['step']}번 노드 {label} · {int(last.get('duration') or 0)} ms{capture_note}")
            self.debug_status.setStyleSheet(
                f"font-weight:700; color:{status_colors.get(str(last.get('status') or ''), COLORS['muted'])};"
            )
        else:
            self.debug_status.setText("실행 대기")
            self.debug_status.setStyleSheet("font-weight:700; color:#9DA7BA;")

    def _clear_current(self) -> None:
        paths = self._current_paths()
        answer = QtWidgets.QMessageBox.question(
            self,
            "로그 지우기",
            f"현재 선택한 로그 {len(paths)}개를 비울까요?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        for path in paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, "로그 지우기 실패", str(exc))
                break
        self.refresh(force=True)

    def _save_diagnostic_bundle(self) -> None:
        stamp = QtCore.QDateTime.currentDateTime().toString("yyyyMMdd-HHmmss")
        default = self.repository.root / "exports" / f"MacroRelay-diagnostics-{stamp}.zip"
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "진단 자료 저장",
            str(default),
            "ZIP 파일 (*.zip)",
        )
        if not selected:
            return
        destination = Path(selected)
        if destination.suffix.casefold() != ".zip":
            destination = destination.with_suffix(".zip")
        screens = [
            {
                "name": screen.name(),
                "geometry": [screen.geometry().x(), screen.geometry().y(), screen.geometry().width(), screen.geometry().height()],
                "available": [
                    screen.availableGeometry().x(),
                    screen.availableGeometry().y(),
                    screen.availableGeometry().width(),
                    screen.availableGeometry().height(),
                ],
                "device_pixel_ratio": screen.devicePixelRatio(),
                "logical_dpi": screen.logicalDotsPerInch(),
            }
            for screen in QtGui.QGuiApplication.screens()
        ]
        try:
            saved = build_diagnostic_bundle(self.repository.root, destination, screens)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "진단 자료 저장 실패", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self,
            "진단 자료 저장 완료",
            f"개인정보와 인증 키를 제거한 진단 자료를 저장했습니다.\n\n{saved}",
        )
