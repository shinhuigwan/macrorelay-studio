from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .repository import MacroRepository
from .widgets import Card, PageHeader, danger_button, primary_button


class DataPage(QtWidgets.QWidget):
    data_changed = QtCore.Signal()
    status = QtCore.Signal(str)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.tables: dict[str, list[list[str]]] = {}
        self._loading = False
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(PageHeader("데이터 테이블", "매크로 입력값을 표로 관리합니다. 저장할 때 자동 백업이 생성됩니다."))

        toolbar = QtWidgets.QHBoxLayout()
        self.table_combo = QtWidgets.QComboBox()
        self.table_combo.currentTextChanged.connect(self._load_selected)
        add_table = primary_button("＋ 새 테이블")
        add_table.clicked.connect(self._add_table)
        delete_table = danger_button("테이블 삭제")
        delete_table.clicked.connect(self._delete_table)
        add_row = QtWidgets.QPushButton("＋ 행")
        add_row.clicked.connect(self._add_row)
        add_col = QtWidgets.QPushButton("＋ 열")
        add_col.clicked.connect(self._add_column)
        save = primary_button("저장")
        save.clicked.connect(self._save)
        toolbar.addWidget(self.table_combo, 1)
        toolbar.addWidget(add_table)
        toolbar.addWidget(delete_table)
        toolbar.addSpacing(12)
        toolbar.addWidget(add_row)
        toolbar.addWidget(add_col)
        toolbar.addWidget(save)
        root.addLayout(toolbar)

        card = Card()
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        self.table = QtWidgets.QTableWidget()
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.table)
        root.addWidget(card, 1)

    def refresh(self) -> None:
        previous = self.table_combo.currentText()
        self.tables = self.repository.load_tables()
        self.table_combo.blockSignals(True)
        self.table_combo.clear()
        self.table_combo.addItems(sorted(self.tables, key=str.casefold))
        if previous in self.tables:
            self.table_combo.setCurrentText(previous)
        self.table_combo.blockSignals(False)
        self._load_selected(self.table_combo.currentText())

    def _load_selected(self, name: str) -> None:
        self._loading = True
        rows = self.tables.get(name, [])
        columns = max((len(row) for row in rows), default=1)
        self.table.setRowCount(max(len(rows), 1))
        self.table.setColumnCount(max(columns, 1))
        self.table.setHorizontalHeaderLabels([self._column_name(index) for index in range(self.table.columnCount())])
        for row_index in range(self.table.rowCount()):
            for column_index in range(self.table.columnCount()):
                value = rows[row_index][column_index] if row_index < len(rows) and column_index < len(rows[row_index]) else ""
                self.table.setItem(row_index, column_index, QtWidgets.QTableWidgetItem(str(value)))
        self._loading = False

    @staticmethod
    def _column_name(index: int) -> str:
        result = ""
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _collect_current(self) -> None:
        name = self.table_combo.currentText()
        if not name:
            return
        rows: list[list[str]] = []
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, col).text() if self.table.item(row, col) else "" for col in range(self.table.columnCount())]
            while values and not values[-1]:
                values.pop()
            rows.append(values)
        while rows and not rows[-1]:
            rows.pop()
        self.tables[name] = rows

    def _save(self) -> None:
        self._collect_current()
        self.repository.save_tables(self.tables)
        self.data_changed.emit()
        self.status.emit("데이터 테이블을 저장했습니다.")

    def _add_table(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "새 테이블", "테이블 이름")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.tables:
            QtWidgets.QMessageBox.warning(self, "이름 중복", "같은 이름의 테이블이 있습니다.")
            return
        self._collect_current()
        self.tables[name] = [[""]]
        self.table_combo.addItem(name)
        self.table_combo.setCurrentText(name)

    def _delete_table(self) -> None:
        name = self.table_combo.currentText()
        if not name:
            return
        answer = QtWidgets.QMessageBox.question(self, "테이블 삭제", f"'{name}' 테이블을 삭제할까요?\n저장 시 백업이 생성됩니다.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.tables.pop(name, None)
        self.repository.save_tables(self.tables)
        self.refresh()
        self.data_changed.emit()

    def _add_row(self) -> None:
        self.table.insertRow(self.table.rowCount())

    def _add_column(self) -> None:
        column = self.table.columnCount()
        self.table.insertColumn(column)
        self.table.setHorizontalHeaderItem(column, QtWidgets.QTableWidgetItem(self._column_name(column)))

