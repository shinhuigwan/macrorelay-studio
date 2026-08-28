from __future__ import annotations

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
        self.resize(760, 560)

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
        root.addWidget(self.log_view, 1)

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
