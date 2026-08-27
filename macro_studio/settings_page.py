from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .remote import RemoteController
from .shortcuts import STUDIO_SHORTCUT_SPECS
from .theme import COLORS
from .validation import ProjectValidator
from .widgets import Card, PageHeader, primary_button


class SettingsPage(QtWidgets.QWidget):
    status = QtCore.Signal(str)
    shortcut_config_changed = QtCore.Signal(dict)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.validator = ProjectValidator(repository)
        self.remote = RemoteController(repository.root)
        self.shortcut_edits: dict[str, QtWidgets.QKeySequenceEdit] = {}
        self._component_buttons: list[QtWidgets.QPushButton] = []
        self._component_process = QtCore.QProcess(self)
        self._component_process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._component_process.readyReadStandardOutput.connect(self._read_component_output)
        self._component_process.finished.connect(self._component_install_finished)
        self._component_process.errorOccurred.connect(self._component_process_error)
        self._component_label = ""
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        header_row = QtWidgets.QHBoxLayout()
        header_row.addWidget(PageHeader("설정", "기능별 단축키, 프로젝트 진단, 실행 환경을 관리합니다."), 1)
        refresh_btn = QtWidgets.QPushButton("새로 점검")
        refresh_btn.clicked.connect(self.refresh)
        header_row.addWidget(refresh_btn)
        root.addLayout(header_row)

        self.tabs = QtWidgets.QTabWidget()
        self.shortcuts_tab_index = self.tabs.addTab(self._build_shortcuts_tab(), "기능별 단축키")
        self.tabs.addTab(self._build_issues_tab(), "프로젝트 진단")
        self.tabs.addTab(self._build_environment_tab(), "실행 환경")
        self.tabs.addTab(self._build_components_tab(), "구성요소 설치")
        self.remote_tab_index = self.tabs.addTab(self._build_remote_tab(), "모바일 원격")
        self.tabs.addTab(self._build_storage_tab(), "저장 공간")
        root.addWidget(self.tabs, 1)
        self._remote_timer = QtCore.QTimer(self)
        self._remote_timer.setInterval(1500)
        self._remote_timer.timeout.connect(self._refresh_remote_status)
        self._remote_timer.start()

    def _build_shortcuts_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        notice = QtWidgets.QLabel(
            "Quick Slots가 아닌 Studio 기능을 임의의 키로 실행하는 설정입니다. "
            "중복된 키는 저장 전에 자동 검사합니다."
        )
        notice.setObjectName("Muted")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = Card()
        grid = QtWidgets.QGridLayout(body)
        grid.setContentsMargins(18, 18, 18, 18)
        left = QtWidgets.QFormLayout()
        right = QtWidgets.QFormLayout()
        left.setVerticalSpacing(9)
        right.setVerticalSpacing(9)
        grid.addLayout(left, 0, 0)
        grid.addLayout(right, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        split = (len(STUDIO_SHORTCUT_SPECS) + 1) // 2
        for index, (action_id, label, _default) in enumerate(STUDIO_SHORTCUT_SPECS):
            edit = QtWidgets.QKeySequenceEdit()
            edit.setClearButtonEnabled(True)
            clear = QtWidgets.QPushButton("지우기")
            clear.setFixedWidth(64)
            clear.clicked.connect(edit.clear)
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(edit, 1)
            row.addWidget(clear)
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            (left if index < split else right).addRow(label, holder)
            self.shortcut_edits[action_id] = edit
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        actions = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton("기본값 복원")
        reset.clicked.connect(self._reset_shortcuts)
        save = primary_button("단축키 저장 및 즉시 적용")
        save.clicked.connect(self._save_shortcuts)
        actions.addStretch(1)
        actions.addWidget(reset)
        actions.addWidget(save)
        layout.addLayout(actions)
        return page

    def open_shortcut_settings(self) -> None:
        self.tabs.setCurrentIndex(self.shortcuts_tab_index)

    def _load_shortcuts(self) -> None:
        saved = self.repository.load_hotkey_actions()
        for action_id, _label, default in STUDIO_SHORTCUT_SPECS:
            self.shortcut_edits[action_id].setKeySequence(QtGui.QKeySequence(saved.get(action_id, default)))

    def _reset_shortcuts(self) -> None:
        for action_id, _label, default in STUDIO_SHORTCUT_SPECS:
            self.shortcut_edits[action_id].setKeySequence(QtGui.QKeySequence(default))

    def _save_shortcuts(self) -> None:
        payload = {
            action_id: self.shortcut_edits[action_id].keySequence().toString(QtGui.QKeySequence.PortableText)
            for action_id, _label, _default in STUDIO_SHORTCUT_SPECS
        }
        labels = {action_id: label for action_id, label, _default in STUDIO_SHORTCUT_SPECS}
        seen: dict[str, str] = {}
        duplicates: list[str] = []
        for action_id, sequence in payload.items():
            folded = sequence.casefold().strip()
            if not folded:
                continue
            if folded in seen:
                duplicates.append(f"{labels[seen[folded]]} ↔ {labels[action_id]}: {sequence}")
            else:
                seen[folded] = action_id
        if duplicates:
            QtWidgets.QMessageBox.warning(self, "단축키 중복", "같은 단축키를 여러 기능에 지정할 수 없습니다.\n\n" + "\n".join(duplicates))
            return
        self.repository.save_hotkey_actions(payload)
        self.shortcut_config_changed.emit(payload)
        self.status.emit("기능별 단축키를 저장하고 즉시 적용했습니다.")

    def _build_issues_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.issue_summary = QtWidgets.QLabel()
        self.issue_table = QtWidgets.QTableWidget(0, 5)
        self.issue_table.setHorizontalHeaderLabels(["등급", "문제", "매크로", "단계", "설명"])
        self.issue_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.issue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.issue_summary)
        layout.addWidget(self.issue_table, 1)
        return page

    def _build_environment_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        self.environment_table = QtWidgets.QTableWidget(0, 3)
        self.environment_table.setHorizontalHeaderLabels(["항목", "상태", "경로/안내"])
        self.environment_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.environment_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.environment_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.environment_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.environment_table)
        return page

    def _build_components_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        notice = QtWidgets.QLabel(
            "필요한 기능만 설치할 수 있습니다. Python 구성요소는 MacroRelay 전용 runtime_packages에 설치되어 "
            "다른 프로그램의 Python 환경을 변경하지 않습니다."
        )
        notice.setObjectName("Muted")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.component_table = QtWidgets.QTableWidget(0, 4)
        self.component_table.setHorizontalHeaderLabels(["기능", "상태", "포함 구성요소", "설치·연결"])
        self.component_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.component_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.component_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.component_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.component_table.verticalHeader().setVisible(False)
        layout.addWidget(self.component_table, 1)
        self.install_output = QtWidgets.QPlainTextEdit()
        self.install_output.setReadOnly(True)
        self.install_output.setMaximumBlockCount(500)
        self.install_output.setMaximumHeight(150)
        self.install_output.setPlaceholderText("설치 진행 내용이 여기에 표시됩니다.")
        layout.addWidget(self.install_output)
        return page

    def _build_storage_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        notice = QtWidgets.QLabel(
            "브라우저 프로필에는 로그인 쿠키가 포함될 수 있습니다. 새 버전은 자동 삭제하지 않으며, 정리 후보만 표시합니다."
        )
        notice.setObjectName("Muted")
        notice.setWordWrap(True)
        self.storage_table = QtWidgets.QTableWidget(0, 3)
        self.storage_table.setHorizontalHeaderLabels(["항목", "크기", "권장 처리"])
        self.storage_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.storage_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.storage_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.storage_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(notice)
        layout.addWidget(self.storage_table, 1)
        return page

    def _build_remote_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setSpacing(12)
        intro = QtWidgets.QLabel(
            "휴대폰에서 매크로 상태를 확인하고 실행·정지하며 완료 알림을 받을 수 있습니다. "
            "PC 에이전트는 외부로만 연결하며, 대기 중에는 저전력 장기 폴링으로 동작합니다."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        config_card = Card()
        form = QtWidgets.QFormLayout(config_card)
        form.setContentsMargins(20, 18, 20, 18)
        self.remote_enabled = QtWidgets.QCheckBox("Studio 실행 중 항상 모바일 연결 유지")
        self.remote_relay_url = QtWidgets.QLineEdit()
        self.remote_relay_url.setPlaceholderText("https://relay.example.com")
        self.remote_device_name = QtWidgets.QLineEdit()
        self.remote_allow_run = QtWidgets.QCheckBox("휴대폰에서 실행 허용")
        self.remote_allow_stop = QtWidgets.QCheckBox("휴대폰에서 정지 허용")
        self.remote_macro_list = QtWidgets.QListWidget()
        self.remote_macro_list.setMaximumHeight(145)
        self.remote_macro_list.setAlternatingRowColors(True)
        permissions = QtWidgets.QHBoxLayout()
        permissions.addWidget(self.remote_allow_run)
        permissions.addWidget(self.remote_allow_stop)
        permissions.addStretch(1)
        permissions_holder = QtWidgets.QWidget()
        permissions_holder.setLayout(permissions)
        form.addRow("원격 제어", self.remote_enabled)
        form.addRow("중계 서버 주소", self.remote_relay_url)
        form.addRow("PC 표시 이름", self.remote_device_name)
        form.addRow("원격 권한", permissions_holder)
        form.addRow("휴대폰 공개 매크로", self.remote_macro_list)
        outer.addWidget(config_card)

        status_card = Card()
        grid = QtWidgets.QGridLayout(status_card)
        grid.setContentsMargins(20, 18, 20, 18)
        self.remote_state = QtWidgets.QLabel("중지됨")
        self.remote_state.setStyleSheet(f"font-weight: 700; color: {COLORS['warning']};")
        self.remote_pair_code = QtWidgets.QLabel("—")
        self.remote_pair_code.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.remote_pair_code.setStyleSheet("font-size: 26px; font-weight: 800; letter-spacing: 5px;")
        self.remote_mobile_url = QtWidgets.QLineEdit()
        self.remote_mobile_url.setReadOnly(True)
        grid.addWidget(QtWidgets.QLabel("에이전트 상태"), 0, 0)
        grid.addWidget(self.remote_state, 0, 1)
        grid.addWidget(QtWidgets.QLabel("휴대폰 연결 코드"), 1, 0)
        grid.addWidget(self.remote_pair_code, 1, 1)
        grid.addWidget(QtWidgets.QLabel("같은 Wi-Fi 접속 주소"), 2, 0)
        grid.addWidget(self.remote_mobile_url, 2, 1)
        grid.setColumnStretch(1, 1)
        outer.addWidget(status_card)

        note = QtWidgets.QLabel(
            "같은 Wi-Fi에서는 원격 제어를 켜고 저장하면 로컬 서버와 에이전트가 자동으로 유지됩니다. "
            "외부 인터넷에서 사용하려면 HTTPS가 적용된 중계 서버 주소를 입력해야 합니다. 비밀 키는 화면이나 GitHub에 노출되지 않습니다."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        outer.addWidget(note)
        actions = QtWidgets.QHBoxLayout()
        self.remote_local_button = QtWidgets.QPushButton("로컬 서버 시작")
        self.remote_local_button.clicked.connect(self._toggle_local_relay)
        self.remote_agent_button = QtWidgets.QPushButton("에이전트 시작")
        self.remote_agent_button.clicked.connect(self._toggle_remote_agent)
        open_mobile = QtWidgets.QPushButton("휴대폰 화면 열기")
        open_mobile.clicked.connect(self.remote.open_mobile)
        copy_code = QtWidgets.QPushButton("연결 코드 복사")
        copy_code.clicked.connect(self._copy_remote_code)
        save = primary_button("설정 저장 및 적용")
        save.clicked.connect(self._save_remote_settings)
        actions.addWidget(self.remote_local_button)
        actions.addWidget(self.remote_agent_button)
        actions.addWidget(open_mobile)
        actions.addWidget(copy_code)
        actions.addStretch(1)
        actions.addWidget(save)
        outer.addLayout(actions)
        outer.addStretch(1)
        return page

    def _load_remote_settings(self) -> None:
        config = self.remote.load()
        self.remote_enabled.setChecked(bool(config.get("enabled")))
        self.remote_relay_url.setText(str(config.get("relay_url") or "http://127.0.0.1:8765"))
        self.remote_device_name.setText(str(config.get("device_name") or "MacroRelay PC"))
        self.remote_allow_run.setChecked(bool(config.get("allow_remote_run", True)))
        self.remote_allow_stop.setChecked(bool(config.get("allow_remote_stop", True)))
        allowed = {str(name) for name in config.get("allowed_macros", []) if str(name)}
        self.remote_macro_list.clear()
        for summary in self.repository.list_macros():
            item = QtWidgets.QListWidgetItem(summary.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if summary.name in allowed else QtCore.Qt.Unchecked)
            self.remote_macro_list.addItem(item)
        if not allowed:
            self.remote_macro_list.setToolTip("선택된 항목이 없으므로 모든 매크로가 휴대폰에 표시됩니다.")
        else:
            self.remote_macro_list.setToolTip("체크한 매크로만 휴대폰에 표시되고 실행됩니다.")
        self.remote_mobile_url.setText(self.remote.mobile_url())

    def _save_remote_settings(self) -> None:
        relay_url = self.remote_relay_url.text().strip().rstrip("/")
        if not relay_url.startswith(("http://", "https://")):
            QtWidgets.QMessageBox.warning(self, "중계 서버 주소", "주소는 http:// 또는 https://로 시작해야 합니다.")
            return
        allowed = [
            self.remote_macro_list.item(index).text()
            for index in range(self.remote_macro_list.count())
            if self.remote_macro_list.item(index).checkState() == QtCore.Qt.Checked
        ]
        config = self.remote.save({
            "enabled": self.remote_enabled.isChecked(),
            "relay_url": relay_url,
            "device_name": self.remote_device_name.text().strip() or "MacroRelay PC",
            "allow_remote_run": self.remote_allow_run.isChecked(),
            "allow_remote_stop": self.remote_allow_stop.isChecked(),
            "allowed_macros": allowed,
        })
        if config.get("enabled"):
            self.remote.ensure_running()
        else:
            self.remote.stop_agent()
            if self.remote.uses_local_relay(config):
                self.remote.stop_local_relay()
        self.remote_mobile_url.setText(self.remote.mobile_url())
        self._refresh_remote_status()
        self.status.emit("모바일 원격 설정을 저장하고 즉시 적용했습니다.")

    def _toggle_local_relay(self) -> None:
        running = bool(self.remote.status().get("relay_running"))
        ok = self.remote.stop_local_relay() if running else self.remote.start_local_relay()
        self.status.emit("로컬 중계 서버를 중지했습니다." if running and ok else "로컬 중계 서버를 시작했습니다." if ok else "로컬 중계 서버를 시작하지 못했습니다.")
        self._refresh_remote_status()

    def _toggle_remote_agent(self) -> None:
        running = bool(self.remote.status().get("agent_running"))
        if running:
            ok = self.remote.stop_agent()
        else:
            self.remote.save({"enabled": True})
            self.remote_enabled.setChecked(True)
            ok = self.remote.start_agent()
        self.status.emit("원격 에이전트를 중지했습니다." if running and ok else "원격 에이전트를 시작했습니다." if ok else "원격 에이전트를 시작하지 못했습니다.")
        self._refresh_remote_status()

    def _copy_remote_code(self) -> None:
        code = self.remote_pair_code.text().strip()
        if code and code != "—":
            QtWidgets.QApplication.clipboard().setText(code)
            self.status.emit("휴대폰 연결 코드를 복사했습니다.")

    def _refresh_remote_status(self) -> None:
        status = self.remote.status()
        running = bool(status.get("agent_running"))
        connected = bool(status.get("connected")) and time.time() - float(status.get("updated") or 0) < 15
        if connected:
            text, color = "중계 서버 연결됨", COLORS["success"]
        elif running:
            text, color = "연결 시도 중", COLORS["warning"]
        else:
            text, color = "중지됨", COLORS["warning"]
        self.remote_state.setText(text)
        self.remote_state.setStyleSheet(f"font-weight: 700; color: {color};")
        self.remote_pair_code.setText(str(status.get("pairing_code") or "—"))
        relay_running = bool(status.get("relay_running"))
        self.remote_local_button.setText("로컬 서버 중지" if relay_running else "로컬 서버 시작")
        self.remote_agent_button.setText("에이전트 중지" if running else "에이전트 시작")
        self.remote_mobile_url.setText(self.remote.mobile_url())

    def refresh(self) -> None:
        self._load_shortcuts()
        self._load_remote_settings()
        self._refresh_remote_status()
        self._refresh_issues()
        self._refresh_environment()
        self._refresh_components()
        self._refresh_storage()
        self.status.emit("프로젝트 점검을 완료했습니다.")

    def _refresh_issues(self) -> None:
        issues = self.validator.validate()
        errors = sum(issue.severity == "error" for issue in issues)
        warnings = len(issues) - errors
        self.issue_summary.setText(f"오류 {errors}개 · 경고 {warnings}개")
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            values = ["오류" if issue.severity == "error" else "경고", issue.title, issue.macro, str(issue.step or "-"), issue.detail]
            color = COLORS["danger"] if issue.severity == "error" else COLORS["warning"]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(QtGui.QColor(color))
                self.issue_table.setItem(row, column, item)

    def _refresh_environment(self) -> None:
        ahk = self.repository._read_text_path("ahk_path.txt")
        compiler = self.repository._read_text_path("ahk2exe_path.txt")
        tesseract = self._configured_tesseract()
        modules = {
            "PySide6": "기본 UI",
            "playwright": "브라우저 자동화",
            "PIL": "이미지 처리",
            "pytesseract": "OCR",
            "rapidocr": "한국어 PP-OCRv5 엔진(권장)",
            "rapidocr_onnxruntime": "구형 OCR 엔진(호환)",
            "onnxruntime": "OCR ONNX 추론(선택)",
            "openpyxl": "Excel 파일",
            "cv2": "OpenCV 이미지 서치(선택)",
            "win32com": "실행 중 Excel 제어(선택)",
        }
        rows: list[tuple[str, bool, str]] = [
            ("AutoHotkey", bool(ahk and ahk.exists()), str(ahk or "경로 미설정")),
            ("Ahk2Exe", bool(compiler and compiler.exists()), str(compiler or "경로 미설정")),
            ("Tesseract", bool(tesseract and tesseract.exists()), str(tesseract or "경로 미설정")),
        ]
        runner_status = self.repository.quick_slots_runner().status()
        rows.insert(
            1,
            (
                "MacroRelay Runner",
                runner_status.running,
                f"Quick Slots {runner_status.active_slots}개 · "
                + (f"PID {runner_status.pid}" if runner_status.running else "현재 중지됨"),
            ),
        )
        rows.extend((name, importlib.util.find_spec(name) is not None, description) for name, description in modules.items())
        self.environment_table.setRowCount(len(rows))
        for row, (name, ok, detail) in enumerate(rows):
            values = [name, "정상" if ok else "미설치", detail]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(QtGui.QColor(COLORS["success"] if ok else COLORS["warning"]))
                self.environment_table.setItem(row, column, item)

    def _refresh_components(self) -> None:
        self._component_buttons.clear()
        ahk = self.repository._read_text_path("ahk_path.txt")
        compiler = self.repository._read_text_path("ahk2exe_path.txt")
        tesseract = self._configured_tesseract()
        playwright_root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        chromium_ready = playwright_root.exists() and any(playwright_root.glob("chromium-*"))
        definitions = [
            (
                "고속 OCR 엔진",
                self._modules_ready(("rapidocr", "onnxruntime", "PIL", "pytesseract")),
                "한국어 PP-OCRv5 · ONNX Runtime · 고정밀 Tesseract",
                "설치/업데이트",
                lambda: self._install_python_packages(
                    "고속 OCR 엔진",
                    ("rapidocr>=3.9,<4", "onnxruntime>=1.17,<2", "pillow>=10,<13", "pytesseract>=0.3.13,<1"),
                ),
            ),
            (
                "OpenCV 이미지 서치",
                self._opencv_ready(),
                "cv2 이미지 디코딩까지 실제 실행 검사 · mss · numpy",
                "설치/업데이트",
                lambda: self._install_python_packages(
                    "OpenCV 이미지 서치",
                    ("opencv-python-headless>=4.9,<5", "mss>=9,<11"),
                ),
            ),
            (
                "Windows 자동화",
                self._modules_ready(("win32com", "pywinauto", "uiautomation")),
                "pywin32 · pywinauto · uiautomation",
                "설치/업데이트",
                lambda: self._install_python_packages(
                    "Windows 자동화",
                    ("pywin32>=306", "pywinauto>=0.6.8", "uiautomation>=2.0"),
                ),
            ),
            (
                "브라우저 자동화 엔진",
                chromium_ready,
                "Playwright Chromium 브라우저",
                "브라우저 다운로드",
                self._install_playwright_browser,
            ),
            (
                "Tesseract OCR",
                bool(tesseract and tesseract.exists()),
                str(tesseract or "tesseract.exe 경로 미설정"),
                "실행 파일 연결",
                self._connect_tesseract,
            ),
            (
                "AutoHotkey",
                bool(ahk and ahk.exists()),
                str(ahk or "AutoHotkey.exe 경로 미설정"),
                "실행 파일 연결",
                lambda: self._connect_executable("AutoHotkey 연결", "ahk_path.txt", "AutoHotkey (*.exe)"),
            ),
            (
                "Ahk2Exe",
                bool(compiler and compiler.exists()),
                str(compiler or "Ahk2Exe.exe 경로 미설정"),
                "실행 파일 연결",
                lambda: self._connect_executable("Ahk2Exe 연결", "ahk2exe_path.txt", "Ahk2Exe (*.exe)"),
            ),
        ]
        self.component_table.setRowCount(len(definitions))
        busy = self._component_process.state() != QtCore.QProcess.NotRunning
        for row, (label, ready, detail, button_text, callback) in enumerate(definitions):
            self.component_table.setItem(row, 0, QtWidgets.QTableWidgetItem(label))
            status_item = QtWidgets.QTableWidgetItem("사용 가능" if ready else "미설치/미연결")
            status_item.setForeground(QtGui.QColor(COLORS["success"] if ready else COLORS["warning"]))
            self.component_table.setItem(row, 1, status_item)
            self.component_table.setItem(row, 2, QtWidgets.QTableWidgetItem(detail))
            button = QtWidgets.QPushButton(button_text)
            button.clicked.connect(callback)
            button.setEnabled(not busy)
            self.component_table.setCellWidget(row, 3, button)
            self._component_buttons.append(button)
        self.component_table.resizeRowsToContents()

    @staticmethod
    def _modules_ready(names: tuple[str, ...]) -> bool:
        return all(importlib.util.find_spec(name) is not None for name in names)

    def _opencv_ready(self) -> bool:
        try:
            self.repository._ensure_opencv_runtime()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False
        return True

    def _runtime_packages_dir(self) -> Path:
        # The Studio launcher and generated AHK files already add this exact
        # directory to PYTHONPATH. Keeping one active ABI here also lets newly
        # installed OCR packages become available without restarting Studio.
        target = self.repository.root / "runtime_packages"
        if target.is_dir() and str(target) not in sys.path:
            sys.path.insert(0, str(target))
        return target

    def _install_python_packages(self, label: str, packages: tuple[str, ...]) -> None:
        target = self._runtime_packages_dir()
        target.mkdir(parents=True, exist_ok=True)
        self._start_component_process(
            label,
            ["-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--target", str(target), *packages],
        )

    def _install_playwright_browser(self) -> None:
        if importlib.util.find_spec("playwright") is None:
            QtWidgets.QMessageBox.warning(self, "Playwright 미설치", "먼저 기본 Playwright Python 패키지를 설치해야 합니다.")
            return
        self._start_component_process("Playwright Chromium", ["-m", "playwright", "install", "chromium"])

    def _start_component_process(self, label: str, arguments: list[str]) -> None:
        if self._component_process.state() != QtCore.QProcess.NotRunning:
            self.status.emit("다른 구성요소를 설치하고 있습니다.")
            return
        self._component_label = label
        self.install_output.clear()
        self.install_output.appendPlainText(f"[{label}] 설치를 시작합니다…")
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        paths = [str(self._runtime_packages_dir()), str(self.repository.root / ".venv" / "Lib" / "site-packages")]
        existing = environment.value("PYTHONPATH")
        if existing:
            paths.append(existing)
        environment.insert("PYTHONPATH", os.pathsep.join(paths))
        environment.insert("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        self._component_process.setProcessEnvironment(environment)
        self._component_process.setWorkingDirectory(str(self.repository.root))
        self._component_process.setProgram(sys.executable)
        self._component_process.setArguments(arguments)
        try:
            (self.repository.root / ".component-installing").write_text(label, encoding="utf-8")
        except OSError:
            pass
        for button in self._component_buttons:
            button.setEnabled(False)
        self._component_process.start()
        self.status.emit(f"{label} 설치를 시작했습니다.")

    def _clear_component_install_marker(self) -> None:
        try:
            (self.repository.root / ".component-installing").unlink(missing_ok=True)
        except OSError:
            pass

    def _component_process_error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error == QtCore.QProcess.FailedToStart:
            self._clear_component_install_marker()
            self.install_output.appendPlainText("\n[실행 실패] Python 설치 프로세스를 시작하지 못했습니다.")
            self.status.emit("구성요소 설치 프로세스를 시작하지 못했습니다.")
            self._refresh_components()

    def _read_component_output(self) -> None:
        text = bytes(self._component_process.readAllStandardOutput()).decode("utf-8", errors="replace").rstrip()
        if text:
            self.install_output.appendPlainText(text)

    def _component_install_finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self._clear_component_install_marker()
        label = self._component_label or "구성요소"
        if exit_code == 0:
            self.install_output.appendPlainText(f"\n[{label}] 설치 완료")
            self.status.emit(f"{label} 설치를 완료했습니다. 새로 점검했습니다.")
        else:
            self.install_output.appendPlainText(f"\n[{label}] 설치 실패 · 종료 코드 {exit_code}")
            self.status.emit(f"{label} 설치에 실패했습니다. 출력 내용을 확인하세요.")
        self._refresh_environment()
        self._refresh_components()

    def _configured_tesseract(self) -> Path | None:
        configured = self.repository._read_text_path("tesseract_path.txt")
        if configured and configured.exists():
            return configured
        for candidate in (
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ):
            if candidate.exists():
                return candidate
        return None

    def _connect_tesseract(self) -> None:
        self._connect_executable("Tesseract OCR 연결", "tesseract_path.txt", "Tesseract (tesseract.exe)")

    def _connect_executable(self, title: str, filename: str, file_filter: str) -> None:
        current = self.repository._read_text_path(filename)
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, str(current or self.repository.root), file_filter)
        if not selected:
            return
        path = Path(selected)
        if not path.exists() or path.suffix.lower() != ".exe":
            QtWidgets.QMessageBox.warning(self, title, "올바른 실행 파일(.exe)을 선택하세요.")
            return
        (self.repository.root / filename).write_text(str(path), encoding="utf-8")
        self._refresh_environment()
        self._refresh_components()
        self.status.emit(f"{path.name} 경로를 연결했습니다.")

    def _refresh_storage(self) -> None:
        candidates = [
            (self.repository.root / "Codex.dmg", "프로젝트와 무관하면 보관 또는 삭제"),
            (self.repository.root / "chrome_profile", "로그인이 필요 없으면 별도 데이터 폴더로 이동"),
            (self.repository.root / "whale_profile", "로그인이 필요 없으면 별도 데이터 폴더로 이동"),
            (self.repository.root / "edge_profile", "사용하지 않으면 별도 데이터 폴더로 이동"),
            (self.repository.root / ".venv", "필수 실행 환경 — 유지"),
            (self.repository.root / "assets" / ".trash", "복구가 끝난 오래된 항목만 삭제"),
        ]
        existing = [(path, recommendation) for path, recommendation in candidates if path.exists()]
        self.storage_table.setRowCount(len(existing))
        for row, (path, recommendation) in enumerate(existing):
            size = self._size(path)
            values = [str(path.relative_to(self.repository.root)), self._human_size(size), recommendation]
            for column, value in enumerate(values):
                self.storage_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    @staticmethod
    def _size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _human_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"
