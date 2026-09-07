"""Optional PySide6 dialog. Emits unsaved macro data; never installs or runs it."""
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

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
        self.root_layout = QtWidgets.QHBoxLayout(self)
        self.root_layout.setContentsMargins(12, 12, 12, 12)
        self.root_layout.setSpacing(12)

        self.main_panel = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(self.main_panel)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(8)
        self.root_layout.addWidget(self.main_panel, stretch=1)
        layout = self.main_layout

        info = QtWidgets.QLabel("녹화 목적 입력 → Antigravity AI 플랜 동기화 → 자동 연결된 초안 확인")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.purpose = QtWidgets.QPlainTextEdit()
        self.purpose.setPlaceholderText("예: 빨간 알림이 있는 이벤트의 보상을 모두 받고 창을 닫아줘.")
        self.purpose.setMaximumHeight(95)
        layout.addWidget(self.purpose)

        # 1. Primary One-Click Sync Button
        self.btn_sync_desktop = QtWidgets.QPushButton("⚡ Antigravity 최신 플랜 즉시 동기화 (바탕화면)")
        self.btn_sync_desktop.setStyleSheet("background: #2B6CB0; color: white; font-weight: bold; padding: 8px; border-radius: 4px; font-size: 13px;")
        self.btn_sync_desktop.setToolTip("바탕화면에 생성된 최신 plan.json을 파일 탐색기 없이 원클릭으로 즉시 동기화하여 초안 테이블에 적용합니다.")
        self.btn_sync_desktop.clicked.connect(self.sync_desktop_plan)
        layout.addWidget(self.btn_sync_desktop)

        # 2. Package & Import row
        sub_row = QtWidgets.QHBoxLayout()
        btn_export = QtWidgets.QPushButton("📦 1. AI 패키지 저장 (Antigravity용)")
        btn_export.clicked.connect(self.export)
        btn_import = QtWidgets.QPushButton("📂 2. plan.json 직접 선택")
        btn_import.clicked.connect(self.import_plan)
        sub_row.addWidget(btn_export)
        sub_row.addWidget(btn_import)
        layout.addLayout(sub_row)

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
            try:
                QtGui.QGuiApplication.clipboard().setText(str(destination))
            except Exception:
                pass
            self.status.setText(f"✔ AI 패키지 저장 완료! (폴더 경로가 클립보드에 자동 복사됨)\n{destination}\n"
                                "Antigravity에 요청하시면 플랜이 생성되며, 생성 후 [⚡ Antigravity 최신 플랜 즉시 동기화]를 누르세요.")
        except Exception as exc:
            self.status.setText(f"내보내기 실패: {exc}")

    def sync_desktop_plan(self):
        """Auto-sync plan.json from Desktop without opening file picker."""
        desktop_plan = Path.home() / "Desktop" / "plan.json"
        if not desktop_plan.is_file():
            self.status.setText("⚠ 바탕화면에 plan.json이 없습니다. Antigravity에 요청하여 플랜을 먼저 생성하세요.")
            return

        try:
            plan = load_plan(desktop_plan)
            plan_rec_id = plan.get("recording_id")

            private = None
            if self.local_recording and Path(self.local_recording).is_file():
                try:
                    candidate_private = load_private_recording(self.local_recording)
                    if candidate_private.get("recording_id") == plan_rec_id:
                        private = candidate_private
                except Exception:
                    pass

            if private is None and hasattr(self, "_private") and self._private and self._private.get("recording_id") == plan_rec_id:
                private = self._private

            if private is None:
                desktop = Path.home() / "Desktop"
                for folder in sorted(desktop.glob("recording*"), reverse=True):
                    priv_file = folder / "private-recording.json"
                    if priv_file.is_file():
                        try:
                            candidate_private = load_private_recording(priv_file)
                            if candidate_private.get("recording_id") == plan_rec_id:
                                private = candidate_private
                                self.local_recording = priv_file
                                if hasattr(self, "_private"):
                                    self._private = private
                                break
                        except Exception:
                            continue

            if private is None:
                self.status.setText(f"⚠ plan.json의 녹화 ID({plan_rec_id})와 일치하는 녹화 파일을 찾지 못했습니다. [2. plan.json 직접 선택]을 이용하세요.")
                return

            self.draft = compile_plan(plan, private, self.repository.asset_path)
            self.table.setRowCount(len(self.draft["steps"]))
            for row, step in enumerate(self.draft["steps"]):
                for col, text in enumerate([row+1, step["label"], step.get("on_success", "종료"), step.get("on_fail", "종료")]):
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(text)))
            self.status.setText(f"✔ Antigravity 플랜 자동 동기화 완료! ({len(self.draft['steps'])}개 단계 검증 완료)")
            self.accept_draft.setEnabled(True)
        except Exception as exc:
            self.status.setText(f"동기화 실패: {exc}")

    def import_plan(self):
        self.draft = None
        self.accept_draft.setEnabled(False)
        self.table.setRowCount(0)
        if self.local_recording is None:
            filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "PC에 보관한 private-recording.json", "", "JSON (*.json)")
            if not filename:
                return
            self.local_recording = Path(filename)
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "plan.json 선택", "", "JSON (*.json)")
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

