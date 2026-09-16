from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .repository import MacroRepository


class MacroBundleDialog(QtWidgets.QDialog):
    """Create or edit a macro that runs independent macros in order."""

    def __init__(self, repository: MacroRepository, bundle_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.bundle_name = bundle_name
        self.setWindowTitle("실행 묶음 만들기")
        self.resize(700, 520)

        current_items: list[str] = []
        if bundle_name:
            try:
                payload = repository.load_macro(bundle_name)
            except (OSError, ValueError):
                payload = {}
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            current_items = [str(value) for value in meta.get("bundle_items", []) if str(value).strip()]

        root = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "던전·성장·상점·이벤트처럼 따로 만든 매크로를 실행할 순서대로 묶습니다. "
            "각 매크로가 끝나면 다음 항목이 자동으로 시작됩니다."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(bundle_name or "일일 자동화 묶음")
        self.name_edit.setReadOnly(bool(bundle_name))
        form.addRow("묶음 이름", self.name_edit)
        root.addLayout(form)

        columns = QtWidgets.QHBoxLayout()
        available_box = QtWidgets.QVBoxLayout()
        available_box.addWidget(QtWidgets.QLabel("사용 가능한 개별 매크로"))
        self.available_list = QtWidgets.QListWidget()
        self.available_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        available_box.addWidget(self.available_list, 1)

        controls = QtWidgets.QVBoxLayout()
        controls.addStretch(1)
        self.add_button = QtWidgets.QPushButton("추가  →")
        self.remove_button = QtWidgets.QPushButton("←  제거")
        controls.addWidget(self.add_button)
        controls.addWidget(self.remove_button)
        controls.addStretch(1)

        sequence_box = QtWidgets.QVBoxLayout()
        sequence_box.addWidget(QtWidgets.QLabel("실행 순서"))
        self.sequence_list = QtWidgets.QListWidget()
        self.sequence_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.sequence_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        sequence_box.addWidget(self.sequence_list, 1)
        move_row = QtWidgets.QHBoxLayout()
        self.up_button = QtWidgets.QPushButton("위로")
        self.down_button = QtWidgets.QPushButton("아래로")
        move_row.addWidget(self.up_button)
        move_row.addWidget(self.down_button)
        sequence_box.addLayout(move_row)

        columns.addLayout(available_box, 1)
        columns.addLayout(controls)
        columns.addLayout(sequence_box, 1)
        root.addLayout(columns, 1)

        note = QtWidgets.QLabel(
            "입장권 없음처럼 개별 매크로가 스스로 종료한 경우에도 다음 항목을 계속 실행합니다. "
            "실행 중에는 Studio의 정지 버튼으로 묶음 전체를 멈출 수 있습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #93A4BC;")
        root.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("묶음 저장")
        buttons.accepted.connect(self._accept_if_ready)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for summary in repository.list_macros():
            if summary.name == bundle_name or summary.name in current_items:
                continue
            try:
                payload = repository.load_macro(summary.name)
            except (OSError, ValueError):
                continue
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            if meta.get("macro_bundle"):
                continue
            self.available_list.addItem(summary.name)
        for name in current_items:
            if repository.macro_path(name).is_file():
                self.sequence_list.addItem(name)

        self.add_button.clicked.connect(self._add_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.up_button.clicked.connect(lambda: self._move_current(-1))
        self.down_button.clicked.connect(lambda: self._move_current(1))
        self.available_list.itemDoubleClicked.connect(lambda _item: self._add_selected())
        self.sequence_list.itemDoubleClicked.connect(lambda _item: self._remove_selected())

    def selected_macros(self) -> list[str]:
        return [self.sequence_list.item(index).text() for index in range(self.sequence_list.count())]

    def _add_selected(self) -> None:
        rows = sorted((self.available_list.row(item) for item in self.available_list.selectedItems()), reverse=True)
        moved_items = []
        for row in rows:
            item = self.available_list.takeItem(row)
            if item is not None:
                moved_items.append(item)
        for item in reversed(moved_items):
            self.sequence_list.addItem(item)

    def _remove_selected(self) -> None:
        rows = sorted((self.sequence_list.row(item) for item in self.sequence_list.selectedItems()), reverse=True)
        for row in rows:
            item = self.sequence_list.takeItem(row)
            if item is not None:
                self.available_list.addItem(item)
        self.available_list.sortItems()

    def _move_current(self, delta: int) -> None:
        row = self.sequence_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.sequence_list.count():
            return
        item = self.sequence_list.takeItem(row)
        self.sequence_list.insertItem(target, item)
        self.sequence_list.setCurrentRow(target)

    def _accept_if_ready(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "실행 묶음", "묶음 이름을 입력하세요.")
            return
        if not self.selected_macros():
            QtWidgets.QMessageBox.warning(self, "실행 묶음", "실행할 매크로를 하나 이상 추가하세요.")
            return
        target = self.repository.macro_path(name)
        if target.exists() and name != self.bundle_name:
            QtWidgets.QMessageBox.warning(self, "실행 묶음", "같은 이름의 매크로가 이미 있습니다.")
            return
        self.accept()
