from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .action_editor import (
    ACTION_LABELS,
    ActionEditor,
    ActionEditorDialog,
    ImageSearchConfidenceDialog,
    action_template,
    korean_contains,
)
from .ai_macro_plan import validate_compiled_draft
from .automation import (
    AutomationAnalyzer,
    AutomationOverlay,
    DiagnosticsDialog,
    QuickActionWizard,
    RecordingReviewDialog,
    SmartRecordingController,
    configure_success_candidates,
)
from .log_dialog import MacroLogDialog
from .help_dialog import MacroHelpDialog
from .inactive_click_lab import HandlePointPicker, InactiveClickLabDialog
from .image_editor import ImageEditorDialog
from .node_editor import ACTION_STYLES, ACTION_TITLES, NodeCanvas
from .repository import MacroRepository
from .theme import COLORS
from .trigger_dialog import EventTriggerDialog
from .macro_test_cases import MacroTestCaseDialog, run_test_cases
from .validation import ProjectValidator
from .widgets import Card, PageHeader, WheelSafeSpinBox, danger_button, primary_button


ACTION_TEMPLATES: dict[str, dict[str, Any]] = {action: action_template(action) for action in ACTION_LABELS}


class MacroListWidget(QtWidgets.QListWidget):
    delete_requested = QtCore.Signal()
    undo_requested = QtCore.Signal()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Delete and event.modifiers() == QtCore.Qt.NoModifier:
            self.delete_requested.emit()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_Z and event.modifiers() == QtCore.Qt.ControlModifier:
            self.undo_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _is_unconfigured_template_step(step: dict[str, Any]) -> bool:
    """Recognize the untouched starter node created with a new macro."""
    action = str(step.get("action") or "")
    template = ACTION_TEMPLATES.get(action)
    if not template or step.get("label") or step.get("_automation"):
        return False
    ignored = {"on_success", "on_fail", "edge_conditions"}
    normalized = {key: value for key, value in step.items() if key not in ignored}
    return normalized == template


CATEGORIZED_ACTIONS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("🖱️ 마우스 & 키보드", "#4D9FFF", [
        ("mouse_click", "마우스 클릭", "지정한 화면 좌표를 클릭하거나 드래그합니다."),
        ("inactive_click", "비활성 클릭", "창이 가려져 있어도 백그라운드 클릭을 전달합니다."),
        ("type_text", "텍스트 입력", "문자열 타이핑 및 단축키/기능키를 입력합니다."),
        ("wait", "대기", "지정한 시간(ms) 동안 일시 대기합니다."),
    ]),
    ("🖼️ 화면 & 이미지", "#35C89A", [
        ("image_search", "이미지 서치", "화면에서 이미지를 찾아 중심 또는 오프셋을 클릭합니다."),
        ("screen_condition", "화면 조건", "이미지가 화면에 있는지 확인하여 성공/실패로 분기합니다."),
    ]),
    ("🎯 색상 & 픽셀", "#FF6B9D", [
        ("pixel_search", "픽셀 색상 서치", "화면 또는 지정 영역에서 특정 색상의 픽셀 좌표를 찾습니다."),
        ("multi_pixel_check", "다중 픽셀 체크", "여러 좌표의 픽셀 색상이 동시에 일치하는지 정밀 판별합니다."),
        ("wait_color", "색상 변화 대기", "특정 좌표의 색상이 목표 색상이 되거나 벗어날 때까지 대기합니다."),
        ("color_ratio", "색상 비율 게이지", "지정 영역 내 특정 색상의 픽셀 비율(%)을 측정하여 체력바 등을 감지합니다."),
    ]),
    ("🔤 OCR & 텍스트", "#F5B942", [
        ("ocr", "OCR 텍스트 인식", "화면의 문자를 인식하여 텍스트/숫자를 추출하거나 클릭합니다."),
        ("ocr_tracking", "OCR 추적 인식", "움직이는 이미지/박스를 추적하여 그 위치의 문자를 실시간 인식합니다."),
        ("text_condition", "텍스트 조건", "변수나 추출된 텍스트가 특정 단어를 포함하는지 조건 분기합니다."),
    ]),
    ("📊 데이터 & 엑셀", "#24B8C7", [
        ("table_store", "테이블 값 저장", "변수나 화면 추출 값을 데이터 테이블에 저장합니다."),
        ("table_copy", "테이블 복사", "테이블의 행/열 데이터를 복사합니다."),
        ("table_paste", "테이블 붙여넣기", "테이블 데이터를 대상 창에 순차적으로 자동 붙여넣습니다."),
        ("table_excel_read", "Excel 읽기", "엑셀(.xlsx) 파일의 셀/시트 데이터를 읽어옵니다."),
        ("table_excel_write", "Excel 쓰기", "데이터를 엑셀(.xlsx) 파일로 내보내어 저장합니다."),
    ]),
    ("⚙️ 변수 & 흐름 제어", "#C47CFF", [
        ("set_var", "변수 설정", "변수에 문자열, 숫자 또는 고정 값을 저장합니다."),
        ("calc_var", "변수 계산", "변수 간 사칙연산 및 수식을 계산합니다."),
        ("vault_get", "보안 보관함 값", "비밀번호 등 암호화된 보안 보관함 값을 안전하게 불러옵니다."),
        ("coord_mode", "좌표 기준 변경", "화면(Screen) 또는 창(Window) 좌표 기준을 전환합니다."),
        ("call_submacro", "서브매크로 호출", "다른 매크로를 서브루틴으로 호출하여 실행합니다."),
        ("flow_control", "반복 이동", "지정한 노드로 점프하거나 반복 루프를 제어합니다."),
        ("datetime_condition", "날짜·시간 조건", "특정 시각, 요일, 날짜 범위에 맞추어 조건 분기합니다."),
    ]),
    ("🌐 시스템 & 앱", "#F06A78", [
        ("browser_action", "브라우저 요소", "웹 브라우저의 DOM 요소를 감지하고 제어합니다."),
        ("run_program", "프로그램 실행", "외부 프로그램이나 스크립트를 실행합니다."),
        ("terminate_program", "프로그램 종료", "실행 중인 대상 프로그램을 안전하게 종료합니다."),
        ("remote_notify", "모바일 알림", "스마트폰이나 웹훅으로 완료/알림 메시지를 전송합니다."),
    ]),
]


class ActionButtonTile(QtWidgets.QPushButton):
    double_clicked = QtCore.Signal(str)

    def __init__(self, action_key: str, label: str, tooltip: str, badge: str = "", color: str = "#4D9FFF", parent=None):
        super().__init__(parent)
        self.action_key = action_key
        self.label = label
        self.badge = badge
        self.accent_color = color
        self.setToolTip(f"<b>{label}</b><br>{tooltip}")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self.setText(f"  [{badge}]  {label}" if badge else f"  {label}")
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: #1D2A42;
                    color: #FFFFFF;
                    border: 2px solid {self.accent_color};
                    border-radius: 6px;
                    text-align: left;
                    padding-left: 10px;
                    font-size: 9pt;
                    font-weight: 700;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: #171C26;
                    color: #E2E8F0;
                    border: 1px solid #2B354A;
                    border-radius: 6px;
                    text-align: left;
                    padding-left: 10px;
                    font-size: 9pt;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: #242E42;
                    border-color: {self.accent_color};
                    color: #FFFFFF;
                }}
                QPushButton:pressed {{
                    background: #121620;
                }}
            """)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.double_clicked.emit(self.action_key)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CategorizedActionDialog(QtWidgets.QDialog):
    """Wide rectangular action picker organized into thematic categories with live Korean search."""

    def __init__(self, current_action: str = "", parent=None) -> None:
        super().__init__(parent)
        self.selected_action = current_action or "mouse_click"
        self.add_immediately = False
        self.setWindowTitle("추가할 노드 액션 선택")
        self.resize(920, 600)
        self.setStyleSheet("QDialog { background: #11151F; color: #E2E8F0; }")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hdr = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("✦ 추가할 노드 액션 선택")
        title.setStyleSheet("font-size: 13pt; font-weight: 800; color: #FFFFFF;")
        hdr.addWidget(title)
        hdr.addSpacing(20)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("액션 검색 · 이름 또는 초성 (예: 색상, ocr, ㄷㅈ)")
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background: #181D28;
                color: #FFFFFF;
                border: 1px solid #2F3B52;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border-color: #4D9FFF;
            }
        """)
        self.search_edit.textChanged.connect(self._filter_actions)
        hdr.addWidget(self.search_edit, 1)
        layout.addLayout(hdr)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #242C3D; border-radius: 8px; background: #0E121A; }")
        body = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout(body)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setHorizontalSpacing(14)
        self.grid_layout.setVerticalSpacing(14)

        self.category_widgets: list[tuple[QtWidgets.QGroupBox, list[ActionButtonTile]]] = []

        col_count = 3
        for idx, (cat_title, cat_color, items) in enumerate(CATEGORIZED_ACTIONS):
            box = QtWidgets.QGroupBox(f"{cat_title}  ({len(items)})")
            box.setStyleSheet(f"""
                QGroupBox {{
                    font-weight: 700;
                    font-size: 9.5pt;
                    color: {cat_color};
                    border: 1px solid #252F42;
                    border-radius: 8px;
                    margin-top: 8px;
                    padding: 10px 8px 8px 8px;
                    background: #141923;
                }}
            """)
            box_layout = QtWidgets.QVBoxLayout(box)
            box_layout.setContentsMargins(4, 8, 4, 4)
            box_layout.setSpacing(5)

            tile_list: list[ActionButtonTile] = []
            for action_key, label, desc in items:
                style = ACTION_STYLES.get(action_key, ("•", cat_color))
                badge = style[0]
                tile = ActionButtonTile(action_key, label, desc, badge, cat_color, box)
                tile.clicked.connect(lambda _, k=action_key: self._select_action(k))
                tile.double_clicked.connect(self._double_click_action)
                box_layout.addWidget(tile)
                tile_list.append(tile)

            box_layout.addStretch(1)
            row = idx // col_count
            col = idx % col_count
            self.grid_layout.addWidget(box, row, col)
            self.category_widgets.append((box, tile_list))

        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.selected_label = QtWidgets.QLabel()
        self.selected_label.setStyleSheet("color: #CAD5E8; font-size: 9.5pt;")
        bottom.addWidget(self.selected_label)
        bottom.addStretch(1)

        btn_add = QtWidgets.QPushButton("⚡ 즉시 추가 (더블클릭 / Enter)")
        btn_add.setStyleSheet("""
            QPushButton {
                background: #1A56DB;
                color: #FFFFFF;
                font-weight: 700;
                font-size: 9.5pt;
                padding: 7px 18px;
                border-radius: 6px;
                border: 1px solid #3B82F6;
            }
            QPushButton:hover {
                background: #2563EB;
            }
            QPushButton:pressed {
                background: #1E40AF;
            }
        """)
        btn_add.setToolTip("더블클릭 또는 이 버튼을 누르면 스마트 설정(화면 캡처/좌표 선택) 후 노드를 즉시 추가합니다.")
        btn_add.clicked.connect(self._confirm_add_immediately)
        bottom.addWidget(btn_add)

        btn_select = QtWidgets.QPushButton("선택만 하고 닫기")
        btn_select.setStyleSheet("padding: 7px 14px; border-radius: 6px; background: #1B2130; color: #DDE5F4; border: 1px solid #2C374E; font-size: 9pt;")
        btn_select.clicked.connect(self._confirm_select_only)
        bottom.addWidget(btn_select)

        btn_cancel = QtWidgets.QPushButton("취소 (Esc)")
        btn_cancel.setStyleSheet("padding: 7px 14px; border-radius: 6px; background: #151821; color: #8A98B0; border: 1px solid #242C3D; font-size: 9pt;")
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        layout.addLayout(bottom)

        self._update_selection_visuals()

    def _select_action(self, action_key: str) -> None:
        self.selected_action = action_key
        self._update_selection_visuals()

    def _double_click_action(self, action_key: str) -> None:
        self.selected_action = action_key
        self.add_immediately = True
        self.accept()

    def _confirm_add_immediately(self) -> None:
        if self.selected_action:
            self.add_immediately = True
            self.accept()

    def _confirm_select_only(self) -> None:
        self.add_immediately = False
        self.accept()

    def _update_selection_visuals(self) -> None:
        for box, tiles in self.category_widgets:
            for tile in tiles:
                tile.set_selected(tile.action_key == self.selected_action)
        action_name = ACTION_LABELS.get(self.selected_action, self.selected_action)
        self.selected_label.setText(
            f"선택: <span style='color:#4D9FFF; font-weight:700;'>{action_name}</span> "
            f"<span style='color:#7A89A4;'>· 더블클릭 또는 Enter 시 즉시 스마트 추가</span>"
        )

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if self.selected_action:
                self.add_immediately = True
                self.accept()
                event.accept()
                return
        elif event.key() == QtCore.Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def _filter_actions(self, text: str) -> None:
        query = text.strip()
        first_match = ""
        for box, tiles in self.category_widgets:
            any_visible = False
            for tile in tiles:
                match = not query or korean_contains(query, tile.label) or korean_contains(query, tile.action_key) or korean_contains(query, tile.toolTip())
                tile.setVisible(match)
                if match:
                    any_visible = True
                    if not first_match:
                        first_match = tile.action_key
            box.setVisible(any_visible)
        if query and first_match:
            self.selected_action = first_match
            self._update_selection_visuals()


class ActionSelectorCombo(QtWidgets.QComboBox):
    node_addition_requested = QtCore.Signal(str)

    def showPopup(self) -> None:
        dialog = CategorizedActionDialog(str(self.currentData() or "mouse_click"), self.window())
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            selected = dialog.selected_action
            if selected:
                idx = self.findData(selected)
                if idx >= 0:
                    self.setCurrentIndex(idx)
            if dialog.add_immediately:
                QtWidgets.QApplication.processEvents()
                self.node_addition_requested.emit(selected)


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


class NodeArchiveDialog(QtWidgets.QDialog):
    """보관된 노드들을 확인하고 캔버스로 다시 복원하거나 영구 삭제하는 보관함 창."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        archived_steps: list[dict[str, Any]],
        restore_callback: Any,
        delete_callback: Any,
    ) -> None:
        super().__init__(parent)
        self.archived_steps = archived_steps
        self.restore_callback = restore_callback
        self.delete_callback = delete_callback
        self.setWindowTitle("📦 노드 보관함 (보관된 노드 복원 및 관리)")
        self.resize(840, 500)
        self.setStyleSheet("""
            QDialog { background: #131722; color: #E2E8F0; }
            QTableWidget { background: #0D0F15; border: 1px solid #242B3D; color: #E2E8F0; gridline-color: #1A2130; border-radius: 6px; selection-background-color: #1E3A5F; }
            QTableWidget::item:hover { background: #162032; }
            QHeaderView::section { background: #171A26; color: #9DA7BA; border: none; padding: 7px; font-weight: bold; border-bottom: 1px solid #242B3D; }
            QPushButton { background: #1B293A; border: 1px solid #2B5A8A; color: #70C5FF; padding: 6px 14px; border-radius: 6px; font-weight: 600; min-height: 22px; }
            QPushButton:hover { background: #2B5A8A; color: #FFFFFF; }
        """)
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("📦 노드 보관함")
        title.setStyleSheet("font-size: 15pt; font-weight: 800; color: #5ED9FF;")
        subtitle = QtWidgets.QLabel("삭제되거나 보관 처리된 노드들이 보관되는 공간입니다. 원하는 노드를 선택하여 다시 매크로 캔버스로 복원할 수 있습니다.")
        subtitle.setStyleSheet("color: #94A3B8; font-size: 9.5pt;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        vbox.addLayout(header)

        self.table = QtWidgets.QTableWidget(len(self.archived_steps), 5)
        self.table.setHorizontalHeaderLabels(["선택", "#", "액션 유형", "노드 이름 / 라벨", "주요 설정 내용"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.table.setColumnWidth(3, 220)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        for row, step in enumerate(self.archived_steps):
            chk_item = QtWidgets.QTableWidgetItem()
            chk_item.setCheckState(QtCore.Qt.Unchecked)
            self.table.setItem(row, 0, chk_item)

            num_item = QtWidgets.QTableWidgetItem(str(row + 1))
            num_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(row, 1, num_item)

            action = str(step.get("action") or "")
            action_name = ACTION_TITLES.get(action, action)
            act_item = QtWidgets.QTableWidgetItem(action_name)
            self.table.setItem(row, 2, act_item)

            label = str(step.get("label") or step.get("name") or action_name)
            lbl_item = QtWidgets.QTableWidgetItem(label)
            lbl_item.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            self.table.setItem(row, 3, lbl_item)

            details = []
            if action in {"image_search", "screen_condition"}:
                asset = step.get("asset") or (step.get("assets") or [""])[0]
                if asset:
                    details.append(f"자산: {asset}")
                engine = step.get("engine")
                if engine:
                    details.append(f"엔진: {engine}")
            elif action == "wait":
                dur = step.get("duration") or 1000
                details.append(f"대기: {dur}ms")
            elif action in {"mouse_click", "inactive_click"}:
                pos = step.get("position")
                if pos:
                    details.append(f"좌표: {pos}")
            elif action == "type_text":
                txt = step.get("text") or ""
                details.append(f"텍스트: {txt[:25]}...")
            elif action == "key_press":
                key = step.get("key") or ""
                details.append(f"키: {key}")
            elif action == "pixel_search":
                clr = step.get("color") or ""
                details.append(f"색상: {clr}")
            detail_str = " · ".join(details) if details else "-"
            det_item = QtWidgets.QTableWidgetItem(detail_str)
            self.table.setItem(row, 4, det_item)

        vbox.addWidget(self.table, 1)

        bottom_row = QtWidgets.QHBoxLayout()
        btn_sel_all = QtWidgets.QPushButton("전체 선택")
        btn_sel_all.clicked.connect(self._select_all)
        btn_desel_all = QtWidgets.QPushButton("선택 해제")
        btn_desel_all.clicked.connect(self._deselect_all)
        bottom_row.addWidget(btn_sel_all)
        bottom_row.addWidget(btn_desel_all)
        bottom_row.addStretch(1)

        btn_restore = QtWidgets.QPushButton("↩️ 선택 노드 캔버스로 복원")
        btn_restore.setStyleSheet("background: #0E4E7A; color: #FFFFFF; border: 1px solid #38BDF8; font-weight: bold; padding: 7px 18px;")
        btn_restore.clicked.connect(self._do_restore)
        bottom_row.addWidget(btn_restore)

        btn_delete = QtWidgets.QPushButton("🗑️ 선택 영구 삭제")
        btn_delete.setStyleSheet("background: #3A1B22; color: #FFA3A3; border: 1px solid #7A2838; padding: 7px 14px;")
        btn_delete.clicked.connect(self._do_delete)
        bottom_row.addWidget(btn_delete)

        if not self.archived_steps:
            self.table.setRowCount(1)
            msg = QtWidgets.QTableWidgetItem("📦 현재 보관된 노드가 없습니다. 노드를 우클릭하여 [노드 보관]을 실행하면 여기에 보관됩니다.")
            msg.setTextAlignment(QtCore.Qt.AlignCenter)
            msg.setForeground(QtGui.QColor("#94A3B8"))
            self.table.setItem(0, 3, msg)
            btn_restore.setEnabled(False)
            btn_delete.setEnabled(False)
            btn_sel_all.setEnabled(False)
            btn_desel_all.setEnabled(False)

        btn_close = QtWidgets.QPushButton("닫기")
        btn_close.clicked.connect(self.reject)
        bottom_row.addWidget(btn_close)

        vbox.addLayout(bottom_row)

    def _selected_rows(self) -> list[int]:
        rows = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.checkState() == QtCore.Qt.Checked:
                rows.add(r)
        for idx in self.table.selectionModel().selectedRows():
            rows.add(idx.row())
        return sorted(rows)

    def _select_all(self) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(QtCore.Qt.Checked)

    def _deselect_all(self) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(QtCore.Qt.Unchecked)

    def _do_restore(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "노드 보관함", "복원할 노드를 1개 이상 선택하세요 (체크박스 또는 행 클릭).")
            return
        self.restore_callback(rows)
        self.accept()

    def _do_delete(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(self, "노드 보관함", "영구 삭제할 노드를 1개 이상 선택하세요.")
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "노드 영구 삭제",
            f"선택한 {len(rows)}개 노드를 보관함에서 완전히 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self.delete_callback(rows)
            self.accept()


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
    ai_macro_save_result = QtCore.Signal(bool, str)

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
        self._recording_review_dialog: RecordingReviewDialog | None = None
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

        new_btn = QtWidgets.QPushButton("＋ 새 매크로")
        new_btn.setStyleSheet("""
            QPushButton {
                background: #231E3D;
                color: #CBBFFF;
                border: 1px solid #453A73;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 9pt;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #2F2852;
                color: #FFFFFF;
                border-color: #5C4F99;
            }
        """)
        new_btn.setToolTip("<b>새 매크로 생성 (Ctrl+N)</b><br>새로운 작업 흐름을 빈 캔버스에서 시작합니다.")
        new_btn.clicked.connect(self._create_macro)

        duplicate_btn = QtWidgets.QPushButton("복제")
        duplicate_btn.setToolTip("<b>현재 매크로 복제</b><br>현재 매크로의 모든 노드와 설정을 그대로 복사하여 새 이름으로 생성합니다.")
        duplicate_btn.clicked.connect(self._duplicate_macro)
        archive_btn = danger_button("보관")
        archive_btn.setToolTip("<b>현재 매크로 보관</b><br>현재 매크로를 안전하게 보관함으로 이동합니다. (언제든 복원 가능)")
        archive_btn.clicked.connect(self._archive_macro)

        self.record_btn = QtWidgets.QPushButton("● 스마트 녹화")
        self.record_btn.setStyleSheet("""
            QPushButton {
                background: #7C6CFF;
                color: #FFFFFF;
                border: 1px solid #8F81FF;
                border-radius: 6px;
                padding: 5px 14px;
                font-size: 9pt;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #9084FF;
                border-color: #A49BFF;
            }
            QPushButton:pressed {
                background: #6857FF;
            }
        """)
        self.record_btn.setToolTip(
            "F9 시작 · ` 또는 F12 기록 ON/OFF · F5 결과 확인 · F6 1초 대기 · "
            "F7 새 작업 · F8 이미지 캡처 · F11 일반/분기 · F10 종료"
        )
        self.record_btn.clicked.connect(self._start_smart_recording)

        self.review_recording_btn = QtWidgets.QPushButton("▤ 최근 녹화 검토")
        self.review_recording_btn.setStyleSheet("""
            QPushButton {
                background: #171A22;
                color: #CBD5E1;
                border: 1px solid #2A3040;
                border-radius: 6px;
                padding: 5px 11px;
                font-size: 9pt;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #212634;
                color: #FFFFFF;
                border-color: #3B455C;
            }
            QPushButton:disabled {
                background: #12141C;
                color: #475163;
                border-color: #1E2330;
            }
        """)
        self.review_recording_btn.setToolTip("닫았던 마지막 스마트 녹화의 상세 편집/검토 창을 다시 엽니다.")
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

        self.action_combo = ActionSelectorCombo()
        self._populate_action_combo(self.action_combo)
        self.action_combo.setMinimumWidth(150)
        self.action_combo.setStyleSheet("""
            QComboBox {
                background: #12151C;
                color: #F2F4F8;
                border: 1px solid #2A3040;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 9pt;
                font-weight: 600;
            }
            QComboBox:hover {
                border-color: #3E4B65;
            }
            QComboBox:focus {
                border-color: #7C6CFF;
            }
            QComboBox QAbstractItemView {
                background: #171A22;
                color: #F2F4F8;
                border: 1px solid #2A3040;
                selection-background-color: #2D2852;
                selection-color: #FFFFFF;
                padding: 4px;
            }
        """)
        self.action_combo.setToolTip("추가할 노드 액션 선택 (클릭 시 카테고리별 와이드 팝업 표시)")

        self.add_node_button = QtWidgets.QPushButton("＋ 마우스 클릭 노드 추가")
        self.add_node_button.setStyleSheet("""
            QPushButton {
                background: #1B2130;
                color: #DDE5F4;
                border: 1px solid #2C374E;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 9pt;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #252F44;
                color: #FFFFFF;
                border-color: #425275;
            }
            QPushButton:pressed {
                background: #151A26;
            }
        """)
        self.add_node_button.setToolTip("<b>노드 스마트 추가 (+)</b><br>선택한 액션 노드를 캔버스에 추가합니다.<br>이미지 서치나 비활성 클릭 등은 화면 캡처 및 좌표 지정이 즉시 실행됩니다.<br>💡 <i>Shift 키를 누른 채 클릭하면 빈 템플릿 노드로 추가됩니다.</i>")
        self.add_node_button.clicked.connect(self._add_step)
        self.action_combo.currentIndexChanged.connect(self._update_add_node_label)
        self.action_combo.node_addition_requested.connect(self._add_step)

        wizard_btn = QtWidgets.QPushButton("⚡ 자동 설정")
        wizard_btn.setToolTip("선택한 액션을 짧은 안내에 따라 자동 구성합니다.")
        wizard_btn.clicked.connect(self._quick_action_wizard)

        self.run_button = QtWidgets.QPushButton("▶ 실행")
        self.run_button.setToolTip("<b>매크로 전체 실행 (Ctrl+R)</b><br>현재 매크로를 1번 노드부터 끝까지 실제 동작과 함께 순차 실행합니다.<br>💡 <b>강제 중단</b>: 언제든 <code>F12</code> 키를 누르면 즉시 멈춥니다.")
        self.run_button.clicked.connect(self._run_current)

        self.warning_badge = QtWidgets.QToolButton()
        self.warning_badge.setText("⚠️ 주의 (0)")
        self.warning_badge.setStyleSheet("""
            QToolButton {
                background-color: #382402;
                color: #F0B72F;
                border: 1px solid #9E6A03;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: 800;
                font-size: 11px;
            }
            QToolButton:hover {
                background-color: #533604;
                border-color: #D29922;
                color: #FFF2C2;
            }
        """)
        self.warning_badge.setCursor(QtCore.Qt.PointingHandCursor)
        self.warning_badge.hide()
        self.warning_badge.clicked.connect(self._show_warning_dialog)

        self.player_button = QtWidgets.QPushButton("⚡ 플레이어로 실행")
        self.player_button.setToolTip("<b>전용 매크로 플레이어 (초고속 터보)</b><br>화면 렌더링 부하가 전혀 없는 가벼운 전용 플레이어 창을 띄워 현재 작업 중인 매크로를 즉시 실행합니다.")
        self.player_button.setStyleSheet("QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1F6FEB, stop:1 #1158C7); color: #FFFFFF; font-weight: 800; border: 1px solid #388BFD; padding: 4px 10px; border-radius: 4px; } QPushButton:hover { background: #388BFD; }")
        self.player_button.clicked.connect(self._open_in_player)

        self.dry_run_button = QtWidgets.QPushButton("▷ 드라이런")
        self.dry_run_button.setToolTip("<b>가상 시뮬레이션 (드라이런)</b><br>마우스 클릭이나 키보드 입력을 실제로 전송하지 않고, 이미지 서치와 조건 분기 흐름만 안전하게 가상 테스트합니다.")
        self.dry_run_button.clicked.connect(self._run_dry_run)

        self.stop_button = danger_button("■ 정지")
        self.stop_button.setToolTip("<b>매크로 즉시 정지 (F12)</b><br>현재 실행 중인 모든 매크로와 서치 프로세스를 즉시 강제 중단합니다.")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_running_macros)

        log_btn = QtWidgets.QPushButton("📋 성공·실패 로그")
        log_btn.setToolTip("<b>성공·실패 실행 로그 타임라인</b><br>노드별 실행 성공/실패 여부, 실패 시 쉬운 원인 분석과 개선 가이드를 실시간으로 확인합니다.")
        log_btn.setStyleSheet("font-weight: 700; color: #38E7FF;")
        log_btn.clicked.connect(self._open_logs)

        diagnose_btn = QtWidgets.QPushButton("✓ 자동 진단")
        diagnose_btn.setToolTip("<b>자동 진단 및 문제 해결</b><br>누락된 이미지, 끊어진 연결선, 잘못된 창 설정 등을 자동으로 검사하고 원클릭 수정안을 제시합니다.")
        diagnose_btn.clicked.connect(self._diagnose_automation)

        self.step_test_toolbar_btn = QtWidgets.QPushButton("▷ 단계별 테스트")
        self.step_test_toolbar_btn.setToolTip("<b>선택 노드 단계별 테스트 (Ctrl+Shift+T)</b><br>선택한 노드 1개만 단독으로 테스트하여, 이미지 탐지 성공 여부와 클릭 위치를 즉시 확인합니다.<br>💡 <b>우클릭</b>: 추가 테스트 옵션")
        self.step_test_toolbar_btn.clicked.connect(self._test_selected_step)
        self.step_test_toolbar_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.step_test_toolbar_btn.customContextMenuRequested.connect(self._show_step_test_menu)

        self.run_from_node_toolbar_btn = QtWidgets.QPushButton("▶ 선택 노드부터 실행")
        self.run_from_node_toolbar_btn.setToolTip("<b>선택 노드부터 이어서 실행</b><br>매크로를 처음부터 다시 시작하지 않고, 캔버스에서 선택한 특정 노드부터 끝까지 연속으로 실행합니다.")
        self.run_from_node_toolbar_btn.clicked.connect(self._run_from_selected_step)

        help_btn = QtWidgets.QPushButton("❓ 도움말 & 가이드")
        help_btn.setToolTip("<b>전체 도움말 & 사용 가이드 (F1)</b><br>노드 조작, 이미지 서치, 분기, 비활성 클릭, 단축키 등 모든 기능의 상세 설명서를 엽니다.")
        help_btn.setStyleSheet("font-weight: 700; color: #4ADE80; border-color: #2F4D3C;")
        help_btn.clicked.connect(self._open_help_dialog)

        toolbar_top.addWidget(new_btn)
        toolbar_top.addWidget(duplicate_btn)
        toolbar_top.addWidget(archive_btn)
        archive_box_btn = QtWidgets.QPushButton("📦 보관함")
        archive_box_btn.setToolTip("<b>노드 보관함 (📦)</b><br>삭제하거나 보관 처리한 노드 목록을 확인하고, 원하는 노드를 캔버스로 즉시 복원합니다.")
        archive_box_btn.clicked.connect(self._open_node_archive_dialog)
        toolbar_top.addWidget(archive_box_btn)
        toolbar_top.addStretch(1)
        toolbar_top.addWidget(self.dry_run_button)
        toolbar_top.addWidget(self.run_button)
        toolbar_top.addWidget(self.warning_badge)
        toolbar_top.addWidget(self.player_button)
        toolbar_top.addWidget(self.stop_button)
        toolbar_top.addWidget(log_btn)
        toolbar_top.addWidget(help_btn)

        recording_tools = QtWidgets.QToolButton()
        recording_tools.setText("녹화 도구 ▾")
        recording_tools.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        recording_tools.setStyleSheet("""
            QToolButton {
                background: #171A22;
                color: #E2E8F0;
                border: 1px solid #2A3040;
                border-radius: 6px;
                padding: 5px 11px;
                font-size: 9pt;
                font-weight: 600;
            }
            QToolButton:hover {
                background: #212634;
                color: #FFFFFF;
                border-color: #3B455C;
            }
            QToolButton::menu-indicator { image: none; }
        """)
        recording_menu = QtWidgets.QMenu(recording_tools)
        recording_menu.setStyleSheet("""
            QMenu {
                background: #171A22;
                color: #F2F4F8;
                border: 1px solid #2A3040;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #2A3040;
                color: #FFFFFF;
            }
        """)
        recording_menu.addAction("최근 녹화 검토", self.review_recording_btn.click)
        recording_menu.addAction("비활성 클릭 핸들 실험실", self.inactive_handle_lab_btn.click)
        recording_menu.addAction("선택 노드 분기 묶기", self.branch_group_btn.click)
        recording_tools.setMenu(recording_menu)

        for command_button in (
            self.inactive_handle_lab_btn, self.branch_group_btn,
        ):
            command_button.setParent(self)
            command_button.hide()

        action_label = QtWidgets.QLabel("추가할 노드 액션")
        action_label.setStyleSheet("color: #8A98B0; font-weight: 600; font-size: 8.5pt;")

        toolbar_tools.addWidget(recording_tools)
        toolbar_tools.addSpacing(8)
        toolbar_tools.addWidget(action_label)
        toolbar_tools.addWidget(self.action_combo)
        toolbar_tools.addWidget(wizard_btn)
        toolbar_tools.addWidget(self.add_node_button)
        toolbar_tools.addWidget(self.record_btn)
        toolbar_tools.addWidget(self.review_recording_btn)
        toolbar_tools.addStretch(1)
        toolbar_tools.addWidget(self.step_test_toolbar_btn)
        toolbar_tools.addWidget(self.run_from_node_toolbar_btn)
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
                "action_test_selected_step": self.step_test_toolbar_btn,
                "action_run_from_selected_node": self.run_from_node_toolbar_btn,
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
        self.macro_panel.hide()
        self.inspector_panel.hide()
        self.macro_panel_toggle.setText("목록 ▶")
        self.inspector_panel_toggle.setText("◀ 설정")
        self.builder_splitter.setSizes([0, 1640, 0])
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
        self.macro_list = MacroListWidget()
        self.macro_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.macro_list.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.macro_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.macro_list.customContextMenuRequested.connect(lambda _pos: self._assign_selected_group())
        self.macro_list.itemClicked.connect(self._toggle_macro_group)
        self.macro_list.currentItemChanged.connect(self._select_macro)
        self.macro_list.delete_requested.connect(self._delete_macro_list_selection)
        self.macro_list.undo_requested.connect(self._undo_macro_list_deletion)
        layout.addWidget(title)
        layout.addWidget(self.search_edit)
        layout.addLayout(group_row)
        layout.addLayout(folder_controls)
        layout.addWidget(self.macro_list, 1)
        return card

    def _build_steps_panel(self) -> QtWidgets.QWidget:
        card = Card()
        card.setMinimumWidth(280)
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
        self.node_more_button.setText("⋯ 더보기 ▾")
        self.node_more_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.node_more_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.node_more_button.setMinimumWidth(88)
        self.node_more_button.setStyleSheet("""
            QToolButton {
                background: #171A22;
                color: #E2E8F0;
                border: 1px solid #2A3040;
                border-radius: 6px;
                padding: 5px 11px;
                font-size: 9pt;
                font-weight: 600;
            }
            QToolButton:hover {
                background: #212634;
                color: #FFFFFF;
                border-color: #3B455C;
            }
            QToolButton::menu-indicator { image: none; }
        """)
        self.node_more_menu = QtWidgets.QMenu(self.node_more_button)
        self.node_more_menu.setStyleSheet("""
            QMenu {
                background: #171A22;
                color: #F2F4F8;
                border: 1px solid #2A3040;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #2A3040;
                color: #FFFFFF;
            }
        """)
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
        self.node_canvas.collapsed_changed.connect(self._graph_collapsed_changed)
        self.node_canvas.comments_changed.connect(self._graph_comments_changed)
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
        self.node_canvas.log_requested.connect(self._open_logs)
        self.node_canvas.image_edit_requested.connect(self._edit_node_search_image)
        self.node_canvas.color_visual_test_requested.connect(self._open_node_color_visual_test)
        self.node_canvas.multi_image_merge_requested.connect(
            lambda indexes: QtCore.QTimer.singleShot(
                0, lambda values=list(indexes): self._merge_graph_image_nodes(values)
            )
        )
        self.node_canvas.multi_color_merge_requested.connect(
            lambda indexes: QtCore.QTimer.singleShot(
                0, lambda values=list(indexes): self._merge_graph_color_nodes(values)
            )
        )
        self.node_canvas.node_title_changed.connect(self._graph_node_title_changed)
        self.node_canvas.archive_requested.connect(self._open_node_archive_dialog)
        self.node_canvas.branch_chain_requested.connect(self._configure_branch_chain)
        self.node_canvas.single_branch_requested.connect(self._configure_single_branch)
        self.node_canvas.unbranch_requested.connect(self._remove_branch_chain)
        self.node_canvas.help_requested.connect(self._open_help_dialog)

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
        self._last_inspector_width = 360
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
        content.setMinimumWidth(330)
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

        cur_conf = int(step.get("confidence") or 86)
        cur_asset_conf = step.get("asset_confidences") if isinstance(step.get("asset_confidences"), dict) else {}
        cur_region = step.get("region") or step.get("search_region")
        if not cur_region and isinstance(step.get("regions"), list) and step["regions"]:
            cur_region = step["regions"][0]
        if isinstance(cur_region, list) and len(cur_region) >= 4:
            try:
                if int(cur_region[2]) <= int(cur_region[0]) or int(cur_region[3]) <= int(cur_region[1]):
                    cur_region = None
            except (TypeError, ValueError):
                cur_region = None
        cur_asset_regions = step.get("asset_regions") if isinstance(step.get("asset_regions"), dict) else {}
        dialog = ImageSearchConfidenceDialog(
            self.repository, aliases, cur_conf, cur_asset_conf, cur_region, self, step=step, asset_regions=cur_asset_regions
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            step["confidence"] = dialog.get_confidence()
            step["asset_confidences"] = dialog.get_asset_confidences()
            new_asset_regs = dialog.get_asset_regions()
            if new_asset_regs:
                step["asset_regions"] = new_asset_regs
                try:
                    min_l = min(int(r[0]) for r in new_asset_regs.values())
                    min_t = min(int(r[1]) for r in new_asset_regs.values())
                    max_r = max(int(r[2]) for r in new_asset_regs.values())
                    max_b = max(int(r[3]) for r in new_asset_regs.values())
                    if max_r > min_l and max_b > min_t:
                        step["region"] = [min_l, min_t, max_r, max_b]
                        step["regions"] = [[min_l, min_t, max_r, max_b]]
                        step["region_mode"] = "client"
                        step["region_coords"] = "relative"
                except Exception:
                    pass
            else:
                step.pop("asset_regions", None)
            if dialog.search_region():
                step["region"] = dialog.search_region()
                step["regions"] = [dialog.search_region()]
                step["region_mode"] = "client"
                step["region_coords"] = "relative"
            elif "region" in step and (step["region"] == [0, 0, 0, 0] or not isinstance(step["region"], list) or len(step["region"]) < 4 or step["region"][2] <= step["region"][0]):
                step.pop("region", None)
                if isinstance(step.get("regions"), list) and step["regions"] == [[0, 0, 0, 0]]:
                    step.pop("regions", None)
            self._persist(f"{step_index}번 노드의 이미지 신뢰도 및 검색 영역 설정을 저장했습니다.")
            self._refresh_steps(row)
            self.status.emit(f"{step_index}번 노드 이미지 신뢰도 저장 완료 (신뢰도: {step['confidence']}%)")

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

    def _delete_macro_list_selection(self) -> None:
        record = self._archive_macros(self._selected_macro_names(), confirm=False)
        window = self.window()
        if isinstance(record, dict) and hasattr(window, "_undo_deletions"):
            window._undo_deletions.append(record)
            window._undo_deletions = window._undo_deletions[-20:]

    def _undo_macro_list_deletion(self) -> None:
        window = self.window()
        restore = getattr(window, "_restore_last_deletion", None)
        if callable(restore):
            restore()

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
        self._execution_issues()
        self._update_warning_badge()

    def _refresh_steps(self, selected: int = 0) -> None:
        steps = list((self.current_macro or {}).get("steps") or [])
        self.steps_table.blockSignals(True)
        self.steps_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            success_candidates = step.get("success_candidates") or []
            success_text = (
                "후보 " + ",".join(str(int(value)) for value in success_candidates)
                if isinstance(success_candidates, list) and len(success_candidates) > 1
                else str(step.get("on_success") or "자동")
            )
            values = [
                str(row + 1),
                str(step.get("action") or ""),
                str(step.get("label") or self._step_summary(step)),
                success_text,
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
        self._open_action_settings()

    def _toggle_builder_side(self, side: str, force_open: bool = False) -> None:
        if not hasattr(self, "builder_splitter"):
            return
        is_macro = side == "macro"
        panel = self.macro_panel if is_macro else self.inspector_panel
        button = self.macro_panel_toggle if is_macro else self.inspector_panel_toggle
        visible = panel.isVisibleTo(self) and panel.width() > 0
        if force_open or not visible:
            if not is_macro:
                panel.setMinimumWidth(360)
                preferred = max(360, getattr(self, "_last_inspector_width", 360))
            else:
                panel.setMinimumWidth(190)
                preferred = 230
            panel.show()
            sizes = self.builder_splitter.sizes()
            index = 0 if is_macro else 2
            if len(sizes) == 3:
                sizes[index] = preferred
                sizes[1] = max(280, sizes[1] - preferred)
                self.builder_splitter.setSizes(sizes)
            button.setText("◀ 목록" if is_macro else "설정 ▶")
            return
        if not is_macro:
            if panel.width() >= 360:
                self._last_inspector_width = panel.width()
            panel.setMinimumWidth(0)
        else:
            panel.setMinimumWidth(0)
        panel.hide()
        sizes = self.builder_splitter.sizes()
        index = 0 if is_macro else 2
        if len(sizes) == 3:
            sizes[1] += sizes[index]
            sizes[index] = 0
            self.builder_splitter.setSizes(sizes)
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

    @QtCore.Slot(list)
    def _graph_collapsed_changed(self, indexes: list[int]) -> None:
        if self.current_macro is None:
            return
        if indexes:
            self.current_macro["graph_collapsed"] = sorted({int(index) for index in indexes if int(index) > 0})
        else:
            self.current_macro.pop("graph_collapsed", None)
        self._graph_save_timer.start()

    @QtCore.Slot(list)
    def _graph_comments_changed(self, comments: list[dict]) -> None:
        if self.current_macro is None:
            return
        if comments:
            self.current_macro["graph_comments"] = comments
        else:
            self.current_macro.pop("graph_comments", None)
        self._graph_save_timer.start()

    def _save_graph_positions(self) -> None:
        if not self.current_name or self.current_macro is None:
            return
        self._persist("노드 위치를 저장했습니다.")

    @QtCore.Slot(int, int, str)
    def _connect_graph_nodes(self, source: int, target: int, kind: str) -> None:
        if hasattr(self, "node_canvas") and self.node_canvas:
            self.node_canvas.include_connected_nodes_in_comment(source, target)
        steps = (self.current_macro or {}).get("steps") or []
        if not (0 < source <= len(steps) and 0 < target <= len(steps)):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        delay_field = "on_fail_delay" if kind == "fail" else "on_success_delay"
        if kind == "success":
            old_target = self.node_canvas.retargeting_target(source, kind)
            current = steps[source - 1].get("success_candidates") or []
            candidates = [int(value) for value in current if str(value).lstrip("-").isdigit()] if isinstance(current, list) else []
            if not candidates and int(steps[source - 1].get("on_success") or 0):
                candidates = [int(steps[source - 1]["on_success"])]
            if old_target:
                candidates = [target if value == old_target else value for value in candidates]
            elif target not in candidates:
                candidates.append(target)
            configure_success_candidates(steps, source, candidates)

        else:
            steps[source - 1][field] = target
        steps[source - 1].setdefault(delay_field, 300)
        self._persist(f"{source}번 노드의 {'실패' if kind == 'fail' else '성공'} 흐름을 {target}번에 연결했습니다.")
        QtCore.QTimer.singleShot(0, lambda: self._refresh_steps(source - 1))

    @QtCore.Slot(list)
    def _merge_graph_image_nodes(self, indexes: list[int]) -> None:
        if self.current_macro is None:
            return
        steps = self.current_macro.get("steps") or []
        selected = sorted({int(index) for index in indexes if 0 < int(index) <= len(steps)})
        if len(selected) < 2 or any(str(steps[index - 1].get("action") or "") != "image_search" for index in selected):
            self.status.emit("이미지 서치 노드를 2개 이상 선택해 주세요.")
            return
        selected_set = set(selected)
        primary_index = selected[0]
        primary = steps[primary_index - 1]
        aliases: list[str] = []
        offsets: dict[str, list[int]] = {}
        for index in selected:
            member = steps[index - 1]
            member_aliases = [
                str(value) for value in member.get("assets") or [] if str(value).strip()
            ] if isinstance(member.get("assets"), list) else []
            member_primary = str(member.get("asset") or "").strip()
            if member_primary and member_primary not in member_aliases:
                member_aliases.insert(0, member_primary)
            stored_offsets = member.get("asset_offsets") if isinstance(member.get("asset_offsets"), dict) else {}
            click = member.get("click") if isinstance(member.get("click"), dict) else {}
            fallback_offset = list(click.get("offset") or [0, 0])[:2]
            for alias in member_aliases:
                if alias not in aliases:
                    aliases.append(alias)
                value = stored_offsets.get(alias, fallback_offset)
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    offsets[alias] = [int(value[0] or 0), int(value[1] or 0)]
        if len(aliases) < 2:
            self.status.emit("선택한 노드에서 서로 다른 검색 이미지를 2개 이상 찾지 못했습니다.")
            return

        def external_target(field: str) -> int:
            for index in reversed(selected):
                target = int(steps[index - 1].get(field) or 0)
                if target and target not in selected_set:
                    return target
            if field == "on_success" and selected[-1] < len(steps):
                return selected[-1] + 1
            return 0

        success_target = external_target("on_success")
        fail_target = external_target("on_fail")
        primary["asset"] = aliases[0]
        primary["assets"] = aliases
        primary["asset_offsets"] = offsets
        primary["engine"] = "opencv"
        primary["label"] = f"멀티 이미지 서치 {len(aliases)}개"
        primary.pop("stop_on_success", None)
        if success_target:
            primary["on_success"] = success_target
        else:
            primary.pop("on_success", None)
        if fail_target:
            primary["on_fail"] = fail_target
        else:
            primary.pop("on_fail", None)
        automation = primary.get("_automation") if isinstance(primary.get("_automation"), dict) else {}
        automation.update({"manual_multi_merge": True, "image_count": len(aliases)})
        primary["_automation"] = automation
        for index in reversed(selected[1:]):
            removed = steps.pop(index - 1)
            self.current_macro.setdefault("meta", {}).setdefault("archived_steps", []).append(removed)
            self._normalize_edges_after_delete(index)
        self._persist(f"이미지 서치 {len(selected)}개를 멀티 이미지 서치 {len(aliases)}개로 묶었습니다.")
        self._refresh_steps(primary_index - 1)

    def _merge_graph_color_nodes(self, indexes: list[int]) -> None:
        if self.current_macro is None:
            return
        steps = self.current_macro.get("steps") or []
        selected = sorted({int(index) for index in indexes if 0 < int(index) <= len(steps)})
        if len(selected) < 2 or any(str(steps[index - 1].get("action") or "") != "pixel_search" for index in selected):
            self.status.emit("색상 서치 노드를 2개 이상 선택해 주세요.")
            return
        selected_set = set(selected)
        primary_index = selected[0]
        primary = steps[primary_index - 1]
        colors: list[str] = []
        tolerances: dict[str, int] = {}
        regions: dict[str, list[int]] = {}
        for index in selected:
            member = steps[index - 1]
            member_colors = [
                str(c).strip() for c in member.get("colors") or [] if str(c).strip()
            ] if isinstance(member.get("colors"), list) else []
            member_primary = str(member.get("color") or "").strip()
            if member_primary and member_primary not in member_colors:
                member_colors.insert(0, member_primary)
            member_tols = member.get("color_tolerances") if isinstance(member.get("color_tolerances"), dict) else {}
            fallback_tol = int(member.get("tolerance") or 10)
            member_regs = member.get("color_regions") if isinstance(member.get("color_regions"), dict) else {}
            fallback_reg = member.get("search_region") or member.get("region")
            if not isinstance(fallback_reg, list) or len(fallback_reg) < 4:
                fallback_reg = [0, 0, 0, 0]
            for c in member_colors:
                if c not in colors:
                    colors.append(c)
                tolerances[c] = int(member_tols.get(c, fallback_tol))
                if c in member_regs and isinstance(member_regs[c], list) and len(member_regs[c]) >= 4:
                    regions[c] = list(member_regs[c][:4])
                else:
                    regions[c] = list(fallback_reg[:4])

        if len(colors) < 2:
            self.status.emit("선택한 노드에서 서로 다른 검색 색상을 2개 이상 찾지 못했습니다.")
            return

        def external_target(field: str) -> int:
            for index in reversed(selected):
                target = int(steps[index - 1].get(field) or 0)
                if target and target not in selected_set:
                    return target
            if field == "on_success" and selected[-1] < len(steps):
                return selected[-1] + 1
            return 0

        success_target = external_target("on_success")
        fail_target = external_target("on_fail")
        primary["color"] = colors[0]
        primary["colors"] = colors
        primary["color_tolerances"] = tolerances
        primary["color_regions"] = regions
        primary["tolerance"] = tolerances.get(colors[0], 10)
        primary["label"] = f"멀티 색상 서치 {len(colors)}개"
        primary["match_condition"] = "all_matched"
        primary["required_count"] = len(colors)
        primary.pop("stop_on_success", None)
        if success_target:
            primary["on_success"] = success_target
        else:
            primary.pop("on_success", None)
        if fail_target:
            primary["on_fail"] = fail_target
        else:
            primary.pop("on_fail", None)

        valid_regs = [r for r in regions.values() if isinstance(r, (list, tuple)) and len(r) >= 4 and (r[0] or r[1] or r[2] or r[3])]
        if valid_regs:
            min_l = min(int(r[0]) for r in valid_regs)
            min_t = min(int(r[1]) for r in valid_regs)
            max_r = max(int(r[2]) for r in valid_regs)
            max_b = max(int(r[3]) for r in valid_regs)
            primary["search_region"] = [min_l, min_t, max_r, max_b]
            primary["region"] = [min_l, min_t, max_r, max_b]

        automation = primary.get("_automation") if isinstance(primary.get("_automation"), dict) else {}
        automation.update({"manual_multi_merge": True, "color_count": len(colors)})
        primary["_automation"] = automation
        for index in reversed(selected[1:]):
            removed = steps.pop(index - 1)
            self.current_macro.setdefault("meta", {}).setdefault("archived_steps", []).append(removed)
            self._normalize_edges_after_delete(index)
        self._persist(f"색상 서치 {len(selected)}개를 멀티 색상 서치 {len(colors)}개로 묶었습니다.")
        self._refresh_steps(primary_index - 1)

    def _open_node_color_visual_test(self, step_index: int) -> None:
        steps = list((self.current_macro or {}).get("steps") or [])
        row = int(step_index) - 1
        if not 0 <= row < len(steps):
            return
        step = steps[row]
        if str(step.get("action") or "") != "pixel_search":
            return
        from .region_visual_test import RegionVisualTestDialog
        dlg = RegionVisualTestDialog(step, self.repository, parent=self.window())
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            updated_color_regs = dlg.get_color_regions()
            if updated_color_regs:
                step["color_regions"] = updated_color_regs
            updated_tols = dlg.get_color_tolerances()
            if updated_tols:
                step["color_tolerances"] = updated_tols
                if step.get("color") in updated_tols:
                    step["tolerance"] = updated_tols[step["color"]]
                elif updated_tols:
                    step["tolerance"] = next(iter(updated_tols.values()))
            bounding = dlg.get_bounding_region()
            if bounding:
                step["search_region"] = bounding
                step["region"] = bounding
            self._persist(f"{step_index}번 노드의 색상 및 검색 영역을 보정했습니다.")
            self._refresh_steps(row)

    @QtCore.Slot(int, int, str)
    def _delete_graph_edge(self, source: int, target: int, kind: str) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        delay_field = "on_fail_delay" if kind == "fail" else "on_success_delay"
        candidates = steps[source - 1].get("success_candidates") or []
        if kind == "success" and isinstance(candidates, list) and target in [int(value) for value in candidates]:
            configure_success_candidates(steps, source, [int(value) for value in candidates if int(value) != target])
        else:
            if int(steps[source - 1].get(field) or 0) != target:
                return
            steps[source - 1].pop(field, None)
        if kind != "success" or not steps[source - 1].get("success_candidates"):
            steps[source - 1].pop(delay_field, None)
        self._persist(f"{source}번 노드의 연결을 끊었습니다.")
        QtCore.QTimer.singleShot(0, lambda: self._refresh_steps(source - 1))

    @QtCore.Slot(int, int, str)
    def _set_graph_edge_delay(self, source: int, target: int, kind: str) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        if not 0 < source <= len(steps):
            return
        field = "on_fail" if kind == "fail" else "on_success"
        candidate_targets = steps[source - 1].get("success_candidates") or []
        if int(steps[source - 1].get(field) or 0) != target and not (
            kind == "success" and isinstance(candidate_targets, list) and target in [int(value) for value in candidate_targets]
        ):
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
        QtCore.QTimer.singleShot(0, lambda: self._refresh_steps(source - 1))

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

    @QtCore.Slot(int, str)
    def _graph_node_title_changed(self, index: int, new_title: str) -> None:
        if self.current_macro is None:
            return
        steps = self.current_macro.get("steps") if isinstance(self.current_macro.get("steps"), list) else []
        if 1 <= index <= len(steps):
            steps[index - 1]["label"] = new_title
            self._persist(f"{index}번 노드 이름을 '{new_title}'(으)로 변경했습니다.")
            self._refresh_steps(index - 1)
            self.status.emit(f"{index}번 노드 이름 변경 완료: {new_title}")

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
        self.node_canvas.update_archive_count(len(self.current_macro.get("meta", {}).get("archived_steps", [])))
        self.status.emit(f"📦 {len(valid)}개 노드를 보관함으로 이동했습니다. (언제든 '보관함' 버튼을 눌러 복원 가능)")
        return True

    @QtCore.Slot()
    def _open_node_archive_dialog(self) -> None:
        if self.current_macro is None:
            self.status.emit("열려 있는 매크로가 없습니다.")
            return
        archived_steps = self.current_macro.setdefault("meta", {}).setdefault("archived_steps", [])

        def do_restore(selected_rows: list[int]) -> None:
            steps = self.current_macro.setdefault("steps", [])
            restored = []
            for r in sorted(selected_rows, reverse=True):
                if 0 <= r < len(archived_steps):
                    node = archived_steps.pop(r)
                    restored.append(node)
            restored.reverse()
            for node in restored:
                node.pop("on_success", None)
                node.pop("on_fail", None)
                steps.append(node)
            self._persist(f"보관함에서 {len(restored)}개 노드를 캔버스로 복원했습니다.")
            self._refresh_steps(len(steps) - 1)
            self.node_canvas.update_archive_count(len(archived_steps))
            self.status.emit(f"보관함에서 {len(restored)}개 노드를 캔버스 끝에 복원했습니다.")

        def do_delete(selected_rows: list[int]) -> None:
            for r in sorted(selected_rows, reverse=True):
                if 0 <= r < len(archived_steps):
                    archived_steps.pop(r)
            self._persist(f"보관함에서 {len(selected_rows)}개 노드를 영구 삭제했습니다.")
            self.node_canvas.update_archive_count(len(archived_steps))
            self.status.emit(f"보관함에서 {len(selected_rows)}개 노드를 영구 삭제했습니다.")

        dlg = NodeArchiveDialog(self, archived_steps, do_restore, do_delete)
        dlg.exec()

    @QtCore.Slot()
    def _open_help_dialog(self) -> None:
        dlg = MacroHelpDialog(self)
        dlg.exec()

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

    def _add_step(self, action_override: Any = None) -> None:
        if not self.current_macro:
            self.status.emit("먼저 매크로를 선택하세요.")
            return
        action = action_override if isinstance(action_override, str) and action_override else self._selected_action(self.action_combo)
        steps = self.current_macro.setdefault("steps", [])

        modifiers = QtWidgets.QApplication.keyboardModifiers()
        is_shift_held = bool(modifiers & QtCore.Qt.ShiftModifier)

        interactive_actions = {
            "image_search", "screen_condition", "inactive_click", "mouse_click",
            "pixel_search", "ocr", "ocr_tracking", "multi_pixel_check", "wait_color", "color_ratio",
        }

        step = None
        if not is_shift_held and action in interactive_actions:
            try:
                step = QuickActionWizard.build(action, self.repository, self.window())
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "스마트 설정 실패", f"{exc}")
                return
            if step is None:
                self.status.emit(f"{ACTION_LABELS.get(action, action)} 추가가 취소되었습니다.")
                return

        if step is None:
            step = deepcopy(ACTION_TEMPLATES.get(action, {}))
            step["action"] = action

        if action in {"image_search", "screen_condition"} and not step.get("region_window_exe"):
            step.setdefault("engine", "opencv")
            step.setdefault("search_profile", "precise")
            step.setdefault("confidence", 85)
            step.setdefault("wait_condition", "appear")
            for candidate in reversed(steps):
                c_exe = str(candidate.get("window_exe") or candidate.get("region_window_exe") or (candidate.get("click") or {}).get("window_exe") or "")
                c_win = str(candidate.get("window") or candidate.get("region_window") or (candidate.get("click") or {}).get("window") or "")
                if c_exe:
                    step["region_mode"] = "client"
                    step["region_coords"] = "relative"
                    step["region_window_exe"] = c_exe
                    step["region_window"] = c_win
                    click = step.setdefault("click", {})
                    click["mode"] = "inactive"
                    click["window_exe"] = c_exe
                    click["window"] = c_win
                    click["click_image"] = True
                    break

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
                    if new_index > 1 and (new_index - 1) % 10 == 0:
                        new_x = 0.0
                        new_y = source_y + 220.0
                    else:
                        new_x, new_y = source_x + 240.0, source_y
                positions[str(new_index)] = [round(new_x, 2), round(new_y, 2)]
        action_name = ACTION_LABELS.get(action, action)
        if source:
            relation = "삽입" if previous_target else "연결"
            message = f"{new_index}번 {action_name} 노드를 {source}번 성공 흐름에 자동 {relation}했습니다."
        else:
            message = f"{action_name} 단계를 추가했습니다."
        self.action_editor.refresh_sources()
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

    @QtCore.Slot(list)
    def _configure_branch_chain(self, selected_indexes: list[int]) -> None:
        if not self.current_macro:
            return
        steps = self.current_macro.get("steps") or []
        if not steps:
            return

        candidates = sorted(set(int(idx) for idx in selected_indexes if 0 < int(idx) <= len(steps)))
        if len(candidates) < 2:
            # Open selection dialog so user can select nodes
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("🔀 순차 분기 묶기 (실패 시 다음 분기 실행)")
            dlg.setStyleSheet("background: #0F141C; color: #EDF2F7;")
            dlg.setMinimumWidth(480)
            layout = QtWidgets.QVBoxLayout(dlg)

            info = QtWidgets.QLabel(
                "분기 체인으로 연결할 노드들을 선택하세요.\n"
                "선택한 순서대로 [1번 실패 시 ➔ 2번 실행 ➔ 2번 실패 시 ➔ 3번 실행]으로 연결됩니다."
            )
            info.setStyleSheet("color: #70C5FF; font-size: 9.5pt; font-weight: bold; margin-bottom: 8px;")
            layout.addWidget(info)

            list_widget = QtWidgets.QListWidget()
            list_widget.setStyleSheet(
                "QListWidget { background: #151D2A; border: 1px solid #2B3A4C; border-radius: 6px; padding: 4px; font-size: 9pt; }\n"
                "QListWidget::item { padding: 6px; border-bottom: 1px solid #1E2838; }\n"
                "QListWidget::item:selected { background: #23364D; }"
            )
            for idx, s in enumerate(steps, start=1):
                act_name = ACTION_TITLES.get(str(s.get("action")), str(s.get("action")))
                label = str(s.get("label") or act_name)
                item = QtWidgets.QListWidgetItem(f"#{idx} [{act_name}] {label}")
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                item.setCheckState(QtCore.Qt.Checked if idx in candidates else QtCore.Qt.Unchecked)
                item.setData(QtCore.Qt.UserRole, idx)
                list_widget.addItem(item)
            layout.addWidget(list_widget)

            btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            btn_box.button(QtWidgets.QDialogButtonBox.Ok).setText("🔀 분기 체인 생성")
            btn_box.button(QtWidgets.QDialogButtonBox.Cancel).setText("취소")
            btn_box.accepted.connect(dlg.accept)
            btn_box.rejected.connect(dlg.reject)
            layout.addWidget(btn_box)

            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return

            picked = []
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if it.checkState() == QtCore.Qt.Checked:
                    picked.append(it.data(QtCore.Qt.UserRole))
            candidates = sorted(picked)

        if len(candidates) < 2:
            self.status.emit("분기로 연결할 노드를 2개 이상 선택해야 합니다.")
            return

        for position, index in enumerate(candidates):
            step = steps[index - 1]
            step["abort_on_fail"] = False
            if position + 1 < len(candidates):
                step["on_fail"] = candidates[position + 1]
            else:
                step.pop("on_fail", None)
                step["abort_on_fail"] = True

            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            automation["branch_chain"] = True
            automation["candidate_position"] = position + 1
            automation["candidate_count"] = len(candidates)
            automation["hide_candidate_fail_edge"] = True
            step["_automation"] = automation

            # Assign workflow lane so colored bounding area border renders around each branch (like Smart Recording F7)
            step["workflow_id"] = f"branch-lane-{index}"
            step["workflow_label"] = f"{position + 1}번 분기"

        chain_desc = " ➔ ".join(f"#{i}" for i in candidates)
        self._persist(f"순차 분기 체인 생성 ({chain_desc})")
        self._refresh_steps(candidates[0] - 1)
        self.status.emit(f"🔀 순차 분기 연결 완료: {chain_desc} (실패 시 다음 분기 순차 실행)")

    @QtCore.Slot(int)
    def _configure_single_branch(self, source_index: int) -> None:
        if not self.current_macro:
            return
        steps = self.current_macro.get("steps") or []
        if not (0 < source_index <= len(steps)):
            return

        source_step = steps[source_index - 1]
        source_name = ACTION_TITLES.get(str(source_step.get("action")), str(source_step.get("action")))

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("🔀 실패 시 다음 분기 대상 노드 선택")
        dlg.setStyleSheet("background: #0F141C; color: #EDF2F7;")
        dlg.setMinimumWidth(420)
        layout = QtWidgets.QVBoxLayout(dlg)

        info = QtWidgets.QLabel(f"#{source_index} [{source_name}] 노드가 실행 실패했을 때\n다음으로 분기 실행할 대상 노드를 선택하세요:")
        info.setStyleSheet("color: #70C5FF; font-size: 9.5pt; font-weight: bold; margin-bottom: 8px;")
        layout.addWidget(info)

        combo = QtWidgets.QComboBox()
        combo.setStyleSheet("background: #151D2A; border: 1px solid #2B3A4C; padding: 6px; border-radius: 4px; font-size: 9pt;")
        current_fail = int(source_step.get("on_fail") or 0)
        default_idx = -1
        for idx, s in enumerate(steps, start=1):
            if idx == source_index:
                continue
            act_name = ACTION_TITLES.get(str(s.get("action")), str(s.get("action")))
            label = str(s.get("label") or act_name)
            combo.addItem(f"#{idx} [{act_name}] {label}", idx)
            if idx == current_fail:
                default_idx = combo.count() - 1

        if default_idx >= 0:
            combo.setCurrentIndex(default_idx)
        elif combo.count() > 0:
            next_idx = combo.findData(source_index + 1)
            if next_idx >= 0:
                combo.setCurrentIndex(next_idx)

        layout.addWidget(combo)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btn_box.button(QtWidgets.QDialogButtonBox.Ok).setText("분기 연결")
        btn_box.button(QtWidgets.QDialogButtonBox.Cancel).setText("취소")
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        target_idx = int(combo.currentData() or 0)
        if 0 < target_idx <= len(steps):
            source_step["on_fail"] = target_idx
            source_step["abort_on_fail"] = False
            automation = source_step.get("_automation") if isinstance(source_step.get("_automation"), dict) else {}
            automation["branch_chain"] = True
            automation["hide_candidate_fail_edge"] = False
            source_step["_automation"] = automation
            self._persist(f"#{source_index} 노드 실패 시 #{target_idx} 분기 연결")
            self._refresh_steps(source_index - 1)
            self.status.emit(f"🔀 분기 연결 완료: #{source_index} 실패 시 ➔ #{target_idx} 실행")

    @QtCore.Slot(list)
    def _remove_branch_chain(self, selected_indexes: list[int]) -> None:
        if not self.current_macro:
            return
        steps = self.current_macro.get("steps") or []
        count = 0
        for idx in selected_indexes:
            if 0 < idx <= len(steps):
                steps[idx - 1].pop("on_fail", None)
                automation = steps[idx - 1].get("_automation")
                if isinstance(automation, dict):
                    automation.pop("branch_chain", None)
                    automation.pop("candidate_position", None)
                    automation.pop("candidate_count", None)
                    if not automation:
                        steps[idx - 1].pop("_automation", None)
                if str(steps[idx - 1].get("workflow_id") or "").startswith("branch-lane-"):
                    steps[idx - 1].pop("workflow_id", None)
                    steps[idx - 1].pop("workflow_label", None)
                count += 1
        self._persist(f"선택한 {count}개 노드의 분기 연결을 해제했습니다.")
        self._refresh_steps(selected_indexes[0] - 1 if selected_indexes else 0)
        self.status.emit("선택 노드 분기 해제 완료")

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
        start_candidate_offsets: list[int] = []
        for offset, step in enumerate(prepared, start=1):
            if bool(step.pop("_start_search_candidate", False)):
                start_candidate_offsets.append(offset)
        for step in prepared:
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                target = int(step.get(field) or 0)
                if target:
                    step[field] = target + base
            if isinstance(step.get("success_candidates"), list):
                step["success_candidates"] = [int(value) + base for value in step["success_candidates"]]
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            for field in ("success_candidate_source", "candidate_original_on_fail"):
                value = int(automation.get(field) or 0)
                if value:
                    automation[field] = value + base
            conditions = step.get("edge_conditions") or []
            if isinstance(conditions, list):
                for rule in conditions:
                    if isinstance(rule, dict) and int(rule.get("target") or 0):
                        rule["target"] = int(rule["target"]) + base
        for step in prepared:
            step.pop("_live_position", None)
            if not str(step.get("workflow_id") or "").strip():
                step.pop("workflow_id", None)
                step.pop("workflow_label", None)

        graph_positions = self.current_macro.setdefault("graph_positions", {})
        existing_x = [
            float(value[0])
            for value in graph_positions.values()
            if isinstance(value, (list, tuple)) and len(value) >= 2
        ]
        start_x = (max(existing_x) + 260.0) if (base > 0 and existing_x) else 0.0
        if base == 0:
            graph_positions.clear()

        wf_offsets: dict[str, int] = {}
        wf_rows: dict[str, int] = {}
        for offset, step in enumerate(prepared, start=1):
            wf_id = str(step.get("workflow_id") or "").strip()
            if wf_id:
                if wf_id not in wf_rows:
                    wf_rows[wf_id] = len(wf_rows)
                    wf_offsets[wf_id] = 0
                col = wf_offsets[wf_id] % 10
                wrap = wf_offsets[wf_id] // 10
                wf_offsets[wf_id] += 1
                row = wf_rows[wf_id]
                px = round(start_x + col * 240.0, 2)
                py = round(row * 160.0 + wrap * 220.0, 2)
            else:
                col = (offset - 1) % 10
                wrap = (offset - 1) // 10
                px = round(start_x + col * 240.0, 2)
                py = round(wrap * 220.0, 2)
            graph_positions[str(base + offset)] = [px, py]

        steps.extend(prepared)
        if start_candidate_offsets and base == 0:
            candidates = [base + offset for offset in start_candidate_offsets]
            self.current_macro["start_search_candidates"] = candidates
            self.current_macro["graph_start_step"] = candidates[0]
        self.current_macro.setdefault("meta", {})["automation_test_version"] = "2.19.17-test"
        # Image-mode recording may have created assets moments ago. Refresh
        # the inspector before loading the generated step so its asset is not
        # replaced with the empty first row by a stale combo box.
        self.action_editor.refresh_sources()
        self._persist(message)
        self._refresh_steps(len(steps) - len(prepared))
        if base == 0:
            QtCore.QTimer.singleShot(0, self.node_canvas.auto_layout)
        else:
            QtCore.QTimer.singleShot(0, self.node_canvas.fit_all)

    def _start_smart_recording(self) -> None:
        if not self.current_macro:
            QtWidgets.QMessageBox.information(
                self,
                "스마트 녹화",
                "녹화한 동작을 추가할 매크로를 먼저 선택하거나 생성해주세요."
            )
            return
        if self._recording_controller is not None:
            proc = getattr(self._recording_controller, "process", None)
            if proc is not None and proc.state() != QtCore.QProcess.NotRunning:
                bar = getattr(self._recording_controller, "bar", None)
                if bar is not None:
                    bar.show()
                    bar.raise_()
                    bar.activateWindow()
                self.status.emit("스마트 녹화 바가 이미 실행 중입니다. 화면 상단을 확인하세요.")
                return
            else:
                try:
                    self._recording_controller.stop()
                except Exception:
                    pass
                self._recording_controller = None
        if not self._confirm_smart_recording():
            return
        controller = SmartRecordingController(self.repository, self.window())
        controller.completed.connect(self._review_smart_recording)
        controller.failed.connect(self._smart_recording_failed)
        self._recording_controller = controller
        controller.start()
        self.status.emit("스마트 녹화 준비 · 실시간 노드맵 · F5 확인 · F7 새 작업 · F8 캡처 · F10 종료")

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
            "F5는 현재 커서의 이미지를 '결과 확인' 노드로, F6은 1초 대기 노드로 추가합니다.\n"
            "F7은 새 작업 분기를 시작하고, F11은 일반/분기 모드, F12는 기록 ON/OFF를 전환합니다.\n"
            "우클릭과 F5는 동일하게 실제 클릭 없이 해당 위치의 이미지를 화면 확인 노드로 기록합니다.\n"
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
        if self._recording_review_dialog is not None:
            self._recording_review_dialog.show()
            self._recording_review_dialog.raise_()
            self._recording_review_dialog.activateWindow()
            return
        dialog = RecordingReviewDialog(events, self.repository, self.window())
        dialog.setWindowModality(QtCore.Qt.NonModal)
        self._recording_review_dialog = dialog
        dialog.events_changed.connect(self._remember_last_recording)
        dialog.accepted.connect(lambda: QtCore.QTimer.singleShot(0, lambda: self._finish_smart_recording_review(dialog)))
        dialog.rejected.connect(lambda: self._close_smart_recording_review(dialog))
        dialog.ai_macro_ready.connect(self._create_macro_from_ai_plan)
        self.ai_macro_save_result.connect(dialog._on_ai_macro_save_result)
        dialog.show()

    def _close_smart_recording_review(self, dialog: RecordingReviewDialog) -> None:
        if self._recording_review_dialog is dialog:
            self._recording_review_dialog = None
        dialog.deleteLater()
        self.status.emit("검토창을 닫았습니다. '녹화 상세편집'에서 다시 열 수 있습니다.")

    def _create_macro_from_ai_plan(self, draft: dict[str, Any]) -> None:
        try:
            validate_compiled_draft(draft)
            base_name = str(draft.get("name") or "AI자동매크로").strip()
            payload = {
                "name": base_name,
                "description": f"스마트 녹화 기반 AI 생성 매크로 ({draft.get('meta', {}).get('recording_id', '')})",
                "steps": draft.get("steps", []),
                "graph_start_step": draft.get("graph_start_step", 1),
                "graph_positions": draft.get("graph_positions", {}),
                "meta": draft.get("meta", {}),
            }
            name, path = self.repository.create_macro_unique(base_name, payload)
        except Exception as exc:
            msg = str(exc)
            self.ai_macro_save_result.emit(False, msg)
            return

        self.ai_macro_save_result.emit(True, f"'{name}' 매크로가 성공적으로 생성되었습니다.")
        self.refresh(name)
        self.data_changed.emit()
        self.status.emit(f"AI 매크로 '{name}' 생성 완료 (노드 {len(payload['steps'])}개). 상단 '🛡️ 드라이런'으로 먼저 안전하게 테스트하세요.")
        QtWidgets.QMessageBox.information(
            self,
            "AI 매크로 생성 완료",
            f"'{name}' 매크로가 성공적으로 생성되어 캔버스에 로드되었습니다.\n\n"
            f"• 총 {len(payload['steps'])}개의 노드와 분기선(성공/실패)이 자동 연결되었습니다.\n"
            f"• 안전을 위해 매크로가 자동으로 실행되지 않습니다.\n"
            f"• 상단 툴바의 '🛡️ 드라이런' 또는 단계별 디버깅을 사용하여 안전하게 동작을 검증하세요.",
        )

    def _finish_smart_recording_review(self, dialog: RecordingReviewDialog) -> None:
        target_mode = getattr(dialog, "target_mode", "append")
        if target_mode == "ai_plan":
            if self._recording_review_dialog is dialog:
                self._recording_review_dialog = None
            dialog.deleteLater()
            return
        try:
            steps = dialog.build_steps()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "녹화 변환 실패", str(exc))
            return

        if self._recording_review_dialog is dialog:
            self._recording_review_dialog = None
        dialog.deleteLater()

        if target_mode == "new":
            from datetime import datetime
            default_name = f"스마트녹화-{datetime.now():%Y%m%d-%H%M%S}"
            dlg = MacroDialog("새 매크로 생성", default_name, parent=self)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                self.status.emit("새 매크로 생성을 취소했습니다.")
                return
            macro_name = dlg.name_edit.text().strip()
            macro_desc = dlg.description_edit.text().strip()
            if not macro_name:
                macro_name = default_name
            try:
                path = self.repository.create_macro(macro_name, macro_desc)
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "생성 실패", str(exc))
                return
            self.refresh(path.stem)
            self._append_automation_steps(steps, f"스마트 녹화로 '{path.stem}' 매크로를 생성했습니다.")
            self.data_changed.emit()
            self.status.emit(f"'{path.stem}' 새 매크로에 녹화 노드가 저장되었습니다.")
        else:
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
            if steps:
                index = 1
                self.node_canvas.set_selected_index(1)
                self.steps_table.selectRow(0)
            else:
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

    def _show_step_test_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self.step_test_toolbar_btn)
        act_step = menu.addAction("▷ 선택 단계만 테스트 (Ctrl+Shift+T)")
        act_step.triggered.connect(self._test_selected_step)
        act_from = menu.addAction("▶ 선택 노드부터 이어서 실행")
        act_from.triggered.connect(self._resume_from_selected_node)
        menu.exec(self.step_test_toolbar_btn.mapToGlobal(pos))

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
            candidates = step.get("success_candidates") or []
            if isinstance(candidates, list):
                remapped_candidates = [mapping[int(value)] for value in candidates if int(value) in mapping]
                if len(remapped_candidates) > 1:
                    step["success_candidates"] = remapped_candidates
                    step["on_success"] = remapped_candidates[0]
                else:
                    step.pop("success_candidates", None)
                    if remapped_candidates:
                        step["on_success"] = remapped_candidates[0]
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            candidate_source = int(automation.get("success_candidate_source") or 0)
            if candidate_source:
                if candidate_source in mapping:
                    automation["success_candidate_source"] = mapping[candidate_source]
                else:
                    for field in (
                        "success_candidate_source",
                        "success_candidate_position",
                        "success_candidate_count",
                        "candidate_original_fail_present",
                        "candidate_original_on_fail",
                        "candidate_original_abort_present",
                        "candidate_original_abort_on_fail",
                    ):
                        automation.pop(field, None)
            original_fail = int(automation.get("candidate_original_on_fail") or 0)
            if original_fail:
                if original_fail in mapping:
                    automation["candidate_original_on_fail"] = mapping[original_fail]
                else:
                    automation["candidate_original_on_fail"] = 0
                    automation["candidate_original_fail_present"] = False
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
            candidates = step.get("success_candidates") or []
            if isinstance(candidates, list):
                for value in candidates:
                    try:
                        target = int(value)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= target <= len(steps):
                        valid_keys.add(f"{source}:success:{target}:-1")
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

    def _remap_graph_collapsed(self, mapping: dict[int, int]) -> None:
        if self.current_macro is None:
            return
        collapsed = self.current_macro.get("graph_collapsed") or []
        remapped = sorted(
            {
                mapping[index]
                for value in collapsed if str(value).lstrip("-").isdigit()
                for index in [int(value)]
                if index in mapping
            }
        ) if isinstance(collapsed, list) else []
        if remapped:
            self.current_macro["graph_collapsed"] = remapped
        else:
            self.current_macro.pop("graph_collapsed", None)

    def _normalize_edges_after_delete(self, deleted: int) -> None:
        steps = (self.current_macro or {}).get("steps") or []
        old_count = len(steps) + 1
        for step in steps:
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            if int(automation.get("success_candidate_source") or 0) == deleted:
                if automation.pop("candidate_original_fail_present", False):
                    step["on_fail"] = int(automation.pop("candidate_original_on_fail", 0) or 0)
                else:
                    step.pop("on_fail", None)
                    automation.pop("candidate_original_on_fail", None)
                if automation.pop("candidate_original_abort_present", False):
                    step["abort_on_fail"] = bool(automation.pop("candidate_original_abort_on_fail", False))
                else:
                    step.pop("abort_on_fail", None)
                    automation.pop("candidate_original_abort_on_fail", None)
                for field in ("success_candidate_source", "success_candidate_position", "success_candidate_count"):
                    automation.pop(field, None)
            for field in ("on_success", "on_fail", "target_step", "jump_to"):
                value = int(step.get(field) or 0)
                if value == deleted:
                    step.pop(field, None)
                elif value > deleted:
                    step[field] = value - 1
            if isinstance(step.get("success_candidates"), list):
                step["success_candidates"] = [
                    int(value) - 1 if int(value) > deleted else int(value)
                    for value in step["success_candidates"]
                    if int(value) != deleted
                ]
            for field in ("success_candidate_source", "candidate_original_on_fail"):
                value = int(automation.get(field) or 0)
                if value == deleted:
                    automation[field] = 0
                elif value > deleted:
                    automation[field] = value - 1
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
        mapping = {
                old_index: old_index - 1 if old_index > deleted else old_index
                for old_index in range(1, old_count + 1)
                if old_index != deleted
            }
        for source, step in enumerate(steps, start=1):
            candidates = step.get("success_candidates") or []
            if isinstance(candidates, list) and candidates:
                configure_success_candidates(steps, source, [int(value) for value in candidates])
        self._remap_graph_routes(mapping)
        self._remap_graph_collapsed(mapping)

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
            if isinstance(step.get("success_candidates"), list):
                step["success_candidates"] = [mapping.get(int(value), int(value)) for value in step["success_candidates"]]
            automation = step.get("_automation") if isinstance(step.get("_automation"), dict) else {}
            for field in ("success_candidate_source", "candidate_original_on_fail"):
                value = int(automation.get(field) or 0)
                if value in mapping:
                    automation[field] = mapping[value]
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
        self._remap_graph_collapsed(
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

    def _persist(self, message: str = "변경 사항을 저장했습니다.") -> None:
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
        self._execution_issues()
        self._update_warning_badge()
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
        if focus is self.macro_list or (isinstance(focus, QtWidgets.QWidget) and self.macro_list.isAncestorOf(focus)):
            names = self._selected_macro_names() or ([self.current_name] if self.current_name else [])
            return self._archive_macros(names, confirm=False)
        # 1. First priority: Check if any NodeGroupItem is selected on the canvas
        scene = getattr(self.node_canvas, "scene", None)
        selected_groups = [
            item for item in (scene.selectedItems() if scene is not None else [])
            if hasattr(item, "node_indexes") or hasattr(item, "comment_id")
        ]
        if selected_groups:
            for grp in selected_groups:
                self.node_canvas.remove_comment_box(grp)
            self.status.emit(f"노드 그룹 {len(selected_groups)}개가 삭제되었습니다.")
            return {"kind": "node_group_delete"}

        indexes = self.node_canvas.selected_indexes()
        if not indexes:
            if isinstance(focus, QtWidgets.QWidget) and (focus is self.node_canvas or self.node_canvas.isAncestorOf(focus)):
                return None
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
        self._update_warning_badge()

        settings = QtCore.QSettings("MacroRelay", "Studio")
        skip_warning_popup = settings.value("skip_execution_warning", True, type=bool)

        if warnings and not skip_warning_popup:
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("실행 전 주의 사항")
            dialog.setMinimumWidth(460)
            d_layout = QtWidgets.QVBoxLayout(dialog)
            d_layout.setSpacing(12)
            d_layout.addWidget(QtWidgets.QLabel(
                "<b>실행은 가능하지만 확인이 필요한 항목입니다:</b><br><br>"
                + "<br>".join(f"• {w}" for w in warnings)
                + "<br><br>계속 실행할까요?"
            ))
            chk_dont_show = QtWidgets.QCheckBox("앞으로 다시 묻지 않고 바로 실행 (툴바 느낌표 아이콘으로 확인)")
            d_layout.addWidget(chk_dont_show)
            btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Yes | QtWidgets.QDialogButtonBox.No)
            btn_box.accepted.connect(dialog.accept)
            btn_box.rejected.connect(dialog.reject)
            d_layout.addWidget(btn_box)
            if not dialog.exec():
                self.status.emit("사용자가 실행 전 주의 사항을 확인하고 실행을 취소했습니다.")
                return
            if chk_dont_show.isChecked():
                settings.setValue("skip_execution_warning", True)

        if warnings:
            self.status.emit(f"⚠️ 주의: {warnings[0]} · '{self.current_name}' 실행을 시작했습니다.")
        else:
            self.status.emit(f"'{self.current_name}' 실행을 요청했습니다. 로그 버튼에서 진행 상태를 확인할 수 있습니다.")
        self.run_macro.emit(self.current_name)

    def _update_warning_badge(self) -> None:
        if not hasattr(self, "warning_badge"):
            return
        warnings = list(getattr(self, "_last_execution_warnings", []))
        if warnings:
            self.warning_badge.setText(f"⚠️ 주의 ({len(warnings)})")
            tooltip_lines = [
                "<b>⚠️ 실행 전 주의 사항</b>",
                "실행은 가능하지만 확인이 권장되는 항목이 있습니다:<br>",
            ]
            for w in warnings[:5]:
                tooltip_lines.append(f"• {w}")
            if len(warnings) > 5:
                tooltip_lines.append(f"<i>... 외 {len(warnings) - 5}건</i>")
            tooltip_lines.append("<br>💡 <b>클릭</b>: 주의 사항 세부 내용 확인 및 팝업 설정")
            self.warning_badge.setToolTip("<br>".join(tooltip_lines))
            self.warning_badge.show()
        else:
            self.warning_badge.hide()

    def _show_warning_dialog(self) -> None:
        warnings = list(getattr(self, "_last_execution_warnings", []))
        if not warnings:
            QtWidgets.QMessageBox.information(self, "실행 전 주의 사항", "현재 매크로에 발견된 주의 사항이 없습니다.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("⚠️ 실행 전 주의 사항")
        dialog.setMinimumWidth(480)
        dialog.setStyleSheet("""
            QDialog { background: #161B22; color: #F0F6FC; }
            QLabel { color: #F0F6FC; }
            QTextEdit { background: #0D1117; color: #E6EDF3; border: 1px solid #30363D; border-radius: 6px; padding: 8px; }
            QCheckBox { color: #8B949E; }
            QCheckBox:hover { color: #F0F6FC; }
        """)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setSpacing(12)

        lbl_info = QtWidgets.QLabel("<b>실행은 가능하지만 확인이 필요한 항목입니다:</b>")
        layout.addWidget(lbl_info)

        text_edit = QtWidgets.QTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setHtml("<br>".join(f"• {w}" for w in warnings))
        text_edit.setMaximumHeight(160)
        layout.addWidget(text_edit)

        settings = QtCore.QSettings("MacroRelay", "Studio")
        chk_skip = QtWidgets.QCheckBox("앞으로 실행 시 경고 팝업 띄우지 않기 (툴바 느낌표 아이콘으로만 확인)", dialog)
        chk_skip.setChecked(settings.value("skip_execution_warning", True, type=bool))
        layout.addWidget(chk_skip)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        btn_box.accepted.connect(dialog.accept)
        layout.addWidget(btn_box)

        if dialog.exec():
            settings.setValue("skip_execution_warning", chk_skip.isChecked())

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

    def _open_in_player(self) -> None:
        if self.current_name:
            self._save_step()
            self._persist("플레이어 실행을 위해 매크로를 저장했습니다.")
        from .player import MacroPlayerWindow
        if not hasattr(self, "_player_window") or self._player_window is None:
            self._player_window = MacroPlayerWindow(self.repository, default_macro=self.current_name)
        else:
            self._player_window.refresh_macro_list()
            if self.current_name:
                self._player_window.select_macro(self.current_name)
        self._player_window.show()
        self._player_window.move_to_cursor()
        self._player_window.raise_()
        self._player_window.activateWindow()
        self.status.emit(f"'{self.current_name}'을(를) 전용 플레이어에서 열었습니다.")

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
