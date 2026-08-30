from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .ai_automation import AIImportIssue, ai_draft_readiness, validate_ai_document
from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop
from .image_search_test import ImageSearchTestDialog


def load_ai_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("AI JSON 최상위 값은 객체여야 합니다.")
    return payload


def package_stage_for(root: Path, payload: dict[str, Any]) -> Path | None:
    package_id = str(payload.get("source_package_id") or "").strip()
    if package_id:
        stage = (root / ".automation" / "ai-packages" / package_id).resolve()
        if stage.is_dir():
            return stage
    try:
        recent = json.loads((root / ".automation" / "last-ai-package.json").read_text(encoding="utf-8-sig"))
        stage = Path(str(recent.get("stage") or "")).resolve()
        if stage.is_dir() and (not package_id or str(recent.get("package_id") or "") == package_id):
            return stage
    except (OSError, TypeError, ValueError):
        pass
    return None


class AIImportPreviewDialog(QtWidgets.QDialog):
    def __init__(self, payload: dict[str, Any], repository, package_stage: Path | None, parent=None) -> None:
        super().__init__(parent)
        self.payload = deepcopy(payload)
        self.repository = repository
        self.package_stage = package_stage
        self.issues = validate_ai_document(payload)
        self.setWindowTitle("AI JSON 가져오기 검토")
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("AI 매크로 JSON 가져오기")
        title.setStyleSheet("font-size:18pt; font-weight:800;")
        summary = QtWidgets.QLabel(
            f"{str(payload.get('name') or '이름 없음')} · 노드 {len(payload.get('steps') or [])}개 · "
            f"대상 {len(payload.get('targets') or [])}개 · 이미지 {len(payload.get('assets') or [])}개"
        )
        summary.setObjectName("Muted")
        root.addWidget(title)
        root.addWidget(summary)
        package_label = QtWidgets.QLabel(
            f"녹화 패키지: {package_stage}" if package_stage else "녹화 패키지를 찾지 못했습니다. 이미지 자산은 설정 필요 상태로 가져옵니다."
        )
        package_label.setWordWrap(True)
        package_label.setStyleSheet(
            "color:#79E2C1;" if package_stage else "color:#FFB35C;"
        )
        root.addWidget(package_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        issue_box = QtWidgets.QWidget()
        issue_layout = QtWidgets.QVBoxLayout(issue_box)
        issue_layout.setContentsMargins(0, 0, 8, 0)
        issue_layout.addWidget(QtWidgets.QLabel("형식·안전 검사"))
        self.issue_table = QtWidgets.QTableWidget(len(self.issues), 3)
        self.issue_table.setHorizontalHeaderLabels(["수준", "위치", "내용"])
        self.issue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        for row, issue in enumerate(self.issues):
            self.issue_table.setItem(row, 0, QtWidgets.QTableWidgetItem("오류" if issue.severity == "error" else "확인"))
            self.issue_table.setItem(row, 1, QtWidgets.QTableWidgetItem(issue.step_id or "전체"))
            item = QtWidgets.QTableWidgetItem(issue.detail)
            item.setForeground(QtGui.QColor("#FF7185" if issue.severity == "error" else "#FFB35C"))
            self.issue_table.setItem(row, 2, item)
        if not self.issues:
            self.issue_table.setRowCount(1)
            self.issue_table.setItem(0, 0, QtWidgets.QTableWidgetItem("정상"))
            self.issue_table.setItem(0, 1, QtWidgets.QTableWidgetItem("전체"))
            self.issue_table.setItem(0, 2, QtWidgets.QTableWidgetItem("JSON 구조와 허용 액션 검사를 통과했습니다."))
        issue_layout.addWidget(self.issue_table, 1)
        splitter.addWidget(issue_box)

        asset_box = QtWidgets.QWidget()
        asset_layout = QtWidgets.QVBoxLayout(asset_box)
        asset_layout.setContentsMargins(8, 0, 0, 0)
        asset_layout.addWidget(QtWidgets.QLabel("이미지 설정 미리보기"))
        self.assets = QtWidgets.QListWidget()
        self.assets.setViewMode(QtWidgets.QListView.IconMode)
        self.assets.setResizeMode(QtWidgets.QListView.Adjust)
        self.assets.setIconSize(QtCore.QSize(150, 96))
        self.assets.setGridSize(QtCore.QSize(185, 145))
        self.assets.setWordWrap(True)
        for asset in payload.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            item = QtWidgets.QListWidgetItem(str(asset.get("label") or asset.get("id") or "이미지"))
            candidate = str(asset.get("candidate") or "")
            if not candidate and package_stage is not None:
                try:
                    manifest = json.loads((package_stage / "asset-manifest.json").read_text(encoding="utf-8-sig"))
                except (OSError, TypeError, ValueError):
                    manifest = {}
                match = next(
                    (row for row in manifest.get("assets", []) if isinstance(row, dict) and str(row.get("id")) == str(asset.get("id"))),
                    {},
                )
                candidate = str(match.get("selected_candidate") or "")
            path = (package_stage / candidate).resolve() if package_stage is not None and candidate else None
            if path is not None and path.is_file():
                item.setIcon(QtGui.QIcon(str(path)))
                item.setToolTip(str(path))
            else:
                item.setToolTip("이미지 후보를 찾지 못했습니다. 가져온 뒤 설정하세요.")
            self.assets.addItem(item)
        asset_layout.addWidget(self.assets, 1)
        splitter.addWidget(asset_box)
        splitter.setSizes([620, 480])
        root.addWidget(splitter, 1)

        note = QtWidgets.QLabel(
            "가져온 매크로는 기존 파일을 덮어쓰지 않고 AI 초안으로 저장됩니다. 미완성 노드는 정식 실행이 차단되며 드라이런만 허용됩니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("background:#111A27; border:1px solid #2B3B52; border-radius:8px; padding:10px;")
        root.addWidget(note)
        buttons = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("취소")
        cancel.clicked.connect(self.reject)
        self.import_button = QtWidgets.QPushButton("AI 초안으로 가져오기")
        self.import_button.setObjectName("Primary")
        self.import_button.setEnabled(not any(issue.severity == "error" for issue in self.issues))
        self.import_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.import_button)
        root.addLayout(buttons)


class AIDraftSetupDialog(QtWidgets.QDialog):
    macro_changed = QtCore.Signal(dict)
    step_edit_requested = QtCore.Signal(int)
    inactive_lab_requested = QtCore.Signal()
    dry_run_requested = QtCore.Signal()

    def __init__(self, macro: dict[str, Any], repository, parent=None) -> None:
        super().__init__(parent)
        self.macro = deepcopy(macro)
        self.repository = repository
        self.setWindowTitle("AI 초안 설정 마법사")
        self.resize(1280, 800)
        self.setMinimumSize(1040, 680)
        root = QtWidgets.QVBoxLayout(self)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel()
        self.title.setStyleSheet("font-size:18pt; font-weight:800;")
        self.readiness = QtWidgets.QLabel()
        self.readiness.setStyleSheet("color:#FFB35C; font-weight:700;")
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.readiness)
        root.addLayout(header)
        hint = QtWidgets.QLabel(
            "한 화면에서 미완성 항목을 정리합니다. 화면 캡처·이미지 편집·핸들 시험처럼 실제 조작이 필요한 경우에만 별도 창이 열립니다."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        target_box = QtWidgets.QGroupBox("대상 프로그램 프로필 · 실행할 때마다 현재 창과 핸들을 다시 탐색")
        target_layout = QtWidgets.QVBoxLayout(target_box)
        self.targets = QtWidgets.QTableWidget()
        self.targets.setColumnCount(5)
        self.targets.setHorizontalHeaderLabels(["프로필", "실행 파일", "창 제목 규칙", "창 클래스", "비활성 클릭"])
        self.targets.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.targets.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.targets.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.targets.verticalHeader().setVisible(False)
        self.targets.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.targets.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.targets.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.targets.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.targets.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.targets.setMaximumHeight(155)
        self.targets.doubleClicked.connect(lambda _index: self._edit_target_profile())
        target_layout.addWidget(self.targets)
        root.addWidget(target_box)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.steps = QtWidgets.QTableWidget()
        self.steps.setColumnCount(4)
        self.steps.setHorizontalHeaderLabels(["노드", "액션", "준비 상태", "필요한 설정"])
        self.steps.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.steps.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.steps.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.steps.verticalHeader().setVisible(False)
        self.steps.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.steps.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.steps.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.steps.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.steps.doubleClicked.connect(lambda index: self._edit_step(index.row()))
        splitter.addWidget(self.steps)
        self.gallery = QtWidgets.QListWidget()
        self.gallery.setViewMode(QtWidgets.QListView.IconMode)
        self.gallery.setResizeMode(QtWidgets.QListView.Adjust)
        self.gallery.setIconSize(QtCore.QSize(150, 96))
        self.gallery.setGridSize(QtCore.QSize(185, 150))
        self.gallery.setWordWrap(True)
        splitter.addWidget(self.gallery)
        splitter.setSizes([700, 500])
        root.addWidget(splitter, 1)

        controls = QtWidgets.QGridLayout()
        buttons: list[tuple[str, Any]] = [
            ("다음 미설정 항목", self._next_unresolved),
            ("가져오기 질문 답변", self._answer_requirements),
            ("대상 프로그램 설정", self._edit_target_profile),
            ("기존 자산 연결", self._link_existing_asset),
            ("화면에서 캡처", self._capture_asset),
            ("이미지 상세 편집", self._edit_asset),
            ("검색 테스트", self._test_asset),
            ("비활성 클릭 시험", self._request_inactive_lab),
            ("드라이런", self.dry_run_requested.emit),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button, index // 4, index % 4)
        root.addLayout(controls)
        footer = QtWidgets.QHBoxLayout()
        close = QtWidgets.QPushButton("나중에 계속")
        close.clicked.connect(self.reject)
        complete = QtWidgets.QPushButton("설정 완료")
        complete.setObjectName("Primary")
        complete.clicked.connect(self._complete)
        footer.addStretch(1)
        footer.addWidget(close)
        footer.addWidget(complete)
        root.addLayout(footer)
        self._refresh()

    def _refresh(self) -> None:
        complete, total, pending = ai_draft_readiness(self.macro)
        self.title.setText(f"{self.macro.get('name', 'AI 초안')} · 준비도 {complete}/{total}")
        self.readiness.setText("완료" if not pending else "설정 필요: " + ", ".join(pending))
        targets = (self.macro.get("ai_setup") or {}).get("targets") or []
        self.targets.setRowCount(len(targets))
        for row, target in enumerate(targets):
            values = [
                str(target.get("label") or target.get("id") or f"대상 {row + 1}"),
                str(target.get("exe") or ""),
                str(target.get("title") or target.get("window_token") or ""),
                str(target.get("class") or ""),
                "검증 완료" if bool(target.get("inactive_click_verified")) else "시험 필요",
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 4:
                    item.setForeground(QtGui.QColor("#65E0B5" if bool(target.get("inactive_click_verified")) else "#FFB35C"))
                self.targets.setItem(row, column, item)
        steps = self.macro.get("steps") if isinstance(self.macro.get("steps"), list) else []
        self.steps.setRowCount(len(steps))
        for row, step in enumerate(steps):
            needs = [str(value) for value in step.get("needs_setup") or [] if str(value)] if isinstance(step, dict) else []
            values = [
                str(row + 1),
                str(step.get("label") or step.get("action") or "") if isinstance(step, dict) else "",
                "설정 필요" if needs else "준비 완료",
                ", ".join(needs) if needs else "—",
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 2:
                    item.setForeground(QtGui.QColor("#FFB35C" if needs else "#65E0B5"))
                self.steps.setItem(row, column, item)
        self.gallery.clear()
        for row, step in enumerate(steps):
            if not isinstance(step, dict) or step.get("action") != "image_search":
                continue
            alias = str(step.get("asset") or "")
            needs = {str(value) for value in step.get("needs_setup") or [] if str(value)}
            image_ready = bool(alias)
            search_ready = image_ready and not bool(needs & {"select_asset", "confirm_asset", "choose_or_confirm_candidate", "verify_search"})
            click_ready = search_ready and "verify_inactive_click" not in needs
            item = QtWidgets.QListWidgetItem(
                f"{row + 1}번 · {alias or '이미지 필요'}\n"
                f"{'✓' if image_ready else '○'} 이미지  "
                f"{'✓' if search_ready else '○'} 검색  "
                f"{'✓' if click_ready else '○'} 클릭"
            )
            item.setData(QtCore.Qt.UserRole, row)
            path = self.repository.asset_path(alias) if alias else None
            if path is not None:
                item.setIcon(QtGui.QIcon(str(path)))
                item.setToolTip(
                    f"{path}\n\n1. 이미지 준비: {'통과' if image_ready else '설정 필요'}\n"
                    f"2. 검색 성공: {'통과' if search_ready else '테스트 필요'}\n"
                    f"3. 클릭 성공: {'통과' if click_ready else '비활성 클릭 시험 필요'}"
                )
            else:
                item.setToolTip("이미지를 연결하거나 캡처해야 합니다.")
            self.gallery.addItem(item)
        self.macro_changed.emit(deepcopy(self.macro))

    def _selected_image_row(self) -> int:
        item = self.gallery.currentItem()
        if item is not None:
            return int(item.data(QtCore.Qt.UserRole) or 0)
        row = self.steps.currentRow()
        steps = self.macro.get("steps") or []
        if 0 <= row < len(steps) and steps[row].get("action") == "image_search":
            return row
        return next((index for index, step in enumerate(steps) if step.get("action") == "image_search"), -1)

    def _next_unresolved(self) -> None:
        for row, step in enumerate(self.macro.get("steps") or []):
            if isinstance(step, dict) and step.get("needs_setup"):
                self.steps.selectRow(row)
                self.steps.scrollToItem(self.steps.item(row, 0))
                return
        if any(str(value).strip() for value in (self.macro.get("ai_setup") or {}).get("requirements") or []):
            self._answer_requirements()
            return
        QtWidgets.QMessageBox.information(self, "AI 초안", "노드별 미완성 설정이 없습니다.")

    def _answer_requirements(self) -> None:
        setup = self.macro.setdefault("ai_setup", {})
        requirements = [str(value) for value in setup.get("requirements") or [] if str(value).strip()]
        if not requirements:
            QtWidgets.QMessageBox.information(self, "가져오기 질문", "추가로 확인할 가져오기 질문이 없습니다.")
            return
        prompt = "\n".join(f"{index}. {value}" for index, value in enumerate(requirements, start=1))
        answer, accepted = QtWidgets.QInputDialog.getMultiLineText(
            self,
            "가져오기 질문 답변",
            "아래 미확정 항목을 확인하고 답변을 기록하세요. 관련 노드 설정도 함께 수정해야 준비 완료됩니다.\n\n" + prompt,
            str(setup.get("requirement_answers") or ""),
        )
        if not accepted or not answer.strip():
            return
        setup["requirement_answers"] = answer.strip()
        setup["requirements"] = []
        self._refresh()

    def _edit_target_profile(self) -> None:
        targets = (self.macro.get("ai_setup") or {}).get("targets") or []
        if not targets:
            QtWidgets.QMessageBox.information(self, "대상 프로그램", "이 AI 초안에는 대상 프로그램 프로필이 없습니다.")
            return
        row = self.targets.currentRow()
        if not 0 <= row < len(targets):
            row = 0
        target = targets[row]
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("대상 프로그램 프로필")
        dialog.setMinimumWidth(620)
        layout = QtWidgets.QVBoxLayout(dialog)
        note = QtWidgets.QLabel("숫자 핸들은 저장하지 않습니다. 실행 시 아래 규칙으로 현재 최상위 창과 하위 컨트롤을 다시 찾습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QtWidgets.QFormLayout()
        label = QtWidgets.QLineEdit(str(target.get("label") or ""))
        exe = QtWidgets.QLineEdit(str(target.get("exe") or ""))
        title = QtWidgets.QLineEdit(str(target.get("title") or ""))
        window_class = QtWidgets.QLineEdit(str(target.get("class") or ""))
        coordinate = QtWidgets.QComboBox()
        coordinate.addItem("클라이언트 상대 좌표", "client")
        coordinate.addItem("창 상대 좌표", "window")
        coordinate.addItem("화면 좌표", "screen")
        coordinate.setCurrentIndex(max(0, coordinate.findData(str(target.get("coordinate_base") or "client"))))
        form.addRow("표시 이름", label)
        form.addRow("실행 파일", exe)
        form.addRow("창 제목 포함 규칙", title)
        form.addRow("창 클래스", window_class)
        form.addRow("좌표 기준", coordinate)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        target.update({
            "label": label.text().strip() or str(target.get("id") or f"대상 {row + 1}"),
            "exe": exe.text().strip(),
            "title": title.text().strip(),
            "class": window_class.text().strip(),
            "coordinate_base": str(coordinate.currentData() or "client"),
            "reacquire_each_run": True,
        })
        if target["title"] and target["exe"]:
            target["window_token"] = f"{target['title']} ahk_exe {target['exe']}"
        elif target["class"] and target["exe"]:
            target["window_token"] = f"ahk_class {target['class']} ahk_exe {target['exe']}"
        elif target["exe"]:
            target["window_token"] = f"ahk_exe {target['exe']}"
        else:
            target["window_token"] = target["title"]
        self._sync_target_profile(str(target.get("id") or ""), target)
        self._refresh()

    def _sync_target_profile(self, target_id: str, target: dict[str, Any]) -> None:
        token = str(target.get("window_token") or "")
        exe = str(target.get("exe") or "")
        coordinate = str(target.get("coordinate_base") or "client")
        for step in self.macro.get("steps") or []:
            if not isinstance(step, dict) or str((step.get("_ai") or {}).get("target_ref") or "") != target_id:
                continue
            action = str(step.get("action") or "")
            if action == "image_search":
                step["region_window"] = token
                step["region_window_exe"] = exe
                step["region_mode"] = "client" if coordinate == "client" else "window" if coordinate == "window" else "screen"
                step["region_coords"] = "relative" if coordinate in {"client", "window"} else "screen"
                click = step.get("click") if isinstance(step.get("click"), dict) else {}
                click.update({"window": token, "window_exe": exe})
                step["click"] = click
            elif action in {"mouse_click", "inactive_click", "type_text"}:
                step["window"] = token
                step["window_exe"] = exe
                if action == "mouse_click":
                    step["coordinate_scope"] = coordinate
            elif action == "ocr":
                step["window_title"] = token
                step["capture_mode"] = coordinate
                step["coord_base"] = coordinate

    def _edit_step(self, row: int) -> None:
        if row >= 0:
            self.step_edit_requested.emit(row + 1)
            # The builder's detailed editor is non-modal. Close this modal
            # review window so the user can actually edit the selected node,
            # then reopen it with "미완성 설정 계속하기".
            self.reject()

    def _link_existing_asset(self) -> None:
        row = self._selected_image_row()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "이미지 연결", "이미지 서치 노드를 선택하세요.")
            return
        aliases = sorted(self.repository.load_assets())
        alias, okay = QtWidgets.QInputDialog.getItem(self, "기존 자산 연결", "이미지", aliases, editable=False)
        if not okay or not alias:
            return
        step = self.macro["steps"][row]
        step["asset"] = alias
        step["needs_setup"] = [value for value in step.get("needs_setup") or [] if value not in {"select_asset", "confirm_asset"}]
        self._refresh()

    def _capture_asset(self) -> None:
        row = self._selected_image_row()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "화면 캡처", "이미지 서치 노드를 선택하세요.")
            return
        host = self.window()
        host.hide()
        wait = QtCore.QEventLoop(self)
        QtCore.QTimer.singleShot(220, wait.quit)
        wait.exec()
        pixmap, geometry = capture_virtual_desktop()
        picker = ScreenCaptureDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        image = picker.captured_image() if accepted else QtGui.QImage()
        host.show()
        host.raise_()
        if image.isNull():
            return
        default = f"AI-{self.macro.get('name', '초안')}-{row + 1}"
        alias, okay = QtWidgets.QInputDialog.getText(self, "캡처 저장", "이미지 이름", text=default)
        if not okay or not alias.strip():
            return
        try:
            saved = self.repository.add_asset_image(image, alias.strip())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "캡처 실패", str(exc))
            return
        step = self.macro["steps"][row]
        step["asset"] = saved
        step["needs_setup"] = [value for value in step.get("needs_setup") or [] if value not in {"select_asset", "confirm_asset"}]
        self._refresh()

    def _edit_asset(self) -> None:
        row = self._selected_image_row()
        if row < 0:
            return
        alias = str(self.macro["steps"][row].get("asset") or "")
        path = self.repository.asset_path(alias) if alias else None
        if path is None:
            QtWidgets.QMessageBox.information(self, "이미지 편집", "먼저 이미지 자산을 연결하세요.")
            return
        dialog = ImageEditorDialog(path, alias, self.repository.history_dir, self)
        dialog.saved.connect(lambda _path: self.repository.refresh_asset_metadata(alias))
        dialog.exec()
        self._refresh()

    def _test_asset(self) -> None:
        row = self._selected_image_row()
        if row < 0:
            return
        step = self.macro["steps"][row]
        if not str(step.get("asset") or ""):
            QtWidgets.QMessageBox.information(self, "검색 테스트", "먼저 이미지 자산을 연결하세요.")
            return
        dialog = ImageSearchTestDialog(self.repository, step, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            step["needs_setup"] = [value for value in step.get("needs_setup") or [] if value not in {"confirm_asset", "verify_search"}]
            self._refresh()

    def _request_inactive_lab(self) -> None:
        self.inactive_lab_requested.emit()

    def apply_inactive_profile(self, profile: dict[str, Any]) -> None:
        exe = str(profile.get("window_exe") or "").strip().casefold()
        targets = (self.macro.get("ai_setup") or {}).get("targets") or []
        target_ids: set[str] = set()
        for target in targets:
            if not isinstance(target, dict) or (exe and str(target.get("exe") or "").strip().casefold() != exe):
                continue
            target["inactive_click_verified"] = True
            target["inactive_method"] = "handle_probe"
            target["target_control"] = str(profile.get("target_control") or "")
            target["target_child_class"] = str(profile.get("target_child_class") or "")
            target["reacquire_each_run"] = True
            target["needs_setup"] = [value for value in target.get("needs_setup") or [] if value != "verify_inactive_click"]
            target_ids.add(str(target.get("id") or ""))
        for step in self.macro.get("steps") or []:
            if not isinstance(step, dict):
                continue
            step_exe = str(step.get("window_exe") or step.get("region_window_exe") or "").strip().casefold()
            source_target = str((step.get("_ai") or {}).get("target_ref") or "")
            if target_ids and source_target not in target_ids and (not exe or step_exe != exe):
                continue
            if step.get("action") == "image_search":
                click = step.get("click") if isinstance(step.get("click"), dict) else {}
                click.update({
                    "mode": "inactive",
                    "method": "handle_probe",
                    "target_control": str(profile.get("target_control") or ""),
                    "target_child_class": str(profile.get("target_child_class") or ""),
                    # A numeric hwnd is deliberately not copied. Runtime must
                    # reacquire the current control after program restart.
                    "target_hwnd": "",
                })
                step["click"] = click
            elif step.get("action") in {"inactive_click", "type_text"}:
                step["method"] = "handle_probe"
                step["target_control"] = str(profile.get("target_control") or "")
                step["target_child_class"] = str(profile.get("target_child_class") or "")
                step["target_hwnd"] = ""
            step["needs_setup"] = [value for value in step.get("needs_setup") or [] if value != "verify_inactive_click"]
        self._refresh()

    def _complete(self) -> None:
        _complete, _total, pending = ai_draft_readiness(self.macro)
        if pending:
            QtWidgets.QMessageBox.warning(self, "설정 미완료", "다음 항목을 먼저 확인하세요:\n\n" + "\n".join(f"• {item}" for item in pending))
            return
        self.macro.setdefault("meta", {})["ai_draft"] = False
        self.macro.get("ai_setup", {}).update({"unresolved": [], "requirements": []})
        self.macro_changed.emit(deepcopy(self.macro))
        self.accept()
