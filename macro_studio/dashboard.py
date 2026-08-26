from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .repository import MacroRepository
from .widgets import Card, PageHeader, primary_button


MODE_LABELS = {
    "hybrid": "혼합 실행",
    "standard": "전체 실행",
    "browser": "브라우저",
    "image": "이미지 서치",
    "click": "좌표 클릭",
}


class DashboardPage(QtWidgets.QWidget):
    open_page = QtCore.Signal(str)
    open_macro = QtCore.Signal(str)
    run_macro = QtCore.Signal(str)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.latest_name = ""
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        root.addWidget(PageHeader("홈", "이어서 편집하고, 자주 쓰는 매크로를 바로 실행하세요."))

        continue_card = Card()
        continue_layout = QtWidgets.QHBoxLayout(continue_card)
        continue_layout.setContentsMargins(22, 18, 22, 18)
        text = QtWidgets.QVBoxLayout()
        eyebrow = QtWidgets.QLabel("계속 작업")
        eyebrow.setObjectName("Muted")
        self.continue_title = QtWidgets.QLabel("최근 매크로가 없습니다")
        self.continue_title.setStyleSheet("font-size: 19pt; font-weight: 800;")
        self.continue_detail = QtWidgets.QLabel("새 매크로를 만들어 자동화를 시작하세요.")
        self.continue_detail.setObjectName("Muted")
        text.addWidget(eyebrow)
        text.addWidget(self.continue_title)
        text.addWidget(self.continue_detail)
        continue_layout.addLayout(text, 1)
        continue_edit = primary_button("빌더에서 이어하기")
        continue_run = QtWidgets.QPushButton("▶ 실행")
        continue_edit.clicked.connect(self._open_latest)
        continue_run.clicked.connect(self._run_latest)
        continue_layout.addWidget(continue_edit)
        continue_layout.addWidget(continue_run)
        root.addWidget(continue_card)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(14)
        recent_card = Card()
        recent_layout = QtWidgets.QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(18, 16, 18, 16)
        recent_header = QtWidgets.QHBoxLayout()
        recent_title = QtWidgets.QLabel("최근 매크로")
        recent_title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.project_summary = QtWidgets.QLabel()
        self.project_summary.setObjectName("Muted")
        recent_header.addWidget(recent_title)
        recent_header.addStretch(1)
        recent_header.addWidget(self.project_summary)
        self.recent_list = QtWidgets.QListWidget()
        self.recent_list.itemDoubleClicked.connect(self._open_selected)
        self.recent_list.currentItemChanged.connect(self._sync_selection)
        recent_layout.addLayout(recent_header)
        recent_layout.addWidget(self.recent_list, 1)
        recent_actions = QtWidgets.QHBoxLayout()
        edit_btn = primary_button("선택 매크로 편집")
        edit_btn.clicked.connect(self._open_selected)
        run_btn = QtWidgets.QPushButton("선택 실행")
        run_btn.clicked.connect(self._run_selected)
        new_btn = QtWidgets.QPushButton("＋ 새 매크로")
        new_btn.clicked.connect(lambda: self.open_page.emit("builder"))
        recent_actions.addWidget(edit_btn)
        recent_actions.addWidget(run_btn)
        recent_actions.addStretch(1)
        recent_actions.addWidget(new_btn)
        recent_layout.addLayout(recent_actions)
        content.addWidget(recent_card, 3)

        slots_card = Card()
        slots_layout = QtWidgets.QVBoxLayout(slots_card)
        slots_layout.setContentsMargins(18, 16, 18, 16)
        slots_title = QtWidgets.QLabel("백그라운드 Quick Slots")
        slots_title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        slots_hint = QtWidgets.QLabel("활성 슬롯만 보여줍니다. 설정은 Studio에서만 변경됩니다.")
        slots_hint.setObjectName("Muted")
        slots_hint.setWordWrap(True)
        self.slots_list = QtWidgets.QListWidget()
        self.slots_list.itemDoubleClicked.connect(self._run_slot)
        manage_slots = QtWidgets.QPushButton("슬롯과 단축키 관리")
        manage_slots.clicked.connect(lambda: self.open_page.emit("hotkeys"))
        slots_layout.addWidget(slots_title)
        slots_layout.addWidget(slots_hint)
        slots_layout.addWidget(self.slots_list, 1)
        slots_layout.addWidget(manage_slots)
        content.addWidget(slots_card, 2)
        root.addLayout(content, 1)

        quick_card = Card()
        quick_layout = QtWidgets.QHBoxLayout(quick_card)
        quick_layout.setContentsMargins(18, 13, 18, 13)
        quick_title = QtWidgets.QLabel("빠른 작업")
        quick_title.setStyleSheet("font-weight: 700;")
        quick_layout.addWidget(quick_title)
        quick_layout.addStretch(1)
        for label, page in (("이미지 편집", "assets"), ("데이터 테이블", "data"), ("내보내기", "export"), ("프로젝트 진단", "settings")):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _checked=False, target=page: self.open_page.emit(target))
            quick_layout.addWidget(button)
        root.addWidget(quick_card)

    def _selected_name(self) -> str:
        item = self.recent_list.currentItem()
        return str(item.data(QtCore.Qt.UserRole) or "") if item else ""

    def _open_latest(self) -> None:
        if self.latest_name:
            self.open_macro.emit(self.latest_name)
        else:
            self.open_page.emit("builder")

    def _run_latest(self) -> None:
        if self.latest_name:
            self.run_macro.emit(self.latest_name)

    def _open_selected(self, *_args) -> None:
        name = self._selected_name()
        if name:
            self.open_macro.emit(name)

    def _run_selected(self, *_args) -> None:
        name = self._selected_name()
        if name:
            self.run_macro.emit(name)

    def _run_slot(self, item) -> None:
        name = str(item.data(QtCore.Qt.UserRole) or "")
        if name:
            self.run_macro.emit(name)

    def _sync_selection(self, item, _previous) -> None:
        if item:
            self.continue_title.setText(str(item.data(QtCore.Qt.UserRole) or item.text()))
            self.continue_detail.setText(str(item.data(QtCore.Qt.UserRole + 1) or ""))

    def refresh(self) -> None:
        summaries = sorted(self.repository.list_macros(), key=lambda item: item.modified, reverse=True)
        self.recent_list.clear()
        for summary in summaries[:12]:
            item = QtWidgets.QListWidgetItem(f"{summary.name}\n{summary.steps}단계  ·  {summary.modified:%Y.%m.%d %H:%M}")
            item.setData(QtCore.Qt.UserRole, summary.name)
            item.setData(QtCore.Qt.UserRole + 1, summary.description or "설명 없음")
            item.setToolTip(summary.description)
            self.recent_list.addItem(item)
        step_count = sum(item.steps for item in summaries)
        self.project_summary.setText(f"전체 {len(summaries)}개 · {step_count}단계")
        self.latest_name = summaries[0].name if summaries else ""
        if self.recent_list.count():
            self.recent_list.setCurrentRow(0)
        else:
            self.continue_title.setText("최근 매크로가 없습니다")
            self.continue_detail.setText("새 매크로를 만들어 자동화를 시작하세요.")

        self.slots_list.clear()
        slots = list(self.repository.load_hotkeys().get("slots") or [])
        for index, slot in enumerate(slots, start=1):
            macro = str(slot.get("macro") or "").strip()
            hotkey = str(slot.get("hotkey") or "").strip()
            if not macro or not hotkey:
                continue
            mode = MODE_LABELS.get(str(slot.get("mode") or "hybrid"), "빠른 실행")
            item = QtWidgets.QListWidgetItem(f"{index:02d}  {macro}\n{hotkey}  ·  {mode}")
            item.setData(QtCore.Qt.UserRole, macro)
            self.slots_list.addItem(item)
        if not self.slots_list.count():
            placeholder = QtWidgets.QListWidgetItem("아직 활성 Quick Slot이 없습니다.")
            placeholder.setFlags(QtCore.Qt.NoItemFlags)
            self.slots_list.addItem(placeholder)
