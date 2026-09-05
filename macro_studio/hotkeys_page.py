from __future__ import annotations

from copy import deepcopy

from PySide6 import QtCore, QtGui, QtWidgets

from .repository import MacroRepository
from .runner import QuickSlotsRunner
from .shortcuts import STUDIO_SHORTCUT_SPECS
from .theme import COLORS
from .widgets import Card, PageHeader, danger_button, primary_button


class HotkeysPage(QtWidgets.QWidget):
    status = QtCore.Signal(str)
    run_macro = QtCore.Signal(str)
    shortcut_config_changed = QtCore.Signal(dict)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.runner = QuickSlotsRunner(repository)
        self.payload: dict = {}
        self.slots: list[dict] = []
        self.current_index = 0
        self.selected_indexes: set[int] = {0}
        self._slot_drag_active = False
        self.slot_buttons: list[QtWidgets.QPushButton] = []
        self.shortcut_edits: dict[str, QtWidgets.QKeySequenceEdit] = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(PageHeader("Quick Slots", "슬롯을 가로질러 드래그해 다중 선택 · Delete 삭제 · Ctrl+Z 복구"))
        self.tabs = QtWidgets.QTabWidget()
        quick_page = QtWidgets.QWidget()
        quick_layout = QtWidgets.QVBoxLayout(quick_page)
        quick_layout.setContentsMargins(0, 8, 0, 0)
        quick_layout.setSpacing(12)
        quick_layout.addWidget(self._build_runner_card())
        splitter = QtWidgets.QSplitter()
        splitter.setChildrenCollapsible(False)
        grid_card = Card()
        grid_layout = QtWidgets.QGridLayout(grid_card)
        grid_layout.setContentsMargins(18, 18, 18, 18)
        grid_layout.setSpacing(12)
        for index in range(15):
            button = QtWidgets.QPushButton()
            button.setCheckable(True)
            button.setMinimumSize(150, 92)
            button.setMouseTracking(True)
            button.setProperty("slot_index", index)
            button.installEventFilter(self)
            self.slot_buttons.append(button)
            grid_layout.addWidget(button, index // 5, index % 5)
        splitter.addWidget(grid_card)

        editor = Card()
        editor_layout = QtWidgets.QVBoxLayout(editor)
        editor_layout.setContentsMargins(18, 18, 18, 18)
        self.slot_title = QtWidgets.QLabel("슬롯 1")
        self.slot_title.setStyleSheet("font-size: 15pt; font-weight: 700;")
        form = QtWidgets.QFormLayout()
        self.macro_combo = QtWidgets.QComboBox()
        self.key_edit = QtWidgets.QKeySequenceEdit()
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("혼합 빠른 실행", "hybrid")
        self.mode_combo.addItem("전체 실행", "standard")
        self.mode_combo.addItem("브라우저 빠른 실행", "browser")
        self.mode_combo.addItem("이미지서치 보조", "image")
        self.mode_combo.addItem("좌표 클릭 보조", "click")
        form.addRow("매크로", self.macro_combo)
        form.addRow("단축키", self.key_edit)
        form.addRow("실행 모드", self.mode_combo)
        save = primary_button("슬롯 저장 및 적용")
        save.clicked.connect(self._save_slot)
        clear = danger_button("슬롯 비우기")
        clear.clicked.connect(self._clear_slot)
        run = QtWidgets.QPushButton("▶ 즉시 실행")
        run.clicked.connect(self._run)
        editor_layout.addWidget(self.slot_title)
        editor_layout.addLayout(form)
        editor_layout.addWidget(save)
        editor_layout.addWidget(run)
        editor_layout.addWidget(clear)
        editor_layout.addStretch(1)
        splitter.addWidget(editor)
        splitter.setSizes([950, 380])
        quick_layout.addWidget(splitter, 1)
        self.tabs.addTab(quick_page, "빠른 실행 슬롯")
        root.addWidget(self.tabs, 1)

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setInterval(2000)
        self.status_timer.timeout.connect(self._refresh_runner_status)
        self.status_timer.start()

    def _build_shortcut_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 14, 8, 8)
        notice = QtWidgets.QLabel("이전 버전의 기능별 단축키 설정을 복원했습니다. 중복 키는 저장 전에 자동으로 검사합니다.")
        notice.setObjectName("Muted")
        layout.addWidget(notice)
        card = Card()
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        for action_id, label, _default in STUDIO_SHORTCUT_SPECS:
            row = QtWidgets.QHBoxLayout()
            edit = QtWidgets.QKeySequenceEdit()
            edit.setClearButtonEnabled(True)
            clear = QtWidgets.QPushButton("지우기")
            clear.setFixedWidth(72)
            clear.clicked.connect(edit.clear)
            row.addWidget(edit, 1)
            row.addWidget(clear)
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            form.addRow(label, holder)
            self.shortcut_edits[action_id] = edit
        card_layout.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton("기본값 복원")
        reset.clicked.connect(self._reset_shortcuts)
        save = primary_button("단축키 저장 및 적용")
        save.clicked.connect(self._save_shortcuts)
        actions.addStretch(1)
        actions.addWidget(reset)
        actions.addWidget(save)
        card_layout.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_runner_card(self) -> QtWidgets.QWidget:
        card = Card()
        layout = QtWidgets.QHBoxLayout(card)
        layout.setContentsMargins(18, 13, 18, 13)
        layout.setSpacing(10)
        self.runner_status = QtWidgets.QLabel("● Runner 확인 중")
        self.runner_status.setStyleSheet("font-weight: 700;")
        self.runner_detail = QtWidgets.QLabel()
        self.runner_detail.setObjectName("Muted")
        self.startup_check = QtWidgets.QCheckBox("Windows 로그인 시 자동 시작")
        self.startup_check.toggled.connect(self._toggle_startup)
        self.start_runner_button = primary_button("Runner 시작")
        self.start_runner_button.clicked.connect(self._start_runner)
        apply_button = QtWidgets.QPushButton("변경 적용")
        apply_button.clicked.connect(self._apply_runner)
        stop = danger_button("Runner 중지")
        stop.clicked.connect(self._stop_runner)
        layout.addWidget(self.runner_status)
        layout.addWidget(self.runner_detail)
        layout.addStretch(1)
        layout.addWidget(self.startup_check)
        layout.addWidget(self.start_runner_button)
        layout.addWidget(apply_button)
        layout.addWidget(stop)
        return card

    def refresh(self) -> None:
        self.payload = self.repository.load_hotkeys()
        self.payload.setdefault(
            "runner",
            {"enabled": True, "start_with_windows": False, "emergency_hotkey": "Ctrl+Alt+Pause"},
        )
        self.slots = list(self.payload.get("slots") or [])
        while len(self.slots) < 15:
            self.slots.append({"macro": "", "hotkey": "", "mode": "hybrid"})
        self.slots = self.slots[:15]
        current_macro = self.macro_combo.currentText()
        self.macro_combo.clear()
        self.macro_combo.addItem("선택 안 함", "")
        for summary in self.repository.list_macros():
            self.macro_combo.addItem(summary.name, summary.name)
        if current_macro:
            self.macro_combo.setCurrentText(current_macro)
        self._refresh_buttons()
        self._select_slot(self.current_index)
        self.startup_check.blockSignals(True)
        self.startup_check.setChecked(bool(self.payload["runner"].get("start_with_windows", False)))
        self.startup_check.blockSignals(False)
        self._refresh_runner_status()

    def _load_shortcuts(self) -> None:
        saved = self.repository.load_hotkey_actions()
        for action_id, _label, default in STUDIO_SHORTCUT_SPECS:
            self.shortcut_edits[action_id].setKeySequence(QtGui.QKeySequence(saved.get(action_id, default)))

    def _shortcut_payload(self) -> dict[str, str]:
        return {
            action_id: self.shortcut_edits[action_id].keySequence().toString(QtGui.QKeySequence.PortableText)
            for action_id, _label, _default in STUDIO_SHORTCUT_SPECS
        }

    def _save_shortcuts(self) -> None:
        payload = self._shortcut_payload()
        seen: dict[str, str] = {}
        labels = {action_id: label for action_id, label, _default in STUDIO_SHORTCUT_SPECS}
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
        self.status.emit("Studio 기능별 단축키를 저장하고 적용했습니다.")

    def _reset_shortcuts(self) -> None:
        for action_id, _label, default in STUDIO_SHORTCUT_SPECS:
            self.shortcut_edits[action_id].setKeySequence(QtGui.QKeySequence(default))

    def _refresh_buttons(self) -> None:
        for index, button in enumerate(self.slot_buttons):
            slot = self.slots[index]
            macro = str(slot.get("macro") or "비어 있음")
            hotkey = str(slot.get("hotkey") or "-")
            button.setText(f"{index + 1}\n{macro}\n{hotkey}")
            button.setChecked(index in self.selected_indexes)

    def _select_slot(self, index: int, preserve_selection: bool = False) -> None:
        self.current_index = index
        if not preserve_selection:
            self.selected_indexes = {index}
        slot = self.slots[index] if index < len(self.slots) else {}
        self.slot_title.setText(f"슬롯 {index + 1}")
        macro = str(slot.get("macro") or "")
        combo_index = self.macro_combo.findData(macro)
        self.macro_combo.setCurrentIndex(max(combo_index, 0))
        self.key_edit.setKeySequence(QtGui.QKeySequence(str(slot.get("hotkey") or "")))
        mode_index = self.mode_combo.findData(str(slot.get("mode") or "hybrid"))
        self.mode_combo.setCurrentIndex(max(mode_index, 0))
        self._refresh_buttons()

    def eventFilter(self, watched, event) -> bool:
        if watched in self.slot_buttons:
            index = int(watched.property("slot_index"))
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                if event.modifiers() & QtCore.Qt.ControlModifier:
                    if index in self.selected_indexes and len(self.selected_indexes) > 1:
                        self.selected_indexes.remove(index)
                    else:
                        self.selected_indexes.add(index)
                else:
                    self.selected_indexes = {index}
                self._slot_drag_active = True
                self._select_slot(index, preserve_selection=True)
                event.accept()
                return True
            if event.type() == QtCore.QEvent.MouseMove and self._slot_drag_active and event.buttons() & QtCore.Qt.LeftButton:
                global_position = event.globalPosition().toPoint()
                for target_index, button in enumerate(self.slot_buttons):
                    if button.rect().contains(button.mapFromGlobal(global_position)):
                        self.selected_indexes.add(target_index)
                        self._select_slot(target_index, preserve_selection=True)
                        break
                event.accept()
                return True
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                self._slot_drag_active = False
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _save_slot(self) -> None:
        self.slots[self.current_index] = {
            "macro": self.macro_combo.currentData() or "",
            "hotkey": self.key_edit.keySequence().toString(QtGui.QKeySequence.PortableText),
            "mode": self.mode_combo.currentData() or "hybrid",
        }
        payload = self._current_payload()
        if not self._save_and_apply(payload):
            return
        self._refresh_buttons()
        self.status.emit(f"슬롯 {self.current_index + 1}을 저장하고 Runner에 적용했습니다.")

    def _clear_slot(self) -> None:
        self.slots[self.current_index] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        self._save_and_apply(self._current_payload())
        self._select_slot(self.current_index)

    def delete_selected(self) -> dict | None:
        indexes = sorted(index for index in self.selected_indexes if 0 <= index < len(self.slots))
        if not indexes and 0 <= self.current_index < len(self.slots):
            indexes = [self.current_index]
        if not indexes:
            return None
        previous = [
            {"index": index, "slot": deepcopy(self.slots[index])}
            for index in indexes
            if self.slots[index].get("macro") or self.slots[index].get("hotkey")
        ]
        if not previous:
            self.status.emit("선택한 Quick Slot들이 이미 비어 있습니다.")
            return None
        for record in previous:
            self.slots[int(record["index"])] = {"macro": "", "hotkey": "", "mode": "hybrid"}
        if not self._save_and_apply(self._current_payload()):
            for record in previous:
                self.slots[int(record["index"])] = deepcopy(record["slot"])
            self._select_slot(self.current_index, preserve_selection=True)
            return None
        self._select_slot(self.current_index, preserve_selection=True)
        self.status.emit(f"Quick Slot {len(previous)}개를 비웠습니다.")
        return {"kind": "quick_slots", "slots": previous}

    def restore_deleted_slot(self, index: int, slot: dict) -> bool:
        if not 0 <= index < len(self.slots):
            return False
        current = deepcopy(self.slots[index])
        self.slots[index] = deepcopy(slot)
        if not self._save_and_apply(self._current_payload()):
            self.slots[index] = current
            return False
        self._select_slot(index)
        return True

    def restore_deleted_slots(self, records: list[dict]) -> bool:
        valid = [record for record in records if 0 <= int(record.get("index", -1)) < len(self.slots)]
        if not valid:
            return False
        current = {int(record["index"]): deepcopy(self.slots[int(record["index"])]) for record in valid}
        for record in valid:
            self.slots[int(record["index"])] = deepcopy(record.get("slot") or {})
        if not self._save_and_apply(self._current_payload()):
            for index, slot in current.items():
                self.slots[index] = slot
            return False
        self.selected_indexes = {int(record["index"]) for record in valid}
        self._select_slot(int(valid[-1]["index"]), preserve_selection=True)
        return True

    def _run(self) -> None:
        macro = str(self.slots[self.current_index].get("macro") or "")
        if macro:
            self.run_macro.emit(macro)
        else:
            self.status.emit("이 슬롯에 등록된 매크로가 없습니다.")

    def _current_payload(self) -> dict:
        runner_settings = dict(self.payload.get("runner") or {})
        runner_settings.setdefault("enabled", True)
        runner_settings.setdefault("start_with_windows", self.startup_check.isChecked())
        runner_settings.setdefault("emergency_hotkey", "Ctrl+Alt+Pause")
        return {"rows": 3, "cols": 5, "slots": self.slots, "runner": runner_settings}

    def _save_and_apply(self, payload: dict) -> bool:
        errors = self.runner.validate(payload)
        if errors:
            QtWidgets.QMessageBox.warning(self, "Quick Slots 확인", "\n".join(errors))
            return False
        try:
            self.repository.save_hotkeys(payload)
            self.payload = payload
            if bool(payload["runner"].get("enabled", True)):
                self.runner.restart(payload)
            else:
                self.runner.build(payload)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Runner 적용 실패", str(exc))
            return False
        self._refresh_runner_status()
        return True

    def _apply_runner(self) -> None:
        if self._save_and_apply(self._current_payload()):
            self.status.emit("Quick Slots 변경 내용을 Runner에 적용했습니다.")

    def _start_runner(self) -> None:
        payload = self._current_payload()
        payload["runner"]["enabled"] = True
        if self._save_and_apply(payload):
            self.status.emit("MacroRelay Runner를 시작했습니다.")

    def _stop_runner(self) -> None:
        payload = self._current_payload()
        payload["runner"]["enabled"] = False
        self.repository.save_hotkeys(payload)
        self.payload = payload
        self.runner.stop()
        self._refresh_runner_status()
        self.status.emit("MacroRelay Runner를 중지했습니다.")

    def _toggle_startup(self, enabled: bool) -> None:
        if not self.payload:
            return
        payload = self._current_payload()
        payload["runner"]["start_with_windows"] = enabled
        try:
            self.repository.save_hotkeys(payload)
            self.payload = payload
            self.runner.set_startup(enabled)
        except Exception as exc:
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(not enabled)
            self.startup_check.blockSignals(False)
            QtWidgets.QMessageBox.warning(self, "자동 시작 설정 실패", str(exc))
            return
        self._refresh_runner_status()
        self.status.emit("자동 시작을 켰습니다." if enabled else "자동 시작을 껐습니다.")

    def _refresh_runner_status(self) -> None:
        runner_status = self.runner.status(self.payload or None)
        if runner_status.running:
            self.runner_status.setText("● Runner 실행 중")
            self.runner_status.setStyleSheet(f"font-weight: 700; color: {COLORS['success']};")
            self.start_runner_button.setText("Runner 재시작")
            pid_text = f"PID {runner_status.pid}"
        else:
            self.runner_status.setText("● Runner 중지됨")
            self.runner_status.setStyleSheet(f"font-weight: 700; color: {COLORS['muted']};")
            self.start_runner_button.setText("Runner 시작")
            pid_text = "백그라운드 미실행"
        startup = "자동 시작 켜짐" if runner_status.startup_enabled else "자동 시작 꺼짐"
        self.runner_detail.setText(f"{runner_status.active_slots}개 활성 · {pid_text} · {startup} · 긴급 중지 Ctrl+Alt+Pause")
