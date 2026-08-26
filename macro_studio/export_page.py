from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .repository import MacroRepository
from .widgets import Card, PageHeader, primary_button


class ExportWorker(QtCore.QThread):
    progress = QtCore.Signal(str)
    completed = QtCore.Signal(object, str)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        repository: MacroRepository,
        name: str,
        output: Path | None,
        browser_fast: bool,
        runtime_mode: str,
        mode: str,
        compile_script: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.name = name
        self.output = output
        self.browser_fast = browser_fast
        self.runtime_mode = runtime_mode
        self.mode = mode
        self.compile_script = compile_script

    def run(self) -> None:
        try:
            if self.mode == "single":
                self.progress.emit("단일 포터블 EXE 구성 · 런타임과 자산을 검증하고 있습니다…")
                result = self.repository.export_single_file(
                    self.name, self.output, self.browser_fast, self.runtime_mode
                )
                self.completed.emit(result.executable, f"단일 포터블 EXE 생성 완료: {result.executable}")
            elif self.mode == "portable":
                self.progress.emit("포터블 패키지 구성 · 필요한 구성요소만 복사하고 있습니다…")
                result = self.repository.export_portable(
                    self.name, self.output, self.browser_fast, self.runtime_mode
                )
                self.completed.emit(
                    result.archive,
                    f"포터블 생성 완료 · 폴더: {result.folder} · ZIP: {result.archive}",
                )
            else:
                self.progress.emit("매크로 스크립트를 생성하고 있습니다…")
                script = self.repository.export(self.name, self.output, self.browser_fast, "auto")
                result = self.repository.compile(script) if self.compile_script else script
                self.completed.emit(result, f"생성 완료: {result}")
        except Exception as exc:
            self.failed.emit(str(exc))


class ExportPage(QtWidgets.QWidget):
    status = QtCore.Signal(str)
    run_macro = QtCore.Signal(str)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self._export_worker: ExportWorker | None = None
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(PageHeader("내보내기", "AHK 미리보기, EXE 컴파일, 테스트 실행을 한 화면에서 처리합니다."))

        controls = Card()
        controls_layout = QtWidgets.QGridLayout(controls)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        self.macro_combo = QtWidgets.QComboBox()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("비워 두면 exports 폴더에 저장")
        browse = QtWidgets.QPushButton("경로 선택")
        browse.clicked.connect(self._browse)
        self.browser_fast = QtWidgets.QCheckBox("브라우저 빠른 모드")
        self.compile_box = QtWidgets.QCheckBox("EXE도 함께 생성")
        self.portable_box = QtWidgets.QCheckBox("포터블 패키지 · 설치 없이 실행")
        self.portable_box.setToolTip("EXE, 이미지, Python/OpenCV와 보조 파일을 폴더와 ZIP으로 함께 생성합니다.")
        self.portable_box.toggled.connect(self._portable_toggled)
        self.single_file_box = QtWidgets.QCheckBox("단일 포터블 EXE · 파일 하나로 배포")
        self.single_file_box.setToolTip("모든 구성요소를 EXE 하나에 넣고 실행 시 로컬 캐시에 자동 준비합니다.")
        self.single_file_box.toggled.connect(self._single_file_toggled)
        self.runtime_combo = QtWidgets.QComboBox()
        self.runtime_combo.addItem("자동 선택 · 매크로 설정 사용", "auto")
        self.runtime_combo.addItem("AutoHotkey 전용 · Python 제외", "ahk")
        self.runtime_combo.addItem("Python/OpenCV 포함 · 정확도 우선", "python")
        self.runtime_combo.setToolTip(
            "AutoHotkey 전용은 가장 가볍습니다. Python/OpenCV 포함은 이미지 서치 정확도와 고급 기능을 우선합니다."
        )
        self.runtime_combo.currentIndexChanged.connect(self._runtime_mode_changed)
        self.runtime_hint = QtWidgets.QLabel()
        self.runtime_hint.setObjectName("Muted")
        self.portable_hint = QtWidgets.QLabel("포터블 모드는 사용 기능만 묶습니다. 단일 EXE는 최초 실행 시 로컬 캐시를 준비합니다.")
        self.portable_hint.setObjectName("Muted")
        preview = QtWidgets.QPushButton("미리보기 갱신")
        preview.clicked.connect(self._preview)
        self.export_button = primary_button("내보내기")
        self.export_button.clicked.connect(self._export)
        run = QtWidgets.QPushButton("▶ 테스트 실행")
        run.clicked.connect(self._run)
        controls_layout.addWidget(QtWidgets.QLabel("매크로"), 0, 0)
        controls_layout.addWidget(self.macro_combo, 0, 1, 1, 3)
        controls_layout.addWidget(QtWidgets.QLabel("출력 경로"), 1, 0)
        controls_layout.addWidget(self.output_edit, 1, 1, 1, 2)
        controls_layout.addWidget(browse, 1, 3)
        controls_layout.addWidget(self.browser_fast, 2, 1)
        controls_layout.addWidget(self.compile_box, 2, 2)
        controls_layout.addWidget(self.portable_box, 2, 3)
        controls_layout.addWidget(self.single_file_box, 3, 1, 1, 3)
        controls_layout.addWidget(QtWidgets.QLabel("포터블 실행 구성"), 4, 0)
        controls_layout.addWidget(self.runtime_combo, 4, 1, 1, 3)
        controls_layout.addWidget(self.runtime_hint, 5, 1, 1, 3)
        controls_layout.addWidget(self.portable_hint, 6, 1, 1, 3)
        controls_layout.addWidget(preview, 7, 1)
        controls_layout.addWidget(self.export_button, 7, 2)
        controls_layout.addWidget(run, 7, 3)
        self.export_progress = QtWidgets.QProgressBar()
        self.export_progress.setRange(0, 0)
        self.export_progress.setTextVisible(False)
        self.export_progress.setFixedHeight(4)
        self.export_progress.hide()
        controls_layout.addWidget(self.export_progress, 8, 1, 1, 3)
        root.addWidget(controls)
        self._sync_export_mode()

        card = Card()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        self.preview_edit = QtWidgets.QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        card_layout.addWidget(self.preview_edit)
        root.addWidget(card, 1)

    def refresh(self) -> None:
        previous = self.macro_combo.currentText()
        self.macro_combo.clear()
        for summary in self.repository.list_macros():
            self.macro_combo.addItem(summary.name)
        if previous:
            self.macro_combo.setCurrentText(previous)
        if self.macro_combo.count() and not self.preview_edit.toPlainText():
            self._preview()

    def select_macro(self, name: str) -> None:
        index = self.macro_combo.findText(name)
        if index < 0:
            self.refresh()
            index = self.macro_combo.findText(name)
        if index >= 0:
            self.macro_combo.setCurrentIndex(index)
            self._preview()

    def _browse(self) -> None:
        initial_path = self._output_path(self.output_edit.text()) or self._default_output_path()
        single_file = self.single_file_box.isChecked()
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "단일 포터블 EXE 경로 지정" if single_file else "AHK 내보내기 경로 지정",
            str(initial_path),
            "실행 파일 (*.exe)" if single_file else "AutoHotkey (*.ahk)",
        )
        if filename:
            selected_path = self._output_path(filename)
            if selected_path is not None:
                self.output_edit.setText(str(selected_path))
                self.status.emit(f"저장 경로 지정: {selected_path}")

    def _default_output_path(self) -> Path:
        name = self.macro_combo.currentText().strip() or "macro"
        safe_name = self.repository.safe_name(name)
        if self.single_file_box.isChecked():
            return self.repository.exports_dir / f"{safe_name}-portable.exe"
        return self.repository.exports_dir / f"{safe_name}.ahk"

    def _output_path(self, value: str) -> Path | None:
        raw = value.strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_dir() or raw.endswith(("/", "\\")):
            path /= self._default_output_path().name
        suffix = ".exe" if self.single_file_box.isChecked() else ".ahk"
        if path.suffix.casefold() != suffix:
            path = path.with_suffix(suffix)
        return path

    def _preview(self) -> None:
        name = self.macro_combo.currentText()
        if not name:
            return
        try:
            script = self.repository.render(name, self.browser_fast.isChecked(), self._runtime_mode())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "미리보기 실패", str(exc))
            return
        self.preview_edit.setPlainText(script)
        self.status.emit(f"{name} 스크립트를 생성했습니다.")

    def _portable_toggled(self, enabled: bool) -> None:
        if enabled:
            self.single_file_box.setChecked(False)
        self._sync_export_mode()

    def _single_file_toggled(self, enabled: bool) -> None:
        if enabled:
            self.portable_box.setChecked(False)
        self._sync_export_mode()

    def _sync_export_mode(self) -> None:
        bundled = self.portable_box.isChecked() or self.single_file_box.isChecked()
        if bundled:
            self.compile_box.setChecked(True)
        self.compile_box.setEnabled(not bundled)
        self.runtime_combo.setEnabled(bundled)
        self._runtime_mode_changed()

    def _runtime_mode(self) -> str:
        if not (self.portable_box.isChecked() or self.single_file_box.isChecked()):
            return "auto"
        return str(self.runtime_combo.currentData() or "auto")

    def _runtime_mode_changed(self, _index: int = -1) -> None:
        mode = self._runtime_mode()
        if mode == "ahk":
            text = "가벼운 구성 · Python은 포함하지 않으며 이미지 서치는 AutoHotkey 방식으로 내보냅니다."
        elif mode == "python":
            text = "정확도 우선 · Python/OpenCV를 포함하고 이미지 서치를 OpenCV 방식으로 내보냅니다."
        else:
            text = "자동 구성 · 각 단계에 저장된 엔진과 필요한 구성요소만 포함합니다."
        self.runtime_hint.setText(text)

    def _export(self) -> None:
        if self._export_worker is not None and self._export_worker.isRunning():
            self.status.emit("내보내기가 이미 진행 중입니다.")
            return
        name = self.macro_combo.currentText()
        if not name:
            return
        output = self._output_path(self.output_edit.text())
        if output is not None:
            self.output_edit.setText(str(output))
        mode = "single" if self.single_file_box.isChecked() else "portable" if self.portable_box.isChecked() else "script"
        self._export_worker = ExportWorker(
            self.repository,
            name,
            output,
            self.browser_fast.isChecked(),
            self._runtime_mode(),
            mode,
            self.compile_box.isChecked(),
            self,
        )
        self._export_worker.progress.connect(self.status.emit)
        self._export_worker.completed.connect(self._export_completed)
        self._export_worker.failed.connect(self._export_failed)
        self._export_worker.finished.connect(self._export_finished)
        self.export_button.setEnabled(False)
        self.export_button.setText("내보내는 중…")
        self.export_progress.show()
        self._export_worker.start()

    @QtCore.Slot(object, str)
    def _export_completed(self, _result: object, detail: str) -> None:
        self.status.emit(detail)

    @QtCore.Slot(str)
    def _export_failed(self, detail: str) -> None:
        QtWidgets.QMessageBox.warning(self, "내보내기 실패", detail)
        self.status.emit(f"내보내기 실패 · {detail}")

    @QtCore.Slot()
    def _export_finished(self) -> None:
        self.export_progress.hide()
        self.export_button.setEnabled(True)
        self.export_button.setText("내보내기")
        worker = self._export_worker
        self._export_worker = None
        if worker is not None:
            worker.deleteLater()

    def _run(self) -> None:
        name = self.macro_combo.currentText()
        if name:
            self.run_macro.emit(name)
