from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .action_editor import ACTION_LABELS, ActionEditor, action_template
from .automation import (
    AutomationAnalyzer,
    AutomationOverlay,
    DiagnosticsDialog,
    QuickActionWizard,
    RecordingReviewDialog,
    SmartRecordingController,
)
from .log_dialog import MacroLogDialog
from .inactive_click_lab import HandlePointPicker, InactiveClickLabDialog
from .image_editor import ImageEditorDialog
from .node_editor import NodeCanvas
from .repository import MacroRepository
from .theme import COLORS
from .trigger_dialog import EventTriggerDialog
from .macro_test_cases import MacroTestCaseDialog, run_test_cases
from .validation import ProjectValidator
from .widgets import Card, PageHeader, WheelSafeSpinBox, danger_button, primary_button


ACTION_TEMPLATES: dict[str, dict[str, Any]] = {action: action_template(action) for action in ACTION_LABELS}


def _is_unconfigured_template_step(step: dict[str, Any]) -> bool:
    """Recognize the untouched starter node created with a new macro."""
    action = str(step.get("action") or "")
    template = ACTION_TEMPLATES.get(action)
    if not template or step.get("label") or step.get("_automation"):
        return False
    ignored = {"on_success", "on_fail", "edge_conditions"}
    normalized = {key: value for key, value in step.items() if key not in ignored}
    return normalized == template


class MacroDialog(QtWidgets.QDialog):
    def __init__(self, title: str, default_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(default_name)
        self.description_edit = QtWidgets.QLineEdit()
        form.addRow("이름", self.name_edit)
        form.addRow("설명", self.description_edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class EdgeConditionDialog(QtWidgets.QDialog):
    def __init__(self, step_count: int, kind: str, rule: dict[str, Any] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("조건 분기 편집")
        self.setMinimumWidth(460)
        rule = rule or {}
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(f"{'성공' if kind == 'success' else '실패'} 연결을 통과할 때 조건을 검사해 다른 노드로 분기합니다.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        form = QtWidgets.QFormLayout()
        self.label_edit = QtWidgets.QLineEdit(str(rule.get("label") or ""))
        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.addItem("이 연결의 실행 횟수", "edge_count")
        self.source_combo.addItem("사용자 변수", "variable")
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(str(rule.get("source") or "edge_count"))))
        self.variable_edit = QtWidgets.QLineEdit(str(rule.get("variable") or ""))
        self.variable_edit.setPlaceholderText("예: retry_count")
        self.operator_combo = QtWidgets.QComboBox()
        for label, value in (("이상 ≥", ">="), ("이하 ≤", "<="), ("초과 >", ">"), ("미만 <", "<"), ("같음 =", "=="), ("다름 ≠", "!=")):
            self.operator_combo.addItem(label, value)
        self.operator_combo.setCurrentIndex(max(0, self.operator_combo.findData(str(rule.get("operator") or ">="))))
        self.value_spin = WheelSafeSpinBox()
        self.value_spin.setRange(-999_999, 999_999)
        self.value_spin.setValue(int(rule.get("value") or 1))
        self.target_combo = QtWidgets.QComboBox()
        for index in range(1, step_count + 1):
            self.target_combo.addItem(f"{index}번 노드", index)
        self.target_combo.setCurrentIndex(max(0, self.target_combo.findData(int(rule.get("target") or 1))))
        self.delay_spin = WheelSafeSpinBox()
        self.delay_spin.setRange(0, 600_000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(int(rule.get("delay") or 0))
        self.reset_check = QtWidgets.QCheckBox("조건이 맞으면 연결 횟수를 0으로 초기화")
        self.reset_check.setChecked(bool(rule.get("reset_on_match", False)))
        form.addRow("플로우 표시 이름", self.label_edit)
        form.addRow("비교 값", self.source_combo)
        form.addRow("사용자 변수", self.variable_edit)
        form.addRow("연산자", self.operator_combo)
        form.addRow("기준값", self.value_spin)
        form.addRow("분기 목적지", self.target_combo)
        form.addRow("분기 전 대기", self.delay_spin)
        form.addRow("카운터", self.reset_check)
        layout.addLayout(form)
        self.sentence_label = QtWidgets.QLabel()
        self.sentence_label.setWordWrap(True)
        self.sentence_label.setStyleSheet(
            f"background:{COLORS['surface_alt']}; border:1px solid {COLORS['border']}; border-radius:8px; padding:10px; font-weight:700;"
        )
        layout.addWidget(self.sentence_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source_combo.currentIndexChanged.connect(self._sync_source)
        self.variable_edit.textChanged.connect(self._sync_sentence)
        self.operator_combo.currentIndexChanged.connect(self._sync_sentence)
        self.value_spin.valueChanged.connect(self._sync_sentence)
        self.target_combo.currentIndexChanged.connect(self._sync_sentence)
        self._sync_source()

    def _sync_source(self) -> None:
        self.variable_edit.setEnabled(self.source_combo.currentData() == "variable")
        self._sync_sentence()

    def _sync_sentence(self) -> None:
        source = (
            f"변수 '{self.variable_edit.text().strip() or '변수'}'"
            if self.source_combo.currentData() == "variable"
            else "이 연결의 실행 횟수"
        )
        operators = {">=": "이상", "<=": "이하", ">": "초과", "<": "미만", "==": "같으면", "!=": "다르면"}
        operator = operators.get(str(self.operator_combo.currentData() or ">="), str(self.operator_combo.currentData() or ">="))
        self.sentence_label.setText(
            f"{source}가 {self.value_spin.value()} {operator} → {self.target_combo.currentData() or 1}번 노드로 이동"
        )

    def _accept_if_valid(self) -> None:
        if self.source_combo.currentData() == "variable":
            name = self.variable_edit.text().strip()
            if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
                QtWidgets.QMessageBox.warning(self, "변수 이름 확인", "영문, 숫자, 밑줄로 된 변수 이름을 입력하세요.")
                return
        self.accept()

    def payload(self, kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "label": self.label_edit.text().strip(),
            "source": self.source_combo.currentData() or "edge_count",
            "variable": self.variable_edit.text().strip(),
            "operator": self.operator_combo.currentData() or ">=",
            "value": self.value_spin.value(),
            "target": self.target_combo.currentData() or 1,
            "delay": self.delay_spin.value(),
            "reset_on_match": self.reset_check.isChecked(),
        }


class EdgeSettingsDialog(QtWidgets.QDialog):
    def __init__(self, step_count: int, kind: str, delay: int, rules: list[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.step_count = step_count
        self.kind = kind
        self.rules = [deepcopy(rule) for rule in rules]
        self.setWindowTitle("노드라인 설정")
        self.resize(720, 470)
        layout = QtWidgets.QVBoxLayout(self)
        route = "성공" if kind == "success" else "실패"
        title = QtWidgets.QLabel(f"{route} 노드라인")
        title.setStyleSheet("font-size:15pt; font-weight:800;")
        layout.addWidget(title)
        delay_row = QtWidgets.QFormLayout()
        self.delay_spin = WheelSafeSpinBox()
        self.delay_spin.setRange(0, 600_000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(delay)
        delay_row.addRow("기본 연결 딜레이", self.delay_spin)
        layout.addLayout(delay_row)
        note = QtWidgets.QLabel("조건 분기는 위에서부터 검사합니다. 조건이 맞으면 점선 노드라인의 목적지로 이동합니다.")
        note.setObjectName("Muted")
        layout.addWidget(note)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["표시", "비교", "기준", "목적지", "리셋"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for column in (2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit_rule())
        layout.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        add = primary_button("＋ 조건 분기 추가")
        edit = QtWidgets.QPushButton("선택 편집")
        remove = danger_button("선택 삭제")
        add.clicked.connect(self._add_rule)
        edit.clicked.connect(self._edit_rule)
        remove.clicked.connect(self._remove_rule)
        row.addWidget(add)
        row.addWidget(edit)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_rules()

    def _refresh_rules(self) -> None:
        self.table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            source = "횟수" if rule.get("source", "edge_count") == "edge_count" else str(rule.get("variable") or "변수")
            values = [
                str(rule.get("label") or "조건 분기"),
                source,
                f"{rule.get('operator', '>=')} {rule.get('value', 1)}",
                f"#{rule.get('target', 1)}",
                "예" if rule.get("reset_on_match") else "아니오",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        if self.rules:
            self.table.selectRow(min(max(self.table.currentRow(), 0), len(self.rules) - 1))

    def _add_rule(self) -> None:
        dialog = EdgeConditionDialog(self.step_count, self.kind, parent=self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.rules.append(dialog.payload(self.kind))
            self._refresh_rules()

    def _edit_rule(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.rules):
            return
        dialog = EdgeConditionDialog(self.step_count, self.kind, self.rules[row], self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.rules[row] = dialog.payload(self.kind)
            self._refresh_rules()

    def _remove_rule(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules.pop(row)
            self._refresh_rules()


class ActionEditorDialog(QtWidgets.QDialog):
    def __init__(self, repository: MacroRepository, step: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        action = str(step.get("action") or "wait")
        action_name = "멀티 이미지 서치" if action == "image_search" and len(step.get("assets") or []) > 1 else ACTION_LABELS.get(action, action)
        self.setWindowTitle(f"{action_name} · 상세 설정")
        self.resize(780, 780)
        self.setMinimumSize(660, 620)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(action_name)
        title.setStyleSheet("font-size:17pt; font-weight:800;")
        hint = QtWidgets.QLabel("긴 설정을 넓은 창에서 편집합니다. '설정 저장'을 누르면 단계와 매크로 파일에 즉시 반영됩니다.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.editor = ActionEditor(repository)
        self.editor.refresh_sources()
        self.editor.load_step(step)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("설정 저장")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._position_next_to_studio)

    def _position_next_to_studio(self) -> None:
        parent = self.parentWidget()
        host = parent.window() if parent is not None else None
        if host is None or host is self:
            return
        host_rect = host.frameGeometry()
        screen = QtGui.QGuiApplication.screenAt(host_rect.center()) or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        dialog_size = self.frameGeometry().size()
        x = host_rect.right() + 1
        y = host_rect.top()
        if x + dialog_size.width() > available.right() + 1:
            left_x = host_rect.left() - dialog_size.width()
            x = left_x if left_x >= available.left() else available.right() - dialog_size.width() + 1
        x = max(available.left(), min(x, available.right() - dialog_size.width() + 1))
        y = max(available.top(), min(y, available.bottom() - dialog_size.height() + 1))
        self.move(x, y)

    def payload(self) -> dict[str, Any]:
        return self.editor.build_step()


class RecentClickPreviewPopup(QtWidgets.QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QFrame { background:#11151E; border:2px solid #32E6D0; border-radius:10px; }"
            "QLabel { color:#F2F4F8; border:none; background:transparent; }"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.title = QtWidgets.QLabel("최근 실제 클릭 위치")
        self.title.setStyleSheet("font-weight:800;")
        self.image = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        self.image.setMinimumSize(360, 220)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.image)
        layout.addWidget(self.detail)

    def set_preview(self, pixmap: QtGui.QPixmap | None, description: str = "") -> None:
        if pixmap is None or pixmap.isNull():
            self.image.clear()
            self.detail.clear()
            self.hide()
            return
        self.image.setPixmap(pixmap.scaled(540, 330, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self.detail.setText(description)
        self.adjustSize()

    def show_near(self, anchor: QtWidgets.QWidget) -> None:
        pixmap = self.image.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        anchor_top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        origin = anchor.mapToGlobal(QtCore.QPoint(anchor.width(), anchor.height()))
        target = origin + QtCore.QPoint(10, 8)
        screen = QtGui.QGuiApplication.screenAt(origin) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if target.x() + self.width() > area.right():
                target.setX(anchor_top_left.x() - self.width() - 10)
            target.setX(max(area.left(), min(target.x(), area.right() - self.width())))
            target.setY(max(area.top(), min(target.y(), area.bottom() - self.height())))
        self.move(target)
        self.show()
        self.raise_()


class RecentClickPreviewButton(QtWidgets.QToolButton):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.popup = RecentClickPreviewPopup(parent)
        self.setText("⌖")
        self.setFixedSize(38, 34)
        self.setCheckable(True)
        self.setEnabled(False)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("최근 실제 클릭 위치 · 커서를 올려 확인하고 클릭하여 고정")
        self.setStyleSheet(
            "QToolButton { color:#32E6D0; background:#111722; border:1px solid #334158;"
            " border-radius:7px; font-size:18px; font-weight:800; }"
            "QToolButton:hover, QToolButton:checked { background:#17302E; border-color:#32E6D0; }"
            "QToolButton:disabled { color:#586274; background:#11151D; border-color:#283140; }"
        )
        self.toggled.connect(self._toggle_pinned)

    def set_preview(self, pixmap: QtGui.QPixmap | None, description: str = "") -> None:
        self.setChecked(False)
        self.popup.set_preview(pixmap, description)
        self.setEnabled(pixmap is not None and not pixmap.isNull())

    def _toggle_pinned(self, pinned: bool) -> None:
        if pinned:
            self.popup.show_near(self)
        else:
            self.popup.hide()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        if self.isEnabled():
            self.popup.show_near(self)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if not self.isChecked():
            self.popup.hide()
        super().leaveEvent(event)


class BuilderPage(QtWidgets.QWidget):
    data_changed = QtCore.Signal()
    status = QtCore.Signal(str)
    run_macro = QtCore.Signal(str)
    run_macro_step = QtCore.Signal(str, int)
    run_macro_from_step = QtCore.Signal(str, int)
    run_macro_dry_run = QtCore.Signal(str)
    stop_macros = QtCore.Signal()
    open_export = QtCore.Signal(str)
    edit_committed = QtCore.Signal()

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.current_name = ""
        self.current_macro: dict[str, Any] | None = None
        self._undo_history: dict[str, list[dict[str, Any]]] = {}
        self._redo_history: dict[str, list[dict[str, Any]]] = {}
        self._last_persisted_macro: dict[str, Any] | None = None
        self._history_suspended = False
        self._loading = False
        self._graph_save_timer = QtCore.QTimer(self)
        self._graph_save_timer.setSingleShot(True)
        self._graph_save_timer.setInterval(450)
        self._graph_save_timer.timeout.connect(self._save_graph_positions)
        self._data_change_timer = QtCore.QTimer(self)
        self._data_change_timer.setSingleShot(True)
        self._data_change_timer.setInterval(140)
        self._data_change_timer.timeout.connect(self.data_changed.emit)
        self._action_settings_dialog: ActionEditorDialog | None = None
        self._log_dialog: MacroLogDialog | None = None
        self._recording_controller: SmartRecordingController | None = None
        self._automation_overlay: AutomationOverlay | None = None
        self._collapsed_groups: set[str] = set()
        self._last_recording_path = self.repository.root / ".automation" / "last-recording.json"
        self._handle_profiles_path = self.repository.root / ".automation" / "inactive-click-profiles.json"
        self._inactive_handle_profiles = self._load_inactive_handle_profiles()
        self._last_recording_events = self._load_last_recording()
        self._subflow_parent_stack: list[tuple[str, int]] = []
        self.shortcut_buttons: dict[str, QtWidgets.QPushButton] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        root.addWidget(PageHeader("노드 매크로 빌더", "스마트 녹화 · 자동 설정 · 단계 테스트 · 자동 진단 · 재사용 블록"))

        toolbar_top = QtWidgets.QHBoxLayout()
        toolbar_tools = QtWidgets.QHBoxLayout()
        new_btn = primary_button("＋ 새 매크로")
        new_btn.clicked.connect(self._create_macro)
        duplicate_btn = QtWidgets.QPushButton("복제")
        duplicate_btn.clicked.connect(self._duplicate_macro)
        archive_btn = danger_button("보관")
        archive_btn.clicked.connect(self._archive_macro)
        self.record_btn = QtWidgets.QPushButton("● 스마트 녹화")
        self.record_btn.setToolTip("F9 세션 시작 · ` 기록 ON/OFF · Shift+` 일반/분기 모드 · F8 이미지 캡처 · F10 종료")
        self.record_btn.clicked.connect(self._start_smart_recording)
        self.review_recording_btn = QtWidgets.QPushButton("▤ 최근 녹화 검토")
        self.review_recording_btn.setToolTip("닫았던 마지막 스마트 녹화 검토창을 다시 엽니다.")
        self.review_recording_btn.setEnabled(bool(self._last_recording_events))
        self.review_recording_btn.clicked.connect(self._open_last_recording_review)
        self.inactive_handle_lab_btn = QtWidgets.QPushButton("⌑ 비활성 클릭 핸들 실험실")
        self.inactive_handle_lab_btn.setToolTip(
            "대상 프로그램의 핸들을 시험하고 저장합니다. 이후 같은 프로그램의 스마트 녹화 클릭에 자동 적용됩니다."
        )
        self.inactive_handle_lab_btn.clicked.connect(self._open_inactive_handle_lab)
        self.branch_group_btn = QtWidgets.QPushButton("⑂ 선택 노드 분기 묶기")
        self.branch_group_btn.setToolTip(
            "여러 이미지 서치·OCR 노드를 번호 순서대로 검사합니다. 성공한 노드의 흐름만 실행하고, 실패 시 다음 후보로 이동합니다."
        )
        self.branch_group_btn.clicked.connect(
            lambda: self._configure_start_search_candidates(self.node_canvas.selected_indexes())
        )
        self.action_combo = QtWidgets.QComboBox()
        self._populate_action_combo(self.action_combo)
        self.action_combo.setMinimumWidth(145)
        self.action_combo.setToolTip("추가할 노드 액션 선택")
        self.add_node_button = primary_button("＋ 마우스 클릭 노드 추가")
        self.add_node_button.setToolTip("선택한 노드의 성공 흐름에 새 노드를 추가합니다. 기존 연결이 있으면 그 사이에 삽입합니다.")
        self.add_node_button.clicked.connect(self._add_step)
        wizard_btn = QtWidgets.QPushButton("⚡ 자동 설정")
        wizard_btn.setToolTip("선택한 액션을 짧은 안내에 따라 자동 구성")
        wizard_btn.clicked.connect(self._quick_action_wizard)
        self.action_combo.currentIndexChanged.connect(self._update_add_node_label)
        self.run_button = QtWidgets.QPushButton("▶ 실행")
        self.run_button.setToolTip("현재 편집 중인 단계를 저장한 뒤 매크로 실행")
        self.run_button.clicked.connect(self._run_current)
        self.dry_run_button = QtWidgets.QPushButton("▷ 드라이런")
        self.dry_run_button.setToolTip("클릭·입력·프로그램 실행 없이 이미지/OCR/조건과 다음 흐름만 시뮬레이션")
        self.dry_run_button.clicked.connect(self._run_dry_run)
        self.stop_button = danger_button("■ 정지")
        self.stop_button.setToolTip("현재 Studio에서 실행한 매크로와 이미지 검색 하위 프로세스를 즉시 중단")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_running_macros)
        log_btn = QtWidgets.QPushButton("▤ 실행 디버거")
        log_btn.setToolTip("노드별 성공·실패·시간·이미지/OCR 결과·변수와 실행 제어 보기")
        log_btn.clicked.connect(self._open_logs)
        diagnose_btn = QtWidgets.QPushButton("✓ 자동 진단")
        diagnose_btn.setToolTip("연결·이미지·대상 창·실행 가능성을 검사하고 수정 제안")
        diagnose_btn.clicked.connect(self._diagnose_automation)
        toolbar_top.addWidget(new_btn)
        toolbar_top.addWidget(duplicate_btn)
        toolbar_top.addWidget(archive_btn)
        toolbar_top.addStretch(1)
        toolbar_top.addWidget(self.dry_run_button)
        toolbar_top.addWidget(self.run_button)
        toolbar_top.addWidget(self.stop_button)
        toolbar_top.addWidget(log_btn)
        recording_tools = QtWidgets.QToolButton()
        recording_tools.setText("녹화 도구 ▾")
        recording_tools.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        recording_menu = QtWidgets.QMenu(recording_tools)
        recording_menu.addAction("최근 녹화 검토", self.review_recording_btn.click)
        recording_menu.addAction("비활성 클릭 핸들 실험실", self.inactive_handle_lab_btn.click)
        recording_menu.addAction("선택 노드 분기 묶기", self.branch_group_btn.click)
        recording_tools.setMenu(recording_menu)
        # Keep these command buttons alive for shortcuts and UI automation. Their
        # visible entry points live in the compact recording menu above.
        for command_button in (
            self.review_recording_btn, self.inactive_handle_lab_btn, self.branch_group_btn,
        ):
            command_button.setParent(self)
            command_button.hide()
        action_label = QtWidgets.QLabel("추가할 노드 액션")
        action_label.setObjectName("Muted")
        toolbar_tools.addWidget(self.record_btn)
        toolbar_tools.addWidget(recording_tools)
        toolbar_tools.addSpacing(8)
        toolbar_tools.addWidget(action_label)
        toolbar_tools.addWidget(self.action_combo)
        toolbar_tools.addWidget(wizard_btn)
        toolbar_tools.addWidget(self.add_node_button)
        toolbar_tools.addStretch(1)
        toolbar_tools.addWidget(diagnose_btn)
        root.addLayout(toolbar_top)
        root.addLayout(toolbar_tools)

        self.shortcut_buttons.update(
            {
                "action_create_macro": new_btn,
                "action_smart_record": self.record_btn,
                "action_quick_automation": wizard_btn,
                "action_add_node": self.add_node_button,
                "action_run_macro": self.run_button,
                "action_diagnose_automation": diagnose_btn,
            }
        )

        self.builder_splitter = QtWidgets.QSplitter()
        self.builder_splitter.setChildrenCollapsible(True)
        self.macro_panel = self._build_macro_panel()
        self.steps_panel = self._build_steps_panel()
        self.inspector_panel = self._build_inspector()
        self.builder_splitter.addWidget(self.macro_panel)
        self.builder_splitter.addWidget(self.steps_panel)
        self.builder_splitter.addWidget(self.inspector_panel)
        self.builder_splitter.setStretchFactor(0, 0)
        self.builder_splitter.setStretchFactor(1, 1)
        self.builder_splitter.setStretchFactor(2, 0)
        self.builder_splitter.setSizes([230, 1050, 360])
        root.addWidget(self.builder_splitter, 1)

    def _build_macro_panel(self) -> QtWidgets.QWidget:
        card = Card()
        card.setMinimumWidth(190)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        title = QtWidgets.QLabel("매크로")
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("이름 또는 설명 검색")
        self.search_edit.textChanged.connect(self._filter_macros)
        group_row = QtWidgets.QHBoxLayout()
        self.group_combo = QtWidgets.QComboBox()
        self.group_combo.setToolTip("선택한 매크로를 분류할 상위 폴더")
        group_button = QtWidgets.QPushButton("이동")
        group_button.setToolTip("선택한 매크로를 지정한 폴더로 이동")
        group_button.clicked.connect(self._assign_selected_group)
        group_row.addWidget(self.group_combo, 1)
        group_row.addWidget(group_button)
        folder_controls = QtWidgets.QHBoxLayout()
        collapse_btn = QtWidgets.QPushButton("▸ 접기")
        expand_btn = QtWidgets.QPushButton("▾ 펼치기")
        collapse_btn.setToolTip("모든 폴더 접기")
        expand_btn.setToolTip("모든 폴더 펼치기")
        collapse_btn.setMinimumWidth(68)
        expand_btn.setMinimumWidth(68)
        collapse_btn.clicked.connect(lambda: self._set_all_groups_collapsed(True))
        expand_btn.clicked.connect(lambda: self._set_all_groups_collapsed(False))
        folder_controls.addWidget(collapse_btn)
        folder_controls.addWidget(expand_btn)
        self.macro_list = QtWidgets.QListWidget()
        self.macro_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.macro_list.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.macro_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.macro_list.customContextMenuRequested.connect(lambda _pos: self._assign_selected_group())
        self.macro_list.itemClicked.connect(self._toggle_macro_group)
        self.macro_list.currentItemChanged.connect(self._select_macro)
        layout.addWidget(title)
        layout.addWidget(self.search_edit)
        layout.addLayout(group_row)
        layout.addLayout(folder_controls)
        layout.addWidget(self.macro_list, 1)
        return card

    def _build_steps_panel(self) -> QtWidgets.QWidget:
        card = Card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        header = QtWidgets.QHBoxLayout()
        self.macro_title = QtWidgets.QLabel("매크로를 선택하세요")
        self.macro_title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.subflow_back_button = QtWidgets.QPushButton("← 상위 흐름")
        self.subflow_back_button.setToolTip("서브플로우를 호출한 상위 매크로로 돌아갑니다.")
        self.subflow_back_button.setVisible(False)
        self.subflow_back_button.clicked.connect(self._leave_subflow)
        self.duplicate_node_button = QtWidgets.QPushButton("⧉ 노드 복제")
        self.duplicate_node_button.setToolTip("선택한 노드를 복제합니다 (Ctrl+D)")
        self.duplicate_node_button.clicked.connect(
            lambda: self._duplicate_node_from_graph(self.node_canvas.selected_index())
        )
        self.test_node_button = QtWidgets.QPushButton("▶ 선택 단계 테스트")
        self.test_node_button.clicked.connect(self._test_selected_step)
        self.node_more_button = QtWidgets.QToolButton()
        self.node_more_button.setText("⋯ 더보기")
        self.node_more_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.node_more_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.node_more_button.setMinimumWidth(88)
        self.node_more_menu = QtWidgets.QMenu(self.node_more_button)
        sequential_action = self.node_more_menu.addAction("순차 연결")
        sequential_action.setToolTip("연결되지 않은 노드를 목록 순서대로 연결합니다.")
        sequential_action.triggered.connect(self._connect_sequentially)
        start_action = self.node_more_menu.addAction("시작 노드로 지정")
        start_action.triggered.connect(lambda: self._set_graph_marker("start"))
        resume_action = self.node_more_menu.addAction("선택 노드부터 실제 실행")
        resume_action.setToolTip("선택 노드부터 이어지는 전체 흐름을 실제로 실행합니다.")
        resume_action.triggered.connect(self._run_from_selected_step)
        end_action = self.node_more_menu.addAction("종료 노드로 지정")
        end_action.triggered.connect(lambda: self._set_graph_marker("end"))
        recovery_action = self.node_more_menu.addAction("복구 실행 설정")
        recovery_action.setToolTip("연속 실패 자동 정지와 체크포인트 재개 정책을 설정합니다.")
        recovery_action.triggered.connect(self._configure_recovery_engine)
        trigger_action = self.node_more_menu.addAction("이벤트 자동 실행 설정")
        trigger_action.setToolTip("프로그램·창·이미지·OCR·시간 조건으로 현재 매크로를 자동 실행합니다.")
        trigger_action.triggered.connect(self._configure_event_triggers)
        test_cases_action = self.node_more_menu.addAction("매크로 테스트 케이스")
        test_cases_action.setToolTip("이미지·OCR·변수 입력에 대한 기대 경로를 저장하고 업데이트 후 자동 회귀 검사합니다.")
        test_cases_action.triggered.connect(self._open_macro_test_cases)
        self.node_more_menu.addSeparator()
        save_block_action = self.node_more_menu.addAction("선택 노드를 블록으로 저장")
        save_block_action.setToolTip("선택한 노드 묶음을 다른 매크로에서 재사용합니다.")
        save_block_action.triggered.connect(self._save_selected_block)
        add_block_action = self.node_more_menu.addAction("저장된 블록 추가")
        add_block_action.triggered.connect(self._insert_automation_block)
        version_action = self.node_more_menu.addAction("버전 기록·복구")
        version_action.setToolTip("자동 저장된 이전 버전을 확인하고 현재 매크로로 복구합니다.")
        version_action.triggered.connect(self._open_version_history)
        stable_action = self.node_more_menu.addAction("현재를 안정 버전으로 표시")
        stable_action.triggered.connect(lambda: self._set_current_release_channel("stable"))
        test_channel_action = self.node_more_menu.addAction("현재를 테스트 버전으로 표시")
        test_channel_action.triggered.connect(lambda: self._set_current_release_channel("test"))
        self.node_more_button.setMenu(self.node_more_menu)
        header.addWidget(self.subflow_back_button)
        self.macro_panel_toggle = QtWidgets.QPushButton("◀ 목록")
        self.macro_panel_toggle.setToolTip("매크로 목록 패널 접기/펼치기")
        self.macro_panel_toggle.clicked.connect(lambda: self._toggle_builder_side("macro"))
        header.addWidget(self.macro_panel_toggle)
        header.addWidget(self.macro_title)
        header.addStretch(1)
        self.inspector_panel_toggle = QtWidgets.QPushButton("설정 ▶")
        self.inspector_panel_toggle.setToolTip("단계 설정 패널 접기/펼치기")
        self.inspector_panel_toggle.clicked.connect(lambda: self._toggle_builder_side("inspector"))
        header.addWidget(self.inspector_panel_toggle)
        node_actions = QtWidgets.QHBoxLayout()
        node_actions.setSpacing(7)
        self.undo_button = QtWidgets.QPushButton("↶ 실행 취소")
        self.undo_button.setToolTip("마지막 매크로 편집 실행 취소 (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo_edit)
        self.redo_button = QtWidgets.QPushButton("↷ 다시 실행")
        self.redo_button.setToolTip("취소한 매크로 편집 다시 실행 (Ctrl+Y)")
        self.redo_button.clicked.connect(self.redo_edit)
        node_actions.addWidget(self.undo_button)
        node_actions.addWidget(self.redo_button)
        node_actions.addWidget(self.duplicate_node_button)
        node_actions.addWidget(self.test_node_button)
        node_actions.addWidget(self.node_more_button)
        node_actions.addStretch(1)
        self._update_history_buttons()

        self.node_canvas = NodeCanvas()
        self.node_canvas.node_selected.connect(self._select_graph_node)
        self.node_canvas.inspector_requested.connect(self._focus_inspector)
        self.node_canvas.positions_changed.connect(self._graph_positions_changed)
        self.node_canvas.routes_changed.connect(self._graph_routes_changed)
        self.node_canvas.link_requested.connect(self._connect_graph_nodes)
        self.node_canvas.edge_delete_requested.connect(self._delete_graph_edge)
        self.node_canvas.edge_delay_requested.connect(self._set_graph_edge_delay)
        self.node_canvas.edge_condition_delete_requested.connect(self._delete_graph_condition)
        self.node_canvas.edge_condition_retarget_requested.connect(self._retarget_graph_condition)
        self.node_canvas.node_delete_requested.connect(self._delete_node_from_graph)
        self.node_canvas.node_duplicate_requested.connect(self._duplicate_node_from_graph)
        self.node_canvas.wait_duration_requested.connect(self._set_selected_wait_durations)
        self.node_canvas.all_wait_duration_requested.connect(self._set_all_wait_durations)
        self.node_canvas.start_search_group_requested.connect(self._configure_start_search_candidates)
        self.node_canvas.image_edit_requested.connect(self._edit_node_search_image)

        list_page = QtWidgets.QWidget()
        list_layout = QtWidgets.QVBoxLayout(list_page)
        list_layout.setContentsMargins(4, 8, 4, 4)
        self.steps_table = QtWidgets.QTableWidget(0, 5)
        self.steps_table.setHorizontalHeaderLabels(["#", "액션", "이름", "성공", "실패"])
        self.steps_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.steps_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.steps_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.steps_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.steps_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.steps_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.steps_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.steps_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.steps_table.currentCellChanged.connect(self._select_step)
        controls = QtWidgets.QHBoxLayout()
        up_btn = QtWidgets.QPushButton("↑ 위로")
        down_btn = QtWidgets.QPushButton("↓ 아래로")
        connect_btn = QtWidgets.QPushButton("순차 연결")
        remove_btn = danger_button("단계 보관")
        up_btn.clicked.connect(lambda: self._move_step(-1))
        down_btn.clicked.connect(lambda: self._move_step(1))
        connect_btn.clicked.connect(self._connect_sequentially)
        remove_btn.clicked.connect(self._remove_step)
        controls.addWidget(up_btn)
        controls.addWidget(down_btn)
        controls.addWidget(connect_btn)
        controls.addStretch(1)
        controls.addWidget(remove_btn)
        layout.addLayout(header)
        layout.addLayout(node_actions)
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self.node_canvas, "노드 플로우")
        list_layout.addWidget(self.steps_table, 1)
        list_layout.addLayout(controls)
        tabs.addTab(list_page, "단계 목록")
        layout.addWidget(tabs, 1)
        return card

    def _build_inspector(self) -> QtWidgets.QWidget:
        card = Card()
        card.setMinimumWidth(0)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        title = QtWidgets.QLabel("단계 설정")
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        form = QtWidgets.QFormLayout()
        self.inspector_action = QtWidgets.QComboBox()
        self._populate_action_combo(self.inspector_action)
        self.label_edit = QtWidgets.QLineEdit()
        self.repeat_spin = WheelSafeSpinBox()
        self.repeat_var_edit = QtWidgets.QLineEdit()
        self.repeat_var_edit.setPlaceholderText("예: $run_count")
        self.repeat_var_edit.setToolTip("OCR나 변수 노드에서 저장한 값을 이 단계의 실행 횟수로 사용합니다.")
        self.success_spin = WheelSafeSpinBox()
        self.fail_spin = WheelSafeSpinBox()
        self.success_delay_spin = WheelSafeSpinBox()
        self.fail_delay_spin = WheelSafeSpinBox()
        self.node_retry_spin = WheelSafeSpinBox()
        self.node_retry_delay_spin = WheelSafeSpinBox()
        self.delay_spin = WheelSafeSpinBox()
        for spin in (self.success_spin, self.fail_spin):
            spin.setRange(0, 999)
            spin.setSpecialValueText("자동")
        self.repeat_spin.setRange(1, 999)
        self.repeat_spin.setSuffix("회")
        for spin in (self.success_delay_spin, self.fail_delay_spin, self.delay_spin):
            spin.setRange(0, 600_000)
            spin.setSuffix(" ms")
        self.node_retry_spin.setRange(0, 100)
        self.node_retry_spin.setSuffix("회")
        self.node_retry_delay_spin.setRange(10, 600_000)
        self.node_retry_delay_spin.setSuffix(" ms")
        form.addRow("액션", self.inspector_action)
        form.addRow("표시 이름", self.label_edit)
        form.addRow("단계 반복", self.repeat_spin)
        form.addRow("반복 횟수 변수", self.repeat_var_edit)
        form.addRow("성공 시 이동", self.success_spin)
        form.addRow("성공 이동 전 대기", self.success_delay_spin)
        form.addRow("실패 시 이동", self.fail_spin)
        form.addRow("실패 이동 전 대기", self.fail_delay_spin)
        form.addRow("실패 시 재시도", self.node_retry_spin)
        form.addRow("재시도 간격", self.node_retry_delay_spin)
        form.addRow("완료 후 대기", self.delay_spin)

        self.action_editor = ActionEditor(self.repository, card)
        self.action_editor.hide()
        action_box = QtWidgets.QGroupBox("액션 전용 설정")
        action_box_layout = QtWidgets.QVBoxLayout(action_box)
        self.action_summary_label = QtWidgets.QLabel("노드를 선택하세요.")
        self.action_summary_label.setObjectName("Muted")
        self.action_summary_label.setWordWrap(True)
        action_settings_btn = primary_button("상세 설정 창 열기")
        action_settings_btn.clicked.connect(self._open_action_settings)
        export_btn = QtWidgets.QPushButton("⇧ 내보내기")
        export_btn.setToolTip("현재 매크로를 선택한 상태로 내보내기 화면을 엽니다.")
        export_btn.clicked.connect(self._open_current_export)
        step_test_btn = QtWidgets.QPushButton("▶ 이 단계만 테스트")
        step_test_btn.setToolTip("현재 노드만 격리 실행하고 좌표·검색 범위를 화면에 미리 표시")
        step_test_btn.clicked.connect(self._test_selected_step)
        step_test_row = QtWidgets.QHBoxLayout()
        step_test_row.setSpacing(6)
        self.recent_click_preview_btn = RecentClickPreviewButton(card)
        step_test_row.addWidget(step_test_btn, 1)
        step_test_row.addWidget(self.recent_click_preview_btn)
        action_box_layout.addWidget(self.action_summary_label)
        action_box_layout.addWidget(action_settings_btn)
        action_box_layout.addWidget(export_btn)
        action_box_layout.addLayout(step_test_row)
        self._recent_click_source_pixmap: QtGui.QPixmap | None = None
        self._recent_click_description = ""
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText("▸  고급 JSON")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.advanced_toggle.toggled.connect(self._toggle_advanced_json)
        self.json_panel = QtWidgets.QWidget()
        json_layout = QtWidgets.QVBoxLayout(self.json_panel)
        json_layout.setContentsMargins(0, 0, 0, 0)
        self.json_edit = QtWidgets.QPlainTextEdit()
        self.json_edit.setPlaceholderText("단계를 선택하면 전체 설정이 표시됩니다.")
        self.json_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.json_edit.setMinimumHeight(190)
        json_buttons = QtWidgets.QHBoxLayout()
        preview_btn = QtWidgets.QPushButton("폼 → JSON 미리보기")
        apply_json_btn = QtWidgets.QPushButton("JSON → 폼 적용")
        preview_btn.clicked.connect(self._form_to_json)
        apply_json_btn.clicked.connect(self._json_to_form)
        json_buttons.addWidget(preview_btn)
        json_buttons.addWidget(apply_json_btn)
        json_layout.addWidget(self.json_edit)
        json_layout.addLayout(json_buttons)
        self.json_panel.setVisible(False)
        save_btn = primary_button("변경사항 저장")
        save_btn.clicked.connect(self._save_step)
        self.inspector_action.currentIndexChanged.connect(self._change_action_template)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 6, 2)
        content_layout.setSpacing(9)
        content_layout.addWidget(title)
        content_layout.addLayout(form)
        content_layout.addWidget(action_box)
        content_layout.addStretch(1)
        content_layout.addWidget(self.advanced_toggle)
        content_layout.addWidget(self.json_panel)
        inspector_scroll = QtWidgets.QScrollArea()
        inspector_scroll.setObjectName("InspectorScroll")
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inspector_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        inspector_scroll.setWidget(content)
        self.inspector_scroll = inspector_scroll
        for control in (
            self.inspector_action,
            self.label_edit,
            self.repeat_spin,
            self.repeat_var_edit,
            self.success_spin,
            self.success_delay_spin,
            self.fail_spin,
            self.fail_delay_spin,
            self.node_retry_spin,
            self.node_retry_delay_spin,
            self.delay_spin,
        ):
            control.setMinimumHeight(34)
            control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        form.setVerticalSpacing(8)
        layout.addWidget(inspector_scroll, 1)
        layout.addWidget(save_btn)
        return card

    def _edit_node_search_image(self, step_index: int) -> None:
        steps = list((self.current_macro or {}).get("steps") or [])
        row = int(step_index) - 1
        if not 0 <= row < len(steps):
            return
        step = steps[row]
        if str(step.get("action") or "") not in {"image_search", "screen_condition"}:
            return
        aliases = [str(value) for value in step.get("assets") or [] if str(value).strip()] if isinstance(step.get("assets"), list) else []
        primary = str(step.get("asset") or "").strip()
        if primary and primary not in aliases:
            aliases.insert(0, primary)
        aliases = [alias for alias in dict.fromkeys(aliases) if self.repository.asset_path(alias) is not None]
        if not aliases:
            self.status.emit("편집할 검색 이미지를 찾지 못했습니다.")
            return
        alias = aliases[0]
        if len(aliases) > 1:
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("멀티 이미지 서치 · 편집할 이미지 선택")
            dialog.setMinimumSize(680, 430)
            dialog.resize(820, 560)
            dialog_layout = QtWidgets.QVBoxLayout(dialog)
            hint = QtWidgets.QLabel("상세 편집할 이미지를 미리보기에서 선택하세요.")
            hint.setObjectName("Muted")
            picker = QtWidgets.QListWidget()
            picker.setViewMode(QtWidgets.QListView.IconMode)
            picker.setResizeMode(QtWidgets.QListView.Adjust)
            picker.setMovement(QtWidgets.QListView.Static)
            picker.setIconSize(QtCore.QSize(150, 105))
            picker.setGridSize(QtCore.QSize(180, 145))
            picker.setWordWrap(True)
            for candidate in aliases:
                path = self.repository.asset_path(candidate)
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(str(path)), candidate)
                item.setData(QtCore.Qt.UserRole, candidate)
                picker.addItem(item)
            picker.setCurrentRow(0)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("상세 편집")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            picker.itemDoubleClicked.connect(lambda _item: dialog.accept())
            dialog_layout.addWidget(hint)
            dialog_layout.addWidget(picker, 1)
            dialog_layout.addWidget(buttons)
            if dialog.exec() != QtWidgets.QDialog.Accepted or picker.currentItem() is None:
                return
            alias = str(picker.currentItem().data(QtCore.Qt.UserRole) or "")
        path = self.repository.asset_path(alias)
        if path is None:
            self.status.emit("이미지 파일을 찾지 못했습니다.")
            return
        editor = ImageEditorDialog(path, alias, self.repository.history_dir, self)
        if editor.exec() == QtWidgets.QDialog.Accepted:
            self._refresh_steps(row)
            self.status.emit(f"'{alias}' 이미지를 저장하고 미리보기에 적용했습니다.")

    @staticmethod
    def _populate_action_combo(combo: QtWidgets.QComboBox) -> None:
        for action, label in ACTION_LABELS.items():
            combo.addItem(label, action)

    @staticmethod
    def _selected_action(combo: QtWidgets.QComboBox) -> str:
        return str(combo.currentData() or "wait")

    def select_action(self, action: str) -> None:
        index = self.action_combo.findData(action)
        if index >= 0:
            self.action_combo.setCurrentIndex(index)

    def _update_add_node_label(self) -> None:
        label = self.action_combo.currentText() or "선택 액션"
        self.add_node_button.setText(f"＋ {label} 노드 추가")

    def _toggle_advanced_json(self, expanded: bool) -> None:
        self.advanced_toggle.setText(("▾" if expanded else "▸") + "  고급 JSON")
        self.json_panel.setVisible(expanded)

    def _build_form_payload(self) -> dict[str, Any]:
        payload = self.action_editor.build_step()
        payload["action"] = self._selected_action(self.inspector_action)
        label = self.label_edit.text().strip()
        if label:
            payload["label"] = label
        else:
            payload.pop("label", None)
        values = (
            ("repeat", self.repeat_spin.value(), 1),
            ("on_success", self.success_spin.value(), 0),
            ("on_fail", self.fail_spin.value(), 0),
            ("on_success_delay", self.success_delay_spin.value(), 0),
            ("on_fail_delay", self.fail_delay_spin.value(), 0),
            ("node_retry_count", self.node_retry_spin.value(), 0),
            ("node_retry_delay", self.node_retry_delay_spin.value(), 250),
            ("sleep_after", self.delay_spin.value(), 0),
        )
        for key, value, default in values:
            if value != default:
                payload[key] = value
            else:
                payload.pop(key, None)
        repeat_var = self.repeat_var_edit.text().strip().lstrip("$")
        if repeat_var:
            payload["repeat_var"] = repeat_var
        else:
            payload.pop("repeat_var", None)
        return payload

    def _open_action_settings(self) -> None:
        if not self.current_macro or self.steps_table.currentRow() < 0:
            self.status.emit("설정할 노드를 먼저 선택하세요.")
            return
        if self._action_settings_dialog is not None and self._action_settings_dialog.isVisible():
            self._action_settings_dialog.raise_()
            self._action_settings_dialog.activateWindow()
            return
        dialog = ActionEditorDialog(self.repository, self._build_form_payload(), self)
        dialog.setWindowModality(QtCore.Qt.NonModal)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        source_row = self.steps_table.currentRow()
        source_name = self.current_name
        dialog.accepted.connect(lambda row=source_row, name=source_name: self._apply_action_settings_dialog(dialog, name, row))
        dialog.destroyed.connect(lambda: setattr(self, "_action_settings_dialog", None))
        self._action_settings_dialog = dialog
        dialog.show()

    def _apply_action_settings_dialog(self, dialog: ActionEditorDialog, source_name: str, source_row: int) -> None:
        if source_name and source_name != self.current_name and self.repository.macro_path(source_name).exists():
            self.refresh(source_name)
        steps = (self.current_macro or {}).get("steps") or []
        if 0 <= source_row < len(steps):
            self.steps_table.selectRow(source_row)
            self._load_step(source_row)
        payload = dialog.payload()
        self.action_editor.load_step(payload)
        self._update_action_summary(payload)
        self.json_edit.setPlainText(json.dumps(self._build_form_payload(), ensure_ascii=False, indent=2))
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 <= source_row < len(steps):
            self.status.emit("상세 설정을 저장할 단계를 찾지 못했습니다.")
            return
        steps[source_row] = payload
        self._persist(f"{source_row + 1}번 단계의 상세 설정을 저장했습니다.")
        self._refresh_steps(source_row)
        self.status.emit("상세 설정과 매크로 파일을 저장했습니다.")

    def _update_action_summary(self, step: dict[str, Any]) -> None:
        action = str(step.get("action") or "wait")
        details: list[str] = []
        if action in {"image_search", "screen_condition"}:
            region_mode = str(step.get("region_mode") or "screen").lower()
            region_label = {"screen": "화면", "window": "창", "client": "클라이언트"}.get(region_mode, region_mode)
            region_count = len(step.get("regions") or [])
            region_count += sum(
                1
                for key in ("region", "region2")
                if isinstance(step.get(key), (list, tuple)) and len(step[key]) == 4
            )
            range_summary = f"{region_label} · {region_count}개" if region_count else f"{region_label} 전체"
            multi_assets = step.get("assets") if isinstance(step.get("assets"), list) else []
            details = [
                f"이미지: 멀티 {len(multi_assets)}개" if len(multi_assets) > 1 else f"이미지: {step.get('asset') or '미선택'}",
                f"엔진: {step.get('engine') or 'ahk'}",
                f"검색 범위: {range_summary}",
            ]
        elif action in {"mouse_click", "inactive_click"}:
            details = [f"좌표: {step.get('x', 0)}, {step.get('y', 0)}", f"버튼: {step.get('button', 'Left')}"]
        elif action == "type_text":
            details = [f"내용: {str(step.get('text') or '')[:35] or '비어 있음'}"]
        elif action == "wait":
            details = [f"대기: {int(step.get('duration') or 0)} ms"]
        elif action == "browser_action":
            details = [f"선택자: {step.get('selector') or '미입력'}", f"동작: {step.get('browser_action') or 'click'}"]
        else:
            details = [self._step_summary(step)]
        action_name = "멀티 이미지 서치" if action == "image_search" and len(step.get("assets") or []) > 1 else ACTION_LABELS.get(action, action)
        self.action_summary_label.setText(action_name + "\n" + "  ·  ".join(str(item) for item in details))

    def capture_current_action_coordinates(self) -> None:
        if self.action_editor.capture_current_coordinates():
            payload = self._build_form_payload()
            self._update_action_summary(payload)
            self.json_edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
            self.status.emit("좌표를 폼에 반영했습니다. 변경사항 저장을 누르세요.")
        else:
            self.status.emit("현재 액션은 마우스 좌표를 사용하지 않습니다.")

    def _form_to_json(self) -> None:
        self.json_edit.setPlainText(json.dumps(self._build_form_payload(), ensure_ascii=False, indent=2))

    def _json_to_form(self) -> None:
        try:
            payload = json.loads(self.json_edit.toPlainText() or "{}")
            if not isinstance(payload, dict):
                raise ValueError("단계 JSON은 객체여야 합니다.")
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "JSON 확인", str(exc))
            return
        action = str(payload.get("action") or self._selected_action(self.inspector_action))
        index = self.inspector_action.findData(action)
        if index < 0:
            QtWidgets.QMessageBox.warning(self, "액션 확인", f"'{action}' 액션은 지원하지 않습니다.")
            return
        self._loading = True
        self.inspector_action.setCurrentIndex(index)
        self._load_common_fields(payload)
        self.action_editor.load_step(payload)
        self._update_action_summary(payload)
        self._loading = False
        self.status.emit("고급 JSON을 폼에 적용했습니다. 저장 버튼을 누르면 확정됩니다.")

    def _load_common_fields(self, step: dict[str, Any]) -> None:
        self.label_edit.setText(str(step.get("label") or ""))
        self.repeat_spin.setValue(max(1, int(step.get("repeat") or 1)))
        self.repeat_var_edit.setText(str(step.get("repeat_var") or ""))
        self.success_spin.setValue(int(step.get("on_success") or 0))
        self.fail_spin.setValue(int(step.get("on_fail") or 0))
        self.success_delay_spin.setValue(int(step.get("on_success_delay") or 0))
        self.fail_delay_spin.setValue(int(step.get("on_fail_delay") or 0))
        self.node_retry_spin.setValue(int(step.get("node_retry_count") or 0))
        self.node_retry_delay_spin.setValue(max(10, int(step.get("node_retry_delay") or 250)))
        self.delay_spin.setValue(int(step.get("sleep_after") or 0))

    def refresh(self, select_name: str | None = None) -> None:
        self.action_editor.refresh_sources()
        previous = select_name or self.current_name
        scroll_value = self.macro_list.verticalScrollBar().value()
        summaries = self.repository.list_macros()
        tags = self.repository.load_macro_tags()
        groups: dict[str, list[Any]] = {}
        for summary in summaries:
            groups.setdefault(tags.get(summary.name, "").strip() or "미분류", []).append(summary)
        known_groups = [group for group in groups if group != "미분류"]
        self.group_combo.blockSignals(True)
        current_group = self.group_combo.currentData()
        self.group_combo.clear()
        self.group_combo.addItem("미분류", "")
        for group in known_groups:
            self.group_combo.addItem(group, group)
        self.group_combo.addItem("＋ 새 폴더…", "__new__")
        current_index = self.group_combo.findData(current_group)
        self.group_combo.setCurrentIndex(max(0, current_index))
        self.group_combo.blockSignals(False)
        self.macro_list.blockSignals(True)
        self.macro_list.clear()
        ordered_groups = (["미분류"] if "미분류" in groups else []) + [group for group in known_groups]
        for group in ordered_groups:
            entries = groups[group]
            arrow = "▸" if group in self._collapsed_groups else "▾"
            heading = QtWidgets.QListWidgetItem(f"{arrow}  📁  {group}  ·  {len(entries)}")
            heading.setData(QtCore.Qt.UserRole + 2, group)
            heading.setData(QtCore.Qt.UserRole + 3, "group_header")
            heading.setFlags(QtCore.Qt.ItemIsEnabled)
            heading.setToolTip("클릭해서 폴더 접기/펼치기")
            heading.setForeground(QtGui.QColor(COLORS["accent"]))
            heading.setBackground(QtGui.QColor("#171B25"))
            self.macro_list.addItem(heading)
            for summary in entries:
                item = QtWidgets.QListWidgetItem(f"    {summary.name}\n    {summary.steps}단계")
                item.setData(QtCore.Qt.UserRole, summary.name)
                item.setData(QtCore.Qt.UserRole + 1, summary.description)
                item.setData(QtCore.Qt.UserRole + 2, group)
                self.macro_list.addItem(item)
                if summary.name == previous:
                    self.macro_list.setCurrentItem(item)
        self.macro_list.blockSignals(False)
        self._filter_macros(self.search_edit.text())
        if previous:
            match = self._find_macro_item(previous)
            if match is not None:
                self.macro_list.setCurrentItem(match)
                self._select_macro(match, None)
                QtCore.QTimer.singleShot(0, lambda value=scroll_value: self.macro_list.verticalScrollBar().setValue(value))
                return
        first = next((self.macro_list.item(index) for index in range(self.macro_list.count()) if self.macro_list.item(index).data(QtCore.Qt.UserRole)), None)
        if first is not None:
            self.macro_list.setCurrentItem(first)
            self._select_macro(first, None)
        else:
            self._clear_editor()
        QtCore.QTimer.singleShot(0, lambda value=scroll_value: self.macro_list.verticalScrollBar().setValue(value))

    def _find_macro_item(self, name: str) -> QtWidgets.QListWidgetItem | None:
        for index in range(self.macro_list.count()):
            item = self.macro_list.item(index)
            if str(item.data(QtCore.Qt.UserRole) or "") == name:
                return item
        return None

    def _selected_macro_names(self) -> list[str]:
        names = [str(item.data(QtCore.Qt.UserRole) or "") for item in self.macro_list.selectedItems()]
        return [name for name in names if name]

    def _assign_selected_group(self) -> None:
        names = self._selected_macro_names()
        if not names and self.current_name:
            names = [self.current_name]
        if not names:
            self.status.emit("폴더를 지정할 매크로를 선택하세요.")
            return
        group = str(self.group_combo.currentData() or "")
        if group == "__new__":
            group, accepted = QtWidgets.QInputDialog.getText(self, "새 매크로 폴더", "폴더 이름")
            if not accepted:
                return
            group = group.strip()
        self.repository.assign_macro_group(names, group)
        self.refresh(self.current_name or names[0])
        self.status.emit(f"{len(names)}개 매크로를 '{group or '미분류'}' 폴더로 이동했습니다.")

    def _toggle_macro_group(self, item: QtWidgets.QListWidgetItem) -> None:
        if item.data(QtCore.Qt.UserRole + 3) != "group_header":
            return
        group = str(item.data(QtCore.Qt.UserRole + 2) or "미분류")
        if group in self._collapsed_groups:
            self._collapsed_groups.remove(group)
        else:
            self._collapsed_groups.add(group)
        self._filter_macros(self.search_edit.text())

    def _set_all_groups_collapsed(self, collapsed: bool) -> None:
        groups = {
            str(self.macro_list.item(index).data(QtCore.Qt.UserRole + 2) or "미분류")
            for index in range(self.macro_list.count())
            if self.macro_list.item(index).data(QtCore.Qt.UserRole + 3) == "group_header"
        }
        self._collapsed_groups = groups if collapsed else set()
        self._filter_macros(self.search_edit.text())

    def _filter_macros(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.macro_list.count()):
            item = self.macro_list.item(index)
            name = str(item.data(QtCore.Qt.UserRole) or "")
            if not name:
                continue
            haystack = f"{name} {item.data(QtCore.Qt.UserRole + 1)} {item.data(QtCore.Qt.UserRole + 2)}".casefold()
            group = str(item.data(QtCore.Qt.UserRole + 2) or "미분류")
            matches = not query or query in haystack
            item.setHidden(not matches or (not query and group in self._collapsed_groups))
        for index in range(self.macro_list.count()):
            heading = self.macro_list.item(index)
            if heading.data(QtCore.Qt.UserRole):
                continue
            group = str(heading.data(QtCore.Qt.UserRole + 2) or "미분류")
            arrow = "▸" if group in self._collapsed_groups and not query else "▾"
            count_text = heading.text().split("📁", 1)[-1].strip()
            heading.setText(f"{arrow}  📁  {count_text}")
            visible_child = False
            child_index = index + 1
            while child_index < self.macro_list.count() and self.macro_list.item(child_index).data(QtCore.Qt.UserRole):
                visible_child = visible_child or not self.macro_list.item(child_index).isHidden()
                child_index += 1
            # 접힌 폴더는 하위 항목이 보이지 않아도 헤더 자체는 항상 남깁니다.
            # 검색 중에만 일치하는 하위 항목이 없는 폴더를 숨깁니다.
            heading.setHidden(bool(query and not visible_child))

    def _select_macro(self, current, _previous) -> None:
        if not current or not current.data(QtCore.Qt.UserRole):
            return
        name = str(current.data(QtCore.Qt.UserRole))
        try:
            self.current_macro = self.repository.load_macro(name)
        except Exception as exc:
            self.status.emit(str(exc))
            return
        self.current_name = name
        self._last_persisted_macro = deepcopy(self.current_macro)
        self._update_history_buttons()
        channel = str((self.current_macro.get("meta") or {}).get("release_channel") or "test")
        channel_label = "안정" if channel == "stable" else "테스트"
        self.macro_title.setText(f"{name}  ·  {len(self.current_macro.get('steps') or [])}단계  ·  {channel_label}")
        self._refresh_steps()

    def _refresh_steps(self, selected: int = 0) -> None:
        steps = list((self.current_macro or {}).get("steps") or [])
        self.steps_table.blockSignals(True)
        self.steps_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            values = [
                str(row + 1),
                str(step.get("action") or ""),
                str(step.get("label") or self._step_summary(step)),
                str(step.get("on_success") or "자동"),
                str(step.get("on_fail") or "자동"),
            ]
            for column, value in enumerate(values):
                self.steps_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self.steps_table.blockSignals(False)
        if steps:
            selected = max(0, min(selected, len(steps) - 1))
            self.steps_table.selectRow(selected)
            self._load_step(selected)
        else:
            self.json_edit.clear()
        previews: dict[str, str] = {}
        for alias in self.repository.load_assets():
            path = self.repository.asset_path(alias)
            if path is not None:
                previews[str(alias)] = str(path)
        self.node_canvas.set_asset_previews(previews)
        self.node_canvas.set_macro(self.current_macro, selected + 1 if steps else 0)

    @staticmethod
    def _step_summary(step: dict[str, Any]) -> str:
        action = step.get("action")
        if action in {"image_search", "screen_condition"}:
            assets = step.get("assets") if isinstance(step.get("assets"), list) else []
            return f"멀티 이미지 서치 · {len(assets)}개" if len(assets) > 1 else str(step.get("asset") or "이미지 선택 필요")
        if action == "datetime_condition":
            time_text = str(step.get("time_start") or "00:00")
            if bool(step.get("time_end_enabled", "time_end" in step)):
                time_text += f"~{step.get('time_end') or '23:59'}"
            wait_text = " · 조건까지 대기" if step.get("wait_until") else ""
            return f"{time_text} · 날짜·시간 조건{wait_text}"
        if action == "type_text":
            text = str(step.get("text") or "")
            return text[:30] or "텍스트 입력"
        if action == "browser_action":
            return str(step.get("selector") or step.get("title") or "브라우저 액션")
        if action in {"table_copy", "table_paste"}:
            return str(step.get("table") or "테이블 선택 필요")
        if action == "call_submacro":
            inputs = step.get("inputs") if isinstance(step.get("inputs"), dict) else {}
            outputs = step.get("outputs") if isinstance(step.get("outputs"), dict) else {}
            signature = ", ".join(inputs) if inputs else "입력 없음"
            result = str(step.get("result_var") or "").strip()
            suffix = f" → {result}" if result else (f" → 출력 {len(outputs)}개" if outputs else "")
            return f"서브플로우 · {step.get('macro') or '선택 필요'}({signature}){suffix}"
        return str(action or "단계")

    def _select_step(self, row: int, _column: int, _old_row: int, _old_column: int) -> None:
        if row >= 0:
            self._load_step(row)

    def _load_step(self, row: int) -> None:
        steps = list((self.current_macro or {}).get("steps") or [])
        if not 0 <= row < len(steps):
            return
        step = deepcopy(steps[row])
        self._loading = True
        index = self.inspector_action.findData(str(step.get("action") or "wait"))
        self.inspector_action.setCurrentIndex(max(index, 0))
        self._load_common_fields(step)
        self.action_editor.load_step(step)
        self._update_action_summary(step)
        self.json_edit.setPlainText(json.dumps(step, ensure_ascii=False, indent=2))
        self._loading = False
        self.node_canvas.select_node(row + 1)

    @QtCore.Slot(int)
    def _select_graph_node(self, index: int) -> None:
        row = index - 1
        steps = list((self.current_macro or {}).get("steps") or [])
        if not 0 <= row < len(steps):
            return
        self.steps_table.selectRow(row)
        self._load_step(row)

    @QtCore.Slot(int)
    def _focus_inspector(self, index: int) -> None:
        if hasattr(self, "inspector_panel") and not self.inspector_panel.isVisible():
            self._toggle_builder_side("inspector", True)
        self._select_graph_node(index)
        steps = list((self.current_macro or {}).get("steps") or [])
        if 1 <= index <= len(steps) and str(steps[index - 1].get("action") or "") == "call_submacro":
            target = str(steps[index - 1].get("macro") or "").strip()
            if not target or not self.repository.macro_path(target).is_file():
                QtWidgets.QMessageBox.warning(self, "서브플로우 열기", "호출할 서브매크로가 없거나 파일을 찾을 수 없습니다.")
                return
            if self.current_name:
                self._subflow_parent_stack.append((self.current_name, index))
            self.refresh(target)
            self.subflow_back_button.setVisible(True)
            self.status.emit(f"'{target}' 서브플로우 내부를 열었습니다. 상위 흐름 버튼으로 돌아갈 수 있습니다.")
            return
        self.label_edit.setFocus(QtCore.Qt.MouseFocusReason)

    def _toggle_builder_side(self, side: str, force_open: bool = False) -> None:
        if not hasattr(self, "builder_splitter"):
            return
        is_macro = side == "macro"
        panel = self.macro_panel if is_macro else self.inspector_panel
        button = self.macro_panel_toggle if is_macro else self.inspector_panel_toggle
        visible = panel.isVisibleTo(self) and panel.width() > 0
        if force_open or not visible:
            panel.show()
            sizes = self.builder_splitter.sizes()
            preferred = 230 if is_macro else 360
            index = 0 if is_macro else 2
            if len(sizes) == 3:
                sizes[index] = preferred
                sizes[1] = max(480, sizes[1] - preferred)
                self.builder_splitter.setSizes(sizes)
            button.setText("◀ 목록" if is_macro else "설정 ▶")
            return
        panel.hide()
        button.setText("목록 ▶" if is_macro else "◀ 설정")

    def _leave_subflow(self) -> None:
        if not self._subflow_parent_stack:
            self.subflow_back_button.setVisible(False)
            return
        parent_name, node_index = self._subflow_parent_stack.pop()
        self.refresh(parent_name)
        self._select_graph_node(node_index)
        self.subflow_back_button.setVisible(bool(self._subflow_parent_stack))
        self.status.emit(f"'{parent_name}' 상위 흐름으로 돌아왔습니다.")

    @QtCore.Slot(dict)
    def _graph_positions_changed(self, positions: dict) -> None:
        if self.current_macro is None:
            return
        self.current_macro["graph_positions"] = positions
        self._graph_save_timer.start()

    @QtCore.Slot(dict)
    def _graph_routes_changed(self, routes: dict) -> None:
        if self.current_macro is None:
            return
        if routes:
            self.current_macro["graph_routes"] = routes
        else:
            self.current_macro.pop("graph_routes", None)
        self._graph_save_timer.start()

    def _save_graph_positions(self) -> None:
        if not self.current_name or self.current_macro is None:
            return
        self._persist("노드 위치를 저장했습니다.")

    @QtCore.Slot(int, int, str)
    def _connect_graph_nodes(self, source: int, target: int, kind: str) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not (0 < source <= len(steps) and 0 < target <= len(steps)):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        delay_field = "on_fail_delay" if kind == "fail" else "on_success_delay"
        steps[source - 1][field] = target
        steps[source - 1].setdefault(delay_field, 300)
        self._persist(f"{source}번 노드의 {'실패' if kind == 'fail' else '성공'} 흐름을 {target}번에 연결했습니다.")
        self._refresh_steps(source - 1)

    @QtCore.Slot(int, int, str)
    def _delete_graph_edge(self, source: int, target: int, kind: str) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        delay_field = "on_fail_delay" if kind == "fail" else "on_success_delay"
        if int(steps[source - 1].get(field) or 0) != target:
            return
        steps[source - 1].pop(field, None)
        steps[source - 1].pop(delay_field, None)
        self._persist(f"{source}번 노드의 연결을 끊었습니다.")
        self._refresh_steps(source - 1)

    @QtCore.Slot(int, int, str)
    def _set_graph_edge_delay(self, source: int, target: int, kind: str) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        if int(steps[source - 1].get(field) or 0) != target:
            return
        delay_field = "on_fail_delay" if kind == "fail" else "on_success_delay"
        step = steps[source - 1]
        current = int(step.get(delay_field) or 0)
        rules = [rule for rule in (step.get("edge_conditions") or []) if isinstance(rule, dict) and str(rule.get("kind") or "success") == kind]
        other_rules = [rule for rule in (step.get("edge_conditions") or []) if not isinstance(rule, dict) or str(rule.get("kind") or "success") != kind]
        dialog = EdgeSettingsDialog(len(steps), kind, current, rules, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        if dialog.delay_spin.value():
            step[delay_field] = dialog.delay_spin.value()
        else:
            step.pop(delay_field, None)
        combined = other_rules + dialog.rules
        if combined:
            step["edge_conditions"] = combined
        else:
            step.pop("edge_conditions", None)
        self._persist("노드라인 딜레이와 조건 분기를 저장했습니다.")
        self._refresh_steps(source - 1)

    @QtCore.Slot(int, int)
    def _delete_graph_condition(self, source: int, condition_index: int) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        rules = steps[source - 1].get("edge_conditions") or []
        if not isinstance(rules, list) or not 0 <= condition_index < len(rules):
            return
        rules.pop(condition_index)
        if not rules:
            steps[source - 1].pop("edge_conditions", None)
        self._persist("조건 분기 노드라인을 제거했습니다.")
        self.node_canvas.rebuild_edges()

    @QtCore.Slot(int, int, int)
    def _retarget_graph_condition(self, source: int, condition_index: int, target: int) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        rules = steps[source - 1].get("edge_conditions") or []
        if isinstance(rules, list) and 0 <= condition_index < len(rules) and isinstance(rules[condition_index], dict):
            rules[condition_index]["target"] = target
            self._persist(f"조건 분기를 {target}번 노드로 다시 연결했습니다.")
            self.node_canvas.rebuild_edges()

    @QtCore.Slot(int)
    def _delete_node_from_graph(self, index: int) -> None:
        if index <= 0:
            self.status.emit("보관할 노드를 선택하세요.")
            return
        selected = self.node_canvas.selected_indexes()
        self._delete_nodes(selected if index in selected else [index])

    def _delete_nodes(self, indexes: list[int]) -> bool:
        if self.current_macro is None:
            return False
        steps = self.current_macro.get("steps") or []
        valid = sorted({index for index in indexes if 0 < index <= len(steps)}, reverse=True)
        if not valid:
            return False
        for index in valid:
            removed = steps.pop(index - 1)
            self.current_macro.setdefault("meta", {}).setdefault("archived_steps", []).append(removed)
            self._normalize_edges_after_delete(index)
        self._persist(f"{len(valid)}개 노드를 매크로 내부 보관함으로 이동했습니다.")
        self._refresh_steps(min(valid[-1] - 1, len(steps) - 1))
        return True

    @QtCore.Slot(int)
    def _duplicate_node_from_graph(self, index: int) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < index <= len(steps):
            self.status.emit("복제할 노드를 선택하세요.")
            return
        clone = deepcopy(steps[index - 1])
        clone.pop("on_success", None)
        clone.pop("on_fail", None)
        clone.pop("on_success_delay", None)
        clone.pop("on_fail_delay", None)
        clone.pop("edge_conditions", None)
        clone["label"] = (str(clone.get("label") or self._step_summary(clone)) + " 복사본").strip()
        steps.append(clone)
        positions = self.current_macro.setdefault("graph_positions", {})
        source_pos = positions.get(str(index)) if isinstance(positions, dict) else None
        if isinstance(source_pos, (list, tuple)) and len(source_pos) >= 2:
            positions[str(len(steps))] = [float(source_pos[0]) + 40, float(source_pos[1]) + 140]
        self._persist(f"{index}번 노드를 복제했습니다.")
        self._refresh_steps(len(steps) - 1)

    @QtCore.Slot(list)
    def _set_selected_wait_durations(self, indexes: list[int]) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        wait_indexes = sorted(
            {
                int(index)
                for index in indexes
                if 0 < int(index) <= len(steps) and steps[int(index) - 1].get("action") == "wait"
            }
        )
        if not wait_indexes:
            self.status.emit("시간을 변경할 대기 노드를 선택하세요.")
            return
        current = int(steps[wait_indexes[0] - 1].get("duration") or 500)
        value, accepted = QtWidgets.QInputDialog.getInt(
            self,
            "선택한 대기시간 변경",
            f"선택한 대기 노드 {len(wait_indexes)}개의 시간을 같은 값으로 변경합니다.\n대기시간 (ms)",
            current,
            0,
            3_600_000,
            100,
        )
        if not accepted:
            return
        for index in wait_indexes:
            steps[index - 1]["duration"] = int(value)
        self._persist(f"선택한 대기 노드 {len(wait_indexes)}개의 시간을 {value} ms로 변경했습니다.")
        self._refresh_steps(wait_indexes[-1] - 1)

    @QtCore.Slot()
    def _set_all_wait_durations(self) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        wait_indexes = [index for index, step in enumerate(steps, start=1) if step.get("action") == "wait"]
        if not wait_indexes:
            self.status.emit("현재 매크로에 대기 노드가 없습니다.")
            return
        self._set_selected_wait_durations(wait_indexes)

    def _set_graph_marker(self, kind: str) -> None:
        if self.current_macro is None:
            return
        index = self.node_canvas.selected_index()
        if not index:
            self.status.emit("노드를 먼저 선택하세요.")
            return
        key = "graph_start_step" if kind == "start" else "graph_end_step"
        self.current_macro[key] = index
        self._persist(f"{index}번 노드를 {'시작' if kind == 'start' else '종료'} 노드로 지정했습니다.")
        self._refresh_steps(index - 1)

    def _change_action_template(self, _index: int) -> None:
        action = self._selected_action(self.inspector_action)
        if action:
            self.select_action(action)
        if self._loading or not action:
            return
        current = self._build_form_payload()
        payload = deepcopy(ACTION_TEMPLATES.get(action, {"action": action}))
        payload["action"] = action
        for key in ("label", "repeat", "repeat_var", "on_success", "on_fail", "on_success_delay", "on_fail_delay", "sleep_after"):
            if key in current:
                payload[key] = current[key]
        self.action_editor.load_step(payload)
        self._update_action_summary(payload)
        self.json_edit.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _save_step(self) -> None:
        if not self.current_macro:
            return
        row = self.steps_table.currentRow()
        steps = self.current_macro.get("steps") or []
        if not 0 <= row < len(steps):
            return
        payload = self._build_form_payload()
        steps[row] = payload
        self._persist(f"{row + 1}번 단계를 저장했습니다.")
        self._refresh_steps(row)

    def _add_step(self) -> None:
        if not self.current_macro:
            self.status.emit("먼저 매크로를 선택하세요.")
            return
        action = self._selected_action(self.action_combo)
        step = deepcopy(ACTION_TEMPLATES.get(action, {}))
        step["action"] = action
        steps = self.current_macro.setdefault("steps", [])
        source = self.node_canvas.selected_index()
        if not source:
            row = self.steps_table.currentRow()
            source = row + 1 if 0 <= row < len(steps) else len(steps)
        source = source if 0 < source <= len(steps) else 0
        new_index = len(steps) + 1
        previous_target = 0
        if source and steps[source - 1].get("action") != "flow_control":
            previous_target = int(steps[source - 1].get("on_success") or 0)
            steps[source - 1]["on_success"] = new_index
            if previous_target and previous_target != new_index:
                step["on_success"] = previous_target
        steps.append(step)
        positions = self.current_macro.setdefault("graph_positions", {})
        if source and isinstance(positions, dict):
            source_position = positions.get(str(source))
            target_position = positions.get(str(previous_target)) if previous_target else None
            if isinstance(source_position, (list, tuple)) and len(source_position) >= 2:
                source_x, source_y = float(source_position[0]), float(source_position[1])
                if isinstance(target_position, (list, tuple)) and len(target_position) >= 2:
                    new_x = (source_x + float(target_position[0])) / 2.0
                    new_y = (source_y + float(target_position[1])) / 2.0
                else:
                    new_x, new_y = source_x + 350.0, source_y
                positions[str(new_index)] = [round(new_x, 2), round(new_y, 2)]
        if source:
            relation = "삽입" if previous_target else "연결"
            message = f"{new_index}번 {action} 노드를 {source}번 성공 흐름에 자동 {relation}했습니다."
        else:
            message = f"{action} 단계를 추가했습니다."
        self._persist(message)
        self._refresh_steps(len(steps) - 1)

    @QtCore.Slot(list)
    def _configure_start_search_candidates(self, indexes: list[int] | None = None) -> None:
        if self.current_macro is None:
            return
        steps = self.current_macro.get("steps") or []
        requested = indexes if isinstance(indexes, list) else self.node_canvas.selected_indexes()
        candidate_set: set[int] = set()
        for value in requested:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < index <= len(steps) and steps[index - 1].get("action") in {"image_search", "ocr"}:
                candidate_set.add(index)
        candidates = sorted(candidate_set)
        if len(candidates) < 2:
            self.status.emit("분기 후보로 사용할 이미지 서치·OCR 노드를 2개 이상 선택하세요.")
            return
        previous_candidates = self.current_macro.get("start_search_candidates") or []
        if isinstance(previous_candidates, list):
            previous_order: list[int] = []
            for value in previous_candidates:
                try:
                    old_index = int(value)
                except (TypeError, ValueError):
                    continue
                previous_order.append(old_index)
                if not 0 < old_index <= len(steps):
                    continue
                old_step = steps[old_index - 1]
                old_step.pop("stop_on_success", None)
                automation = old_step.get("_automation")
                if isinstance(automation, dict):
                    for key in ("start_search_group", "candidate_position", "candidate_count"):
                        automation.pop(key, None)
                    if not automation:
                        old_step.pop("_automation", None)
            for position, old_index in enumerate(previous_order[:-1]):
                if 0 < old_index <= len(steps) and int(steps[old_index - 1].get("on_fail") or 0) == previous_order[position + 1]:
                    steps[old_index - 1].pop("on_fail", None)
        group_id = f"start-search-{candidates[0]}-{len(candidates)}"
        for position, index in enumerate(candidates, start=1):
            step = steps[index - 1]
            step["abort_on_fail"] = True
            step["stop_on_success"] = True
            step.pop("on_fail_delay", None)
            if position < len(candidates):
                step["on_fail"] = candidates[position]
            else:
                step.pop("on_fail", None)
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            automation.update(
                {
                    "start_search_group": group_id,
                    "candidate_position": position,
                    "candidate_count": len(candidates),
                }
            )
            step["_automation"] = automation
        self.current_macro["start_search_candidates"] = candidates
        self.current_macro["graph_start_step"] = candidates[0]
        self._persist(
            f"검색·인식 {', '.join(map(str, candidates))}번을 분기 후보 그룹으로 묶었습니다. 성공 흐름은 각각 독립 실행됩니다."
        )
        self._refresh_steps(candidates[0] - 1)

    def _append_automation_steps(self, new_steps: list[dict[str, Any]], message: str) -> None:
        if not self.current_macro or not new_steps:
            return
        steps = self.current_macro.setdefault("steps", [])
        if len(steps) == 1 and isinstance(steps[0], dict) and _is_unconfigured_template_step(steps[0]):
            steps.clear()
            self.current_macro["graph_positions"] = {}
            self.current_macro.pop("graph_routes", None)
        base = len(steps)
        prepared = deepcopy(new_steps)
        for step in prepared:
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                target = int(step.get(field) or 0)
                if target:
                    step[field] = target + base
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                for rule in conditions:
                    if isinstance(rule, dict) and int(rule.get("target") or 0):
                        rule["target"] = int(rule["target"]) + base
        if steps and prepared:
            previous = steps[-1]
            if isinstance(previous, dict) and previous.get("action") != "flow_control" and not int(previous.get("on_success") or 0):
                previous["on_success"] = base + 1
        steps.extend(prepared)
        self.current_macro.setdefault("meta", {})["automation_test_version"] = "2.19.17-test"
        # Image-mode recording may have created assets moments ago. Refresh
        # the inspector before loading the generated step so its asset is not
        # replaced with the empty first row by a stale combo box.
        self.action_editor.refresh_sources()
        self._persist(message)
        self._refresh_steps(len(steps) - len(prepared))

    def _start_smart_recording(self) -> None:
        if not self.current_macro:
            self.status.emit("녹화한 노드를 추가할 매크로를 먼저 선택하세요.")
            return
        if self._recording_controller is not None:
            self.status.emit("다른 녹화가 이미 실행 중입니다.")
            return
        if not self._confirm_smart_recording():
            return
        controller = SmartRecordingController(self.repository, self.window())
        controller.completed.connect(self._review_smart_recording)
        controller.failed.connect(self._smart_recording_failed)
        self._recording_controller = controller
        controller.start()
        self.status.emit("스마트 녹화 준비 · ` 기록 ON/OFF · Shift+` 일반/분기 모드 · F8 캡처 · F10 종료")

    @staticmethod
    def _recording_notice_hidden_today() -> bool:
        today = QtCore.QDate.currentDate().toString(QtCore.Qt.ISODate)
        hidden_date = str(
            QtCore.QSettings("MacroRelay", "Studio").value("smart_recording/hide_notice_date", "") or ""
        )
        return hidden_date == today

    def _confirm_smart_recording(self) -> bool:
        if self._recording_notice_hidden_today():
            return True
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle("스마트 동작 녹화")
        message.setIcon(QtWidgets.QMessageBox.Question)
        message.setText(
            "2초 뒤 녹화가 준비됩니다. 단독 ` 키를 한 번 누르면 기록이 켜지고 다시 누르면 일시정지됩니다.\n"
            "Shift+` 키는 일반 액션과 순차 이미지 분기 후보 모드를 전환합니다.\n"
            "분기 모드의 클릭·키 입력·텍스트도 분기 액션으로 저장되며, F8 이미지는 대체 후보로 추가됩니다.\n"
            "`과 Shift+` 키 자체는 대상 프로그램에 입력되거나 매크로 동작으로 저장되지 않습니다.\n"
            "암호·개인정보를 입력했다면 검토 화면에서 텍스트 저장을 해제하세요.\n\n"
            "녹화를 시작할까요? 시작 F9 · 종료 F10입니다."
        )
        message.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        message.setDefaultButton(QtWidgets.QMessageBox.Yes)
        hide_today = QtWidgets.QCheckBox("오늘 하루 다시 표시하지 않기")
        message.setCheckBox(hide_today)
        accepted = message.exec() == QtWidgets.QMessageBox.Yes
        if accepted and hide_today.isChecked():
            QtCore.QSettings("MacroRelay", "Studio").setValue(
                "smart_recording/hide_notice_date",
                QtCore.QDate.currentDate().toString(QtCore.Qt.ISODate),
            )
        return accepted

    @QtCore.Slot(list)
    def _review_smart_recording(self, events: list[dict[str, Any]]) -> None:
        self._recording_controller = None
        events = self._apply_saved_handle_profiles(events)
        self._remember_last_recording(events)
        dialog = RecordingReviewDialog(events, self.repository, self.window())
        dialog.events_changed.connect(self._remember_last_recording)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            self.status.emit("검토창을 닫았습니다. '최근 녹화 검토'에서 다시 열 수 있습니다.")
            return
        try:
            steps = dialog.build_steps()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "녹화 변환 실패", str(exc))
            return
        self._append_automation_steps(steps, f"스마트 녹화에서 노드 {len(steps)}개를 추가했습니다.")
        self.data_changed.emit()

    def _load_last_recording(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self._last_recording_path.read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _remember_last_recording(self, events: list[dict[str, Any]]) -> None:
        self._last_recording_events = deepcopy(events)
        self.review_recording_btn.setEnabled(bool(events))
        try:
            self._last_recording_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._last_recording_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._last_recording_path)
        except OSError:
            pass

    def _open_last_recording_review(self) -> None:
        events = self._last_recording_events or self._load_last_recording()
        if not events:
            self.status.emit("다시 검토할 스마트 녹화 기록이 없습니다.")
            return
        self._review_smart_recording(deepcopy(events))

    def _load_inactive_handle_profiles(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self._handle_profiles_path.read_text(encoding="utf-8-sig"))
        except (OSError, TypeError, ValueError):
            return []
        profiles = payload.get("profiles") if isinstance(payload, dict) else payload
        return [dict(item) for item in profiles if isinstance(item, dict)] if isinstance(profiles, list) else []

    def _save_inactive_handle_profile(self, profile: dict[str, Any]) -> None:
        clean = dict(profile)
        exe = str(clean.get("window_exe") or "").strip().casefold()
        root_class = str(clean.get("target_root_class") or "").strip().casefold()
        remaining = [
            item
            for item in self._inactive_handle_profiles
            if not (
                str(item.get("window_exe") or "").strip().casefold() == exe
                and str(item.get("target_root_class") or "").strip().casefold() == root_class
            )
        ]
        remaining.append(clean)
        self._inactive_handle_profiles = remaining
        self._handle_profiles_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._handle_profiles_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"profiles": self._inactive_handle_profiles}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._handle_profiles_path)

    @staticmethod
    def _event_window_token(window: dict[str, Any]) -> str:
        exe = str(window.get("exe") or "").strip()
        title = str(window.get("title") or "").strip()
        window_class = str(window.get("class") or "").strip()
        if title and exe:
            return f"{title} ahk_exe {exe}"
        if window_class and exe:
            return f"ahk_class {window_class} ahk_exe {exe}"
        return f"ahk_exe {exe}" if exe else title or "A"

    def _matching_inactive_handle_profile(self, window: dict[str, Any]) -> dict[str, Any]:
        exe = str(window.get("exe") or "").strip().casefold()
        if not exe:
            return {}
        candidates = [
            item
            for item in self._inactive_handle_profiles
            if str(item.get("window_exe") or "").strip().casefold() == exe
        ]
        if not candidates:
            return {}
        window_class = str(window.get("class") or "").strip().casefold()
        exact = [
            item
            for item in candidates
            if window_class and str(item.get("target_root_class") or "").strip().casefold() == window_class
        ]
        compatible = [
            item
            for item in candidates
            if not window_class
            or not str(item.get("target_root_class") or "").strip()
            or str(item.get("target_root_class") or "").strip().casefold() == window_class
        ]
        return dict((exact or compatible)[-1]) if exact or compatible else {}

    def _apply_saved_handle_profiles(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = deepcopy(events)
        for event in prepared:
            if event.get("type") not in {"mouse", "capture"} or isinstance(event.get("_handle_profile"), dict):
                continue
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            profile = self._matching_inactive_handle_profile(window)
            if not profile:
                continue
            profile["window"] = self._event_window_token(window)
            profile["window_exe"] = str(window.get("exe") or profile.get("window_exe") or "")
            profile["x"] = int(event.get("client_x") or 0)
            profile["y"] = int(event.get("client_y") or 0)
            event["_handle_profile"] = profile
        return prepared

    def _open_inactive_handle_lab(self) -> bool:
        host = self.window()
        was_visible = host.isVisible()
        ignored = {int(widget.winId()) for widget in QtWidgets.QApplication.topLevelWidgets()}
        host.hide()
        wait_loop = QtCore.QEventLoop(self)
        QtCore.QTimer.singleShot(140, wait_loop.quit)
        wait_loop.exec()
        picker = HandlePointPicker(ignored_hwnds=ignored)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted and picker.probe_result is not None
        if was_visible:
            host.show()
            host.raise_()
            host.activateWindow()
        if not accepted or picker.probe_result is None:
            return False
        lab = InactiveClickLabDialog(picker.probe_result, host)
        if lab.exec() != QtWidgets.QDialog.Accepted:
            return False
        profile = lab.selected_payload()
        if not profile:
            return False
        try:
            self._save_inactive_handle_profile(profile)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "핸들 저장 실패", str(exc))
            return False
        target = str(profile.get("window_exe") or profile.get("window") or "대상 프로그램")
        self.status.emit(f"{target} 비활성 클릭 핸들을 저장했습니다.")
        QtWidgets.QMessageBox.information(
            self,
            "비활성 클릭 핸들 저장",
            f"{target}의 시험 성공 핸들을 저장했습니다.\n이후 같은 프로그램의 스마트 녹화 클릭에 자동 적용됩니다.",
        )
        return True

    def _stop_smart_recording(self) -> None:
        if self._recording_controller is not None:
            self._recording_controller.stop()
            return
        self.status.emit("현재 실행 중인 녹화가 없습니다.")

    def set_shortcut_labels(self, shortcuts: dict[str, str]) -> None:
        for action_id, button in self.shortcut_buttons.items():
            base = str(button.property("shortcutBaseText") or button.text()).split("    ", 1)[0]
            button.setProperty("shortcutBaseText", base)
            sequence = str(shortcuts.get(action_id) or "").strip()
            button.setText(f"{base}    {sequence}" if sequence else base)

    def set_running_step(self, index: int) -> None:
        self.node_canvas.set_active_step(index)
        if index > 0:
            self.status.emit(f"{index}번 노드를 실행하고 있습니다.")

    def set_execution_states(self, states: dict[int, dict[str, Any]]) -> None:
        self.node_canvas.set_execution_states(states)

    def clear_execution_states(self) -> None:
        self.node_canvas.clear_execution_states()

    def set_macro_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.dry_run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _stop_running_macros(self) -> None:
        if not self.stop_button.isEnabled():
            self.status.emit("현재 실행 중인 매크로가 없습니다.")
            return
        self.status.emit("실행 중인 매크로에 정지를 요청했습니다.")
        self.stop_macros.emit()

    @QtCore.Slot(str)
    def _smart_recording_failed(self, detail: str) -> None:
        self._recording_controller = None
        QtWidgets.QMessageBox.warning(self, "스마트 녹화", detail)
        self.status.emit(detail)

    def _quick_action_wizard(self) -> None:
        if not self.current_macro:
            self.status.emit("자동 설정 노드를 추가할 매크로를 먼저 선택하세요.")
            return
        action = self._selected_action(self.action_combo)
        try:
            step = QuickActionWizard.build(action, self.repository, self.window())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "자동 설정 실패", str(exc))
            return
        if step is None:
            return
        self._append_automation_steps([step], f"{ACTION_LABELS.get(action, action)} 자동 설정 노드를 추가했습니다.")
        if action not in {"mouse_click", "inactive_click", "image_search", "type_text", "wait", "run_program"}:
            self.status.emit("기본값으로 추가했습니다. 상세 설정에서 필요한 값을 확인하세요.")

    def _diagnose_automation(self) -> None:
        if not self.current_macro:
            self.status.emit("진단할 매크로를 먼저 선택하세요.")
            return
        issues = AutomationAnalyzer.analyze(self.current_macro, self.repository.load_assets())
        macro_log = self.repository.exports_dir / "macro_log.txt"
        try:
            log_text = macro_log.read_text(encoding="utf-8-sig") if macro_log.is_file() else ""
        except OSError:
            log_text = ""
        issues.extend(AutomationAnalyzer.analyze_runtime_log(self.current_macro, log_text))
        if not issues:
            QtWidgets.QMessageBox.information(self, "자동화 진단", "실행을 막는 문제나 개선 제안을 찾지 못했습니다.")
            return
        dialog = DiagnosticsDialog(issues, self.window())
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            self.status.emit(f"자동화 진단 완료 · 항목 {len(issues)}개")
            return
        fixes = dialog.selected_fixes()
        changed = AutomationAnalyzer.apply_fixes(self.current_macro, fixes)
        if changed:
            self._persist(f"자동화 진단에서 {changed}개 설정을 수정했습니다.")
            self._refresh_steps(self.steps_table.currentRow())
        else:
            self.status.emit("자동으로 수정할 항목이 선택되지 않았습니다.")

    def _test_selected_step(self) -> None:
        if not self.current_macro or not self.current_name:
            self.status.emit("테스트할 매크로와 노드를 먼저 선택하세요.")
            return
        row = self.steps_table.currentRow()
        index = self.node_canvas.selected_index() or (row + 1 if row >= 0 else 0)
        steps = self.current_macro.get("steps") or []
        if not 1 <= index <= len(steps):
            self.status.emit("테스트할 노드를 먼저 선택하세요.")
            return
        if row == index - 1:
            self._save_step()
            steps = (self.current_macro or {}).get("steps") or []
        step = steps[index - 1]
        self.show_click_preview_pending(index)
        self._automation_overlay = AutomationOverlay.show_step(step, self.window())
        self.status.emit(f"{index}번 단계의 대상 위치를 표시한 뒤 단독 실행합니다.")
        QtCore.QTimer.singleShot(450, lambda name=self.current_name, target=index: self.run_macro_step.emit(name, target))

    def show_click_preview_pending(self, step_index: int) -> None:
        self._recent_click_source_pixmap = None
        self._recent_click_description = f"{step_index}번 단계 테스트 · 최종 클릭 좌표 기록 대기"
        self.recent_click_preview_btn.set_preview(None)
        self.recent_click_preview_btn.setToolTip(self._recent_click_description)

    def show_recent_click_preview(self, pixmap: QtGui.QPixmap, x: int, y: int, kind: str) -> None:
        labels = {
            "image": "이미지 서치 클릭",
            "image-inactive": "이미지 서치 비활성 클릭",
            "inactive": "비활성 클릭",
            "foreground": "활성 클릭",
        }
        self._recent_click_source_pixmap = QtGui.QPixmap(pixmap)
        self._recent_click_description = f"{labels.get(kind, '클릭')} · 화면 좌표 X {x}, Y {y} · 십자선 중심"
        self.recent_click_preview_btn.set_preview(self._recent_click_source_pixmap, self._recent_click_description)
        self.recent_click_preview_btn.setToolTip(
            f"{self._recent_click_description}\n커서를 올려 확인하고 클릭하여 고정"
        )

    def show_click_preview_missing(self) -> None:
        self._recent_click_source_pixmap = None
        self._recent_click_description = ""
        self.recent_click_preview_btn.set_preview(None)
        self.recent_click_preview_btn.setToolTip(
            "이번 단계에서 클릭 좌표가 기록되지 않았습니다. 이미지 미탐지 또는 클릭 없는 단계인지 로그를 확인하세요."
        )

    def _save_selected_block(self) -> None:
        if not self.current_macro:
            return
        indexes = self.node_canvas.selected_indexes()
        if not indexes:
            indexes = sorted({item.row() + 1 for item in self.steps_table.selectionModel().selectedRows()})
        if not indexes and self.steps_table.currentRow() >= 0:
            indexes = [self.steps_table.currentRow() + 1]
        steps = self.current_macro.get("steps") or []
        indexes = [index for index in indexes if 1 <= index <= len(steps)]
        if not indexes:
            self.status.emit("블록으로 저장할 노드를 선택하세요.")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "자동화 블록 저장", "블록 이름")
        if not ok or not name.strip():
            return
        description, ok = QtWidgets.QInputDialog.getText(self, "자동화 블록 설명", "설명(선택)")
        if not ok:
            return
        mapping = {old: new for new, old in enumerate(sorted(indexes), start=1)}
        block_steps: list[dict[str, Any]] = []
        for old in sorted(indexes):
            step = deepcopy(steps[old - 1])
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                target = int(step.get(field) or 0)
                if target in mapping:
                    step[field] = mapping[target]
                else:
                    step.pop(field, None)
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                normalized = []
                for rule in conditions:
                    if isinstance(rule, dict) and int(rule.get("target") or 0) in mapping:
                        rule = deepcopy(rule)
                        rule["target"] = mapping[int(rule["target"])]
                        normalized.append(rule)
                if normalized:
                    step["edge_conditions"] = normalized
                else:
                    step.pop("edge_conditions", None)
            block_steps.append(step)
        self.repository.save_automation_block(name, block_steps, description)
        self.status.emit(f"'{name}' 자동화 블록을 저장했습니다. 노드 {len(block_steps)}개")

    def _insert_automation_block(self) -> None:
        if not self.current_macro:
            self.status.emit("블록을 추가할 매크로를 먼저 선택하세요.")
            return
        blocks = self.repository.load_automation_blocks()
        if not blocks:
            self.status.emit("저장된 자동화 블록이 없습니다. 먼저 노드를 선택하고 블록 저장을 누르세요.")
            return
        labels = [
            f"{name} · {len(block.get('steps') or [])}개 노드"
            for name, block in blocks.items()
        ]
        selected, ok = QtWidgets.QInputDialog.getItem(self, "자동화 블록 추가", "블록", labels, 0, False)
        if not ok:
            return
        index = labels.index(selected)
        name = list(blocks)[index]
        block_steps = blocks[name].get("steps") or []
        self._append_automation_steps(block_steps, f"'{name}' 자동화 블록을 추가했습니다.")

    def _remove_step(self) -> None:
        if not self.current_macro:
            return
        row = self.steps_table.currentRow()
        steps = self.current_macro.get("steps") or []
        if not 0 <= row < len(steps):
            return
        removed = steps.pop(row)
        self.current_macro.setdefault("meta", {}).setdefault("archived_steps", []).append(removed)
        self._normalize_edges_after_delete(row + 1)
        self._persist("단계를 매크로 내부 보관함으로 이동했습니다.")
        self._refresh_steps(min(row, len(steps) - 1))

    def _remap_graph_routes(self, mapping: dict[int, int]) -> None:
        """Keep saved manual edge paths attached to their logical nodes."""
        if self.current_macro is None:
            return
        routes = self.current_macro.get("graph_routes") or {}
        if not isinstance(routes, dict):
            self.current_macro.pop("graph_routes", None)
            return
        remapped: dict[str, Any] = {}
        for raw_key, points in routes.items():
            parts = str(raw_key).split(":")
            if len(parts) != 4:
                continue
            try:
                source = mapping[int(parts[0])]
                target = mapping[int(parts[2])]
                condition_index = int(parts[3])
            except (KeyError, TypeError, ValueError):
                continue
            remapped[f"{source}:{parts[1]}:{target}:{condition_index}"] = points

        # Drop paths for links that no longer exist after an edit. This also
        # prevents an old route from being applied to a later, unrelated edge.
        valid_keys: set[str] = set()
        steps = self.current_macro.get("steps") or []
        for source, step in enumerate(steps, start=1):
            for field, kind in (("on_success", "success"), ("on_fail", "fail")):
                target = int(step.get(field) or 0)
                if 1 <= target <= len(steps):
                    valid_keys.add(f"{source}:{kind}:{target}:-1")
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                for condition_index, rule in enumerate(conditions):
                    if not isinstance(rule, dict):
                        continue
                    target = int(rule.get("target") or 0)
                    kind = str(rule.get("kind") or "success")
                    if 1 <= target <= len(steps) and kind in {"success", "fail"}:
                        valid_keys.add(f"{source}:{kind}:{target}:{condition_index}")
        remapped = {key: points for key, points in remapped.items() if key in valid_keys}
        if remapped:
            self.current_macro["graph_routes"] = remapped
        else:
            self.current_macro.pop("graph_routes", None)

    def _normalize_edges_after_delete(self, deleted: int) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        old_count = len(steps) + 1
        for step in steps:
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                value = int(step.get(field) or 0)
                if value == deleted:
                    step.pop(field, None)
                elif value > deleted:
                    step[field] = value - 1
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                normalized_rules = []
                for rule in conditions:
                    if not isinstance(rule, dict):
                        continue
                    target = int(rule.get("target") or 0)
                    if target == deleted:
                        continue
                    if target > deleted:
                        rule["target"] = target - 1
                    normalized_rules.append(rule)
                if normalized_rules:
                    step["edge_conditions"] = normalized_rules
                else:
                    step.pop("edge_conditions", None)
        if self.current_macro is None:
            return
        positions = self.current_macro.get("graph_positions") or {}
        if isinstance(positions, dict):
            normalized: dict[str, Any] = {}
            for key, value in positions.items():
                try:
                    index = int(key)
                except (TypeError, ValueError):
                    continue
                if index == deleted:
                    continue
                normalized[str(index - 1 if index > deleted else index)] = value
            self.current_macro["graph_positions"] = normalized
        for marker in ("graph_start_step", "graph_end_step"):
            value = int(self.current_macro.get(marker) or 0)
            if value == deleted:
                self.current_macro[marker] = 0
            elif value > deleted:
                self.current_macro[marker] = value - 1
        candidates = self.current_macro.get("start_search_candidates") or []
        if isinstance(candidates, list):
            normalized_candidates: list[int] = []
            for value in candidates:
                try:
                    index = int(value)
                except (TypeError, ValueError):
                    continue
                if index != deleted:
                    normalized_candidates.append(index - 1 if index > deleted else index)
            if normalized_candidates:
                self.current_macro["start_search_candidates"] = normalized_candidates
                if not int(self.current_macro.get("graph_start_step") or 0):
                    self.current_macro["graph_start_step"] = normalized_candidates[0]
                for position, index in enumerate(normalized_candidates, start=1):
                    candidate_step = steps[index - 1]
                    if position < len(normalized_candidates):
                        candidate_step["on_fail"] = normalized_candidates[position]
                    else:
                        candidate_step.pop("on_fail", None)
                    automation = candidate_step.get("_automation")
                    if isinstance(automation, dict):
                        automation["candidate_position"] = position
                        automation["candidate_count"] = len(normalized_candidates)
            else:
                self.current_macro.pop("start_search_candidates", None)
        self._remap_graph_routes(
            {
                old_index: old_index - 1 if old_index > deleted else old_index
                for old_index in range(1, old_count + 1)
                if old_index != deleted
            }
        )

    def _move_step(self, direction: int) -> None:
        if not self.current_macro:
            return
        row = self.steps_table.currentRow()
        steps = self.current_macro.get("steps") or []
        target = row + direction
        if not (0 <= row < len(steps) and 0 <= target < len(steps)):
            return
        old_a, old_b = row + 1, target + 1
        steps[row], steps[target] = steps[target], steps[row]
        mapping = {old_a: old_b, old_b: old_a}
        for step in steps:
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                value = int(step.get(field) or 0)
                if value in mapping:
                    step[field] = mapping[value]
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                for rule in conditions:
                    if isinstance(rule, dict):
                        value = int(rule.get("target") or 0)
                        if value in mapping:
                            rule["target"] = mapping[value]
        positions = self.current_macro.get("graph_positions") or {}
        if isinstance(positions, dict):
            first = positions.pop(str(old_a), None)
            second = positions.pop(str(old_b), None)
            if first is not None:
                positions[str(old_b)] = first
            if second is not None:
                positions[str(old_a)] = second
        for marker in ("graph_start_step", "graph_end_step"):
            value = int(self.current_macro.get(marker) or 0)
            if value in mapping:
                self.current_macro[marker] = mapping[value]
        candidates = self.current_macro.get("start_search_candidates") or []
        if isinstance(candidates, list):
            self.current_macro["start_search_candidates"] = [mapping.get(int(value), int(value)) for value in candidates]
        self._remap_graph_routes(
            {index: mapping.get(index, index) for index in range(1, len(steps) + 1)}
        )
        self._persist("단계 순서를 변경했습니다.")
        self._refresh_steps(target)

    def _connect_sequentially(self) -> None:
        if not self.current_macro:
            return
        steps = self.current_macro.get("steps") or []
        for index, step in enumerate(steps):
            if index < len(steps) - 1:
                step["on_success"] = index + 2
            else:
                step.pop("on_success", None)
        self._persist("모든 단계를 순서대로 연결했습니다.")
        self._refresh_steps(self.steps_table.currentRow())

    def _persist(self, message: str) -> None:
        if not self.current_name or self.current_macro is None:
            return
        current = deepcopy(self.current_macro)
        previous = deepcopy(self._last_persisted_macro) if self._last_persisted_macro is not None else None
        if not self._history_suspended and previous is not None and previous != current:
            history = self._undo_history.setdefault(self.current_name, [])
            if not history or history[-1] != previous:
                history.append(previous)
                del history[:-50]
            self._redo_history.setdefault(self.current_name, []).clear()
        self.repository.save_macro(self.current_name, self.current_macro)
        self._last_persisted_macro = current
        self.macro_title.setText(f"{self.current_name}  ·  {len(self.current_macro.get('steps') or [])}단계")
        regressions = [item for item in run_test_cases(self.current_macro) if not item.passed]
        if regressions:
            self.status.emit(f"{message} · 회귀 테스트 {len(regressions)}개 실패 — 테스트 케이스를 확인하세요.")
        else:
            self.status.emit(message)
        self.edit_committed.emit()
        self._update_history_buttons()
        self._data_change_timer.start()

    def _update_history_buttons(self) -> None:
        if not hasattr(self, "undo_button"):
            return
        self.undo_button.setEnabled(bool(self._undo_history.get(self.current_name)))
        self.redo_button.setEnabled(bool(self._redo_history.get(self.current_name)))

    def undo_edit(self) -> bool:
        if not self.current_name or self.current_macro is None:
            return False
        history = self._undo_history.setdefault(self.current_name, [])
        if not history:
            self.status.emit("실행 취소할 매크로 편집 기록이 없습니다.")
            return False
        selected = max(0, self.steps_table.currentRow())
        self._redo_history.setdefault(self.current_name, []).append(deepcopy(self.current_macro))
        self.current_macro = history.pop()
        self.repository.save_macro(self.current_name, self.current_macro)
        self._last_persisted_macro = deepcopy(self.current_macro)
        self._refresh_steps(selected)
        self._update_history_buttons()
        self.status.emit("마지막 매크로 편집을 실행 취소했습니다.")
        self._data_change_timer.start()
        return True

    def redo_edit(self) -> bool:
        if not self.current_name or self.current_macro is None:
            return False
        history = self._redo_history.setdefault(self.current_name, [])
        if not history:
            self.status.emit("다시 실행할 매크로 편집 기록이 없습니다.")
            return False
        selected = max(0, self.steps_table.currentRow())
        self._undo_history.setdefault(self.current_name, []).append(deepcopy(self.current_macro))
        self.current_macro = history.pop()
        self.repository.save_macro(self.current_name, self.current_macro)
        self._last_persisted_macro = deepcopy(self.current_macro)
        self._refresh_steps(selected)
        self._update_history_buttons()
        self.status.emit("취소한 매크로 편집을 다시 실행했습니다.")
        self._data_change_timer.start()
        return True

    def _create_macro(self) -> None:
        dialog = MacroDialog("새 매크로", parent=self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            path = self.repository.create_macro(dialog.name_edit.text(), dialog.description_edit.text())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "생성 실패", str(exc))
            return
        self.refresh(path.stem)
        self.data_changed.emit()
        self.status.emit(f"'{path.stem}' 매크로를 만들었습니다.")

    def _duplicate_macro(self) -> None:
        if not self.current_name:
            return
        dialog = MacroDialog("매크로 복제", f"{self.current_name}-복사본", self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            path = self.repository.duplicate_macro(self.current_name, dialog.name_edit.text())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "복제 실패", str(exc))
            return
        self.refresh(path.stem)
        self.data_changed.emit()

    def _archive_macro(self) -> None:
        names = self._selected_macro_names() or ([self.current_name] if self.current_name else [])
        self._archive_macros(names, confirm=True)

    def _archive_current_macro(self, confirm: bool) -> dict[str, Any] | None:
        if not self.current_name:
            return None
        return self._archive_macros([self.current_name], confirm)

    def _archive_macros(self, names: list[str], confirm: bool) -> dict[str, Any] | None:
        names = list(dict.fromkeys(name for name in names if name and self.repository.macro_path(name).exists()))
        if not names:
            return None
        if confirm:
            answer = QtWidgets.QMessageBox.question(
                self,
                "매크로 보관",
                f"선택한 {len(names)}개 매크로를 보관할까요?\n파일은 .archive 폴더에 남습니다.",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return None
        records: list[dict[str, Any]] = []
        for name in names:
            target = self.repository.archive_macro(name)
            records.append({"kind": "macro", "name": name, "archive_path": str(target)})
        self.current_name = ""
        self.current_macro = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"매크로 {len(records)}개를 보관했습니다.")
        return records[0] if len(records) == 1 else {"kind": "batch", "items": records}

    def delete_selected(self) -> dict[str, Any] | None:
        focus = QtWidgets.QApplication.focusWidget()
        if focus is self.macro_list or (focus is not None and self.macro_list.isAncestorOf(focus)):
            names = self._selected_macro_names() or ([self.current_name] if self.current_name else [])
            return self._archive_macros(names, confirm=False)
        indexes = self.node_canvas.selected_indexes()
        if not indexes:
            indexes = sorted({model_index.row() + 1 for model_index in self.steps_table.selectionModel().selectedRows()})
        if not indexes or self.current_macro is None:
            self.status.emit("삭제할 매크로나 노드를 선택하세요.")
            return None
        snapshot = deepcopy(self.current_macro)
        name = self.current_name
        before = len(self.current_macro.get("steps") or [])
        self._history_suspended = True
        try:
            self._delete_nodes(indexes)
        finally:
            self._history_suspended = False
        after = len((self.current_macro or {}).get("steps") or [])
        if after >= before:
            return None
        return {"kind": "macro_step", "name": name, "payload": snapshot}

    def _run_current(self) -> None:
        if not self.run_button.isEnabled():
            self.status.emit("이미 매크로가 실행 중입니다. 새로 실행하려면 먼저 정지해 주세요.")
            return
        if not self.current_name:
            self.status.emit("실행할 매크로를 먼저 선택하세요.")
            return
        row = self.steps_table.currentRow()
        steps = (self.current_macro or {}).get("steps") or []
        if 0 <= row < len(steps):
            self._save_step()
        issues = self._execution_issues()
        if issues:
            message = "\n".join(f"• {issue}" for issue in issues)
            QtWidgets.QMessageBox.warning(self, "실행 전 설정 확인", message)
            self.status.emit("실행 전 검사에서 오류가 발견되어 실행하지 않았습니다.")
            return
        warnings = list(getattr(self, "_last_execution_warnings", []))
        if warnings:
            message = "실행은 가능하지만 확인이 필요한 항목입니다.\n\n" + "\n".join(f"• {warning}" for warning in warnings)
            answer = QtWidgets.QMessageBox.question(
                self,
                "실행 전 주의 사항",
                message + "\n\n계속 실행할까요?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self.status.emit("사용자가 실행 전 주의 사항을 확인하고 실행을 취소했습니다.")
                return
        self.status.emit(f"'{self.current_name}' 실행을 요청했습니다. 로그 버튼에서 진행 상태를 확인할 수 있습니다.")
        self.run_macro.emit(self.current_name)

    def _run_from_selected_step(self) -> None:
        if not self.current_name or self.current_macro is None:
            self.status.emit("실행할 매크로를 먼저 선택하세요.")
            return
        index = self.node_canvas.selected_index()
        if index <= 0:
            row = self.steps_table.currentRow()
            index = row + 1 if row >= 0 else 0
        if index <= 0:
            self.status.emit("다시 시작할 노드를 선택하세요.")
            return
        self._save_step()
        self.status.emit(f"'{self.current_name}'을(를) {index}번 노드부터 실행합니다.")
        self.run_macro_from_step.emit(self.current_name, index)

    def _run_dry_run(self) -> None:
        if not self.current_name:
            self.status.emit("시뮬레이션할 매크로를 먼저 선택하세요.")
            return
        row = self.steps_table.currentRow()
        if row >= 0:
            self._save_step()
        self.status.emit(f"'{self.current_name}' 드라이런을 시작합니다. 실제 클릭과 입력은 발생하지 않습니다.")
        self.run_macro_dry_run.emit(self.current_name)

    def _configure_recovery_engine(self) -> None:
        if not self.current_name or self.current_macro is None:
            self.status.emit("설정할 매크로를 먼저 선택하세요.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("복구 가능한 실행 엔진")
        dialog.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(dialog)
        note = QtWidgets.QLabel(
            "정상 완료 전에는 마지막 성공 노드의 다음 위치를 저장합니다. Studio나 대상 프로그램이 종료된 뒤 "
            "다시 실행하면 해당 위치에서 자동 재개됩니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QtWidgets.QFormLayout()
        limit = WheelSafeSpinBox()
        limit.setRange(0, 999)
        limit.setSpecialValueText("사용 안 함")
        limit.setSuffix("회")
        meta = self.current_macro.get("meta") if isinstance(self.current_macro.get("meta"), dict) else {}
        limit.setValue(int(meta.get("failure_streak_limit") or self.current_macro.get("failure_streak_limit") or 0))
        form.addRow("연속 실패 자동 정지", limit)
        layout.addLayout(form)
        current = self.repository.saved_checkpoint(self.current_name)
        checkpoint = QtWidgets.QLabel(
            f"저장된 재개 지점: {current}번 노드" if current else "저장된 재개 지점: 없음"
        )
        checkpoint.setObjectName("Muted")
        layout.addWidget(checkpoint)
        clear_btn = QtWidgets.QPushButton("저장된 재개 지점 지우기")
        clear_btn.setEnabled(current > 0)
        clear_btn.clicked.connect(lambda: (self.repository.clear_checkpoint(self.current_name), checkpoint.setText("저장된 재개 지점: 없음"), clear_btn.setEnabled(False)))
        layout.addWidget(clear_btn)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        meta = dict(meta)
        if limit.value():
            meta["failure_streak_limit"] = limit.value()
        else:
            meta.pop("failure_streak_limit", None)
        self.current_macro["meta"] = meta
        self._persist("복구 실행 설정을 저장했습니다.")

    def _configure_event_triggers(self) -> None:
        if not self.current_name or self.current_macro is None:
            self.status.emit("설정할 매크로를 먼저 선택하세요.")
            return
        triggers = self.current_macro.get("triggers") if isinstance(self.current_macro.get("triggers"), list) else []
        dialog = EventTriggerDialog(self.repository, triggers, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        if dialog.triggers:
            self.current_macro["triggers"] = dialog.triggers
        else:
            self.current_macro.pop("triggers", None)
        self._persist(f"이벤트 자동 실행 조건 {len(dialog.triggers)}개를 저장했습니다.")

    def _open_macro_test_cases(self) -> None:
        if not self.current_name or self.current_macro is None:
            self.status.emit("테스트할 매크로를 먼저 선택하세요.")
            return
        dialog = MacroTestCaseDialog(self.current_macro, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        if dialog.cases:
            self.current_macro["test_cases"] = dialog.cases
        else:
            self.current_macro.pop("test_cases", None)
        self._persist(f"매크로 테스트 케이스 {len(dialog.cases)}개를 저장했습니다.")

    def _open_current_export(self) -> None:
        if not self.current_name:
            self.status.emit("내보낼 매크로를 먼저 선택하세요.")
            return
        row = self.steps_table.currentRow()
        steps = (self.current_macro or {}).get("steps") or []
        if 0 <= row < len(steps):
            self._save_step()
        self.open_export.emit(self.current_name)

    def _execution_issues(self) -> list[str]:
        issues: list[str] = []
        warnings: list[str] = []
        validator = ProjectValidator(self.repository)
        try:
            validation_issues = [item for item in validator.validate() if item.macro == self.current_name]
        except Exception as exc:
            validation_issues = []
            warnings.append(f"프로젝트 검사 일부를 완료하지 못했습니다: {exc}")
        for item in validation_issues:
            prefix = f"{item.step}번 " if item.step else ""
            detail = f" · {item.detail}" if item.detail else ""
            message = f"{prefix}{item.title}{detail}"
            if item.severity == "error":
                issues.append(message)
            else:
                warnings.append(message)
        ocr_checked = False
        for index, step in enumerate((self.current_macro or {}).get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "")
            if action in {"image_search", "screen_condition"} and str(step.get("engine") or "ahk").lower() == "opencv":
                try:
                    self.repository._ensure_opencv_runtime()
                except Exception as exc:
                    issues.append(f"{index}번 이미지 서치: OpenCV 실행 환경을 사용할 수 없습니다. {exc}")
            if action == "ocr" and not ocr_checked:
                ocr_checked = True
                try:
                    self.repository._ensure_ocr_runtime()
                except Exception as exc:
                    issues.append(f"{index}번 OCR: OCR 실행 환경을 사용할 수 없습니다. {exc}")
            target_title = ""
            target_exe = ""
            if action == "mouse_click" and str(step.get("coordinate_scope") or "screen") == "client":
                target_title, target_exe = str(step.get("window") or ""), str(step.get("window_exe") or "")
            elif action == "inactive_click":
                target_title, target_exe = str(step.get("window") or ""), str(step.get("window_exe") or "")
            elif action in {"image_search", "screen_condition"} and str(step.get("region_mode") or "screen") in {"window", "client"}:
                target_title, target_exe = str(step.get("region_window") or ""), str(step.get("region_window_exe") or "")
            elif action == "ocr" and str(step.get("capture_mode") or "screen") in {"window", "client"}:
                target_title = str(step.get("window_title") or "")
            if (target_title or target_exe) and not self._target_window_exists(target_title, target_exe):
                warnings.append(f"{index}번 대상 창을 현재 찾지 못했습니다 · {target_exe or target_title}")
        self._last_execution_warnings = list(dict.fromkeys(warnings))
        return list(dict.fromkeys(issues))

    @staticmethod
    def _target_window_exists(title: str, executable: str) -> bool:
        try:
            from .image_search_test import _find_window

            if executable and _find_window(executable, title):
                return True
            import ctypes

            plain_title = str(title or "").split(" ahk_", 1)[0].strip()
            return bool(plain_title and ctypes.windll.user32.FindWindowW(None, plain_title))
        except Exception:
            return True

    def _open_version_history(self) -> None:
        if not self.current_name:
            self.status.emit("버전 기록을 볼 매크로를 먼저 선택하세요.")
            return
        versions = self.repository.list_macro_versions(self.current_name)
        if not versions:
            QtWidgets.QMessageBox.information(
                self,
                "버전 기록",
                "아직 이전 버전이 없습니다. 매크로를 수정해 저장하면 변경 전 상태가 자동 보관됩니다.",
            )
            return
        dialog = QtWidgets.QDialog(self.window())
        dialog.setWindowTitle(f"버전 기록 · {self.current_name}")
        dialog.resize(760, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        hint = QtWidgets.QLabel("복구해도 현재 상태가 새 버전으로 먼저 백업되므로 다시 되돌릴 수 있습니다.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        table = QtWidgets.QTableWidget(len(versions), 5)
        table.setHorizontalHeaderLabels(["저장 시각", "채널", "노드", "현재와 비교", "설명"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, version in enumerate(versions):
            modified = version["modified"].strftime("%Y-%m-%d %H:%M:%S")
            step_count = int(version.get("steps") or 0)
            comparison = self._version_diff_summary(
                (version.get("payload") or {}).get("steps") or [],
                (self.current_macro or {}).get("steps") or [],
            )
            channel = "안정" if str(version.get("channel") or "test") == "stable" else "테스트"
            for column, value in enumerate((modified, channel, str(step_count), comparison, str(version.get("description") or ""))):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        table.selectRow(0)
        layout.addWidget(table, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.RestoreDefaults | QtWidgets.QDialogButtonBox.Close)
        restore_button = buttons.button(QtWidgets.QDialogButtonBox.RestoreDefaults)
        restore_button.setText("선택 버전 복구")
        buttons.rejected.connect(dialog.reject)

        def restore_selected() -> None:
            row = table.currentRow()
            if not 0 <= row < len(versions):
                return
            answer = QtWidgets.QMessageBox.question(
                dialog,
                "선택 버전 복구",
                f"{table.item(row, 0).text()} 버전으로 복구할까요?\n현재 상태는 자동 백업됩니다.",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
            try:
                self.repository.restore_macro_version(self.current_name, Path(versions[row]["path"]))
            except Exception as exc:
                QtWidgets.QMessageBox.warning(dialog, "복구 실패", str(exc))
                return
            dialog.accept()

        restore_button.clicked.connect(restore_selected)
        layout.addWidget(buttons)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.refresh(self.current_name)
            self.status.emit(f"'{self.current_name}' 이전 버전을 복구했습니다. 복구 전 상태도 버전 기록에 남겼습니다.")

    @staticmethod
    def _version_diff_summary(old_steps: list[Any], current_steps: list[Any]) -> str:
        shared = min(len(old_steps), len(current_steps))
        changed = sum(
            1
            for index in range(shared)
            if json.dumps(old_steps[index], ensure_ascii=False, sort_keys=True)
            != json.dumps(current_steps[index], ensure_ascii=False, sort_keys=True)
        )
        added = max(0, len(current_steps) - len(old_steps))
        removed = max(0, len(old_steps) - len(current_steps))
        parts = []
        if changed:
            parts.append(f"변경 {changed}")
        if added:
            parts.append(f"현재에 추가 {added}")
        if removed:
            parts.append(f"현재에서 삭제 {removed}")
        return " · ".join(parts) if parts else "노드 내용 동일"

    def _set_current_release_channel(self, channel: str) -> None:
        if not self.current_name:
            self.status.emit("버전 채널을 지정할 매크로를 먼저 선택하세요.")
            return
        try:
            self.repository.set_macro_release_channel(self.current_name, channel)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "버전 채널 저장 실패", str(exc))
            return
        self.refresh(self.current_name)
        label = "안정 버전" if channel == "stable" else "자동화 테스트 버전"
        self.status.emit(f"'{self.current_name}' 현재 상태를 {label}으로 표시했습니다.")

    def _open_logs(self) -> None:
        if self._log_dialog is not None and self._log_dialog.isVisible():
            self._log_dialog.raise_()
            self._log_dialog.activateWindow()
            return
        dialog = MacroLogDialog(self.repository, self.window())
        dialog.destroyed.connect(lambda: setattr(self, "_log_dialog", None))
        self._log_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_editor(self) -> None:
        self.current_name = ""
        self.current_macro = None
        self._last_persisted_macro = None
        self.macro_title.setText("매크로를 선택하세요")
        self.steps_table.setRowCount(0)
        self.json_edit.clear()
        self.node_canvas.set_macro(None)
        self._update_history_buttons()

    def shutdown_automation(self) -> None:
        if self._recording_controller is not None:
            self._recording_controller.stop()
            self._recording_controller = None
