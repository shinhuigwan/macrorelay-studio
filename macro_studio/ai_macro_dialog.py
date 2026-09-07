"""Optional PySide6 dialog. Emits unsaved macro data; never installs or runs it."""
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .ai_macro_plan import compile_plan, export_recording, load_plan, load_private_recording


class AiMacroDialog(QtWidgets.QDialog):
    macro_ready = QtCore.Signal(dict)

    def __init__(self, repository, recorded_steps, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.recorded_steps = recorded_steps
        self.local_recording = None
        self.draft = None
        self.setWindowTitle("녹화로 자동 매크로 만들기 · 초안")
        self.resize(780, 600)
        self.setModal(False)
        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel("녹화 목적 입력 → 패키지를 GPT에 전달 → plan.json 가져오기 → 자동 연결된 초안 확인")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.purpose = QtWidgets.QPlainTextEdit()
        self.purpose.setPlaceholderText("예: 빨간 알림이 있는 이벤트의 보상을 모두 받고 창을 닫아줘.")
        self.purpose.setMaximumHeight(95)
        layout.addWidget(self.purpose)
        for title, handler in [("1. GPT 전달 패키지 저장", self.export),
                               ("2. 받은 plan.json 가져오기", self.import_plan)]:
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(handler)
            layout.addWidget(button)
        self.status = QtWidgets.QLabel("동작과 PNG가 포함됩니다. 전송 전 화면에 민감한 정보가 없는지 확인하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["번호", "동작", "성공 →", "실패 →"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.accept_draft = QtWidgets.QPushButton("자동 연결된 초안을 빌더로 보내기")
        self.accept_draft.setEnabled(False)
        self.accept_draft.clicked.connect(self.emit_draft)
        layout.addWidget(self.accept_draft)

    def export(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "패키지 저장 위치")
        if not directory:
            return
        try:
            destination = Path(directory) / datetime.now().strftime("recording-%Y%m%d-%H%M%S-%f")
            package = export_recording(self.recorded_steps, self.purpose.toPlainText(), destination,
                                       self.repository.asset_path)
            self.local_recording = destination / "private-recording.json"
            self.draft = None
            self.accept_draft.setEnabled(False)
            self.table.setRowCount(0)
            self.status.setText(f"GPT에는 이 ZIP만 첨부하세요:\n{package}\n"
                                "private-recording.json은 원본 동작 연결용이므로 PC에 보관하세요.")
        except Exception as exc:
            self.status.setText(f"내보내기 실패: {exc}")

    def import_plan(self):
        self.draft = None
        self.accept_draft.setEnabled(False)
        self.table.setRowCount(0)
        if self.local_recording is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "PC에 보관한 private-recording.json", "", "JSON (*.json)")
            if not filename:
                return
            self.local_recording = Path(filename)
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "GPT가 만든 plan.json", "", "JSON (*.json)")
        if not filename:
            return
        try:
            private = load_private_recording(self.local_recording)
            plan = load_plan(filename)
            self.draft = compile_plan(plan, private, self.repository.asset_path)
            self.table.setRowCount(len(self.draft["steps"]))
            for row, step in enumerate(self.draft["steps"]):
                for col, text in enumerate([row+1, step["label"], step.get("on_success", "종료"), step.get("on_fail", "종료")]):
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(text)))
            self.status.setText("이미지 참조와 노드 연결 검증 완료. 실제 검색·클릭 성공 여부는 Studio에서 별도로 테스트하세요.")
            self.accept_draft.setEnabled(True)
        except Exception as exc:
            self.status.setText(f"계획을 가져올 수 없습니다: {exc}")

    def emit_draft(self):
        if self.draft is not None:
            self.accept_draft.setEnabled(False)
            self.status.setText("매크로 저장 및 빌더 로드 중...")
            self.macro_ready.emit(self.draft)

    def on_save_failed(self, message: str) -> None:
        self.status.setText(f"저장 실패: {message}")
        if self.draft is not None:
            self.accept_draft.setEnabled(True)

    def on_save_success(self) -> None:
        self.accept_draft.setEnabled(False)
        self.close()

