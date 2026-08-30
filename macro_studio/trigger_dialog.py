from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6 import QtCore, QtWidgets

from .widgets import WheelSafeSpinBox, primary_button, danger_button


class EventTriggerDialog(QtWidgets.QDialog):
    TYPES = (
        ("직접 실행", "manual"), ("프로그램 시작", "process_start"), ("프로그램 종료", "process_stop"),
        ("창 나타남", "window_appears"), ("이미지 나타남", "image_appears"),
        ("OCR 숫자 조건", "ocr_threshold"), ("지정 시간·요일", "schedule"),
    )

    def __init__(self, repository, triggers: list[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.triggers = deepcopy(triggers)
        self.setWindowTitle("이벤트 기반 자동 실행")
        self.resize(880, 620)
        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel("조건이 처음 성립하는 순간 매크로를 자동 실행합니다. 모바일·웹훅 명령은 원격 연결 설정을 그대로 사용합니다.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["사용", "종류", "대상/시간", "확인 간격"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        for column in (0, 1, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._load_selected)
        root.addWidget(self.table, 1)
        form = QtWidgets.QFormLayout()
        self.type_combo = QtWidgets.QComboBox()
        for label, value in self.TYPES:
            self.type_combo.addItem(label, value)
        self.target_edit = QtWidgets.QLineEdit()
        self.target_edit.setPlaceholderText("프로세스.exe / 창 제목 / 이미지 자산 이름")
        self.time_edit = QtWidgets.QTimeEdit(QtCore.QTime.currentTime())
        self.time_edit.setDisplayFormat("HH:mm")
        self.days_edit = QtWidgets.QLineEdit("0,1,2,3,4,5,6")
        self.days_edit.setPlaceholderText("월=0 … 일=6, 쉼표로 구분")
        self.region_edit = QtWidgets.QLineEdit("0,0,0,0")
        self.region_edit.setPlaceholderText("왼쪽,위,오른쪽,아래 · 0이면 전체 화면")
        condition_row = QtWidgets.QWidget()
        condition_layout = QtWidgets.QHBoxLayout(condition_row)
        condition_layout.setContentsMargins(0, 0, 0, 0)
        self.operator_combo = QtWidgets.QComboBox()
        self.operator_combo.addItems([">=", "<=", ">", "<", "==", "!="])
        self.value_edit = QtWidgets.QDoubleSpinBox()
        self.value_edit.setRange(-999999999, 999999999)
        condition_layout.addWidget(self.operator_combo)
        condition_layout.addWidget(self.value_edit, 1)
        self.interval_spin = WheelSafeSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setSuffix(" 초")
        self.interval_spin.setValue(1)
        self.enabled_check = QtWidgets.QCheckBox("사용")
        self.enabled_check.setChecked(True)
        form.addRow("트리거 종류", self.type_combo)
        form.addRow("대상", self.target_edit)
        form.addRow("시간", self.time_edit)
        form.addRow("요일", self.days_edit)
        form.addRow("화면 범위", self.region_edit)
        form.addRow("OCR 숫자 조건", condition_row)
        form.addRow("확인 간격", self.interval_spin)
        form.addRow("상태", self.enabled_check)
        root.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        add = primary_button("＋ 새 조건 추가")
        update = QtWidgets.QPushButton("선택 조건 적용")
        remove = danger_button("선택 삭제")
        add.clicked.connect(self._add)
        update.clicked.connect(self._update)
        remove.clicked.connect(self._remove)
        row.addWidget(add); row.addWidget(update); row.addWidget(remove); row.addStretch(1)
        root.addLayout(row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _payload(self) -> dict[str, Any]:
        kind = str(self.type_combo.currentData() or "process_start")
        target = self.target_edit.text().strip()
        payload: dict[str, Any] = {"type": kind, "enabled": self.enabled_check.isChecked(), "interval": self.interval_spin.value()}
        if kind == "manual":
            pass
        elif kind.startswith("process_"):
            payload["process"] = target
        elif kind == "window_appears":
            payload["title"] = target
        elif kind == "image_appears":
            payload.update({"asset": target, "threshold": 0.86})
        elif kind == "ocr_threshold":
            payload.update({"operator": self.operator_combo.currentText(), "value": self.value_edit.value()})
        elif kind == "schedule":
            payload["time"] = self.time_edit.time().toString("HH:mm")
            payload["days"] = [int(value) for value in self.days_edit.text().split(",") if value.strip().isdigit() and 0 <= int(value) <= 6]
        if kind in {"image_appears", "ocr_threshold"}:
            try:
                payload["region"] = [int(value.strip()) for value in self.region_edit.text().split(",")[:4]]
            except ValueError:
                payload["region"] = [0, 0, 0, 0]
        return payload

    def _add(self) -> None:
        self.triggers.append(self._payload()); self._refresh(len(self.triggers) - 1)

    def _update(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.triggers):
            self.triggers[row] = self._payload(); self._refresh(row)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.triggers):
            self.triggers.pop(row); self._refresh(min(row, len(self.triggers) - 1))

    def _refresh(self, selected: int = 0) -> None:
        labels = dict((value, label) for label, value in self.TYPES)
        self.table.setRowCount(len(self.triggers))
        for row, item in enumerate(self.triggers):
            kind = str(item.get("type") or "")
            target = "실행 버튼" if kind == "manual" else item.get("process") or item.get("title") or item.get("asset") or item.get("time") or f"{item.get('operator', '>=')} {item.get('value', 0)}"
            values = ["켜짐" if item.get("enabled", True) else "꺼짐", labels.get(kind, kind), str(target), f"{item.get('interval', 1)}초"]
            for column, value in enumerate(values): self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        if self.triggers: self.table.selectRow(max(0, min(selected, len(self.triggers) - 1)))

    def _load_selected(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.triggers): return
        item = self.triggers[row]; kind = str(item.get("type") or "process_start")
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(kind)))
        self.target_edit.setText(str(item.get("process") or item.get("title") or item.get("asset") or ""))
        self.time_edit.setTime(QtCore.QTime.fromString(str(item.get("time") or "00:00"), "HH:mm"))
        self.days_edit.setText(",".join(str(value) for value in item.get("days", range(7))))
        self.region_edit.setText(",".join(str(value) for value in item.get("region", [0, 0, 0, 0])))
        self.operator_combo.setCurrentText(str(item.get("operator") or ">="))
        self.value_edit.setValue(float(item.get("value") or 0)); self.interval_spin.setValue(max(1, int(item.get("interval") or 1)))
        self.enabled_check.setChecked(bool(item.get("enabled", True)))
