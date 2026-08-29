from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop
from .repository import MacroRepository
from .widgets import Card, PageHeader, danger_button, primary_button


CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def _initials(text: str) -> str:
    result: list[str] = []
    for character in text:
        code = ord(character) - 0xAC00
        result.append(CHOSEONG[code // 588] if 0 <= code < 11172 else character.casefold())
    return "".join(result)


class AssetsPage(QtWidgets.QWidget):
    data_changed = QtCore.Signal()
    status = QtCore.Signal(str)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.current_alias = ""
        self.current_path: Path | None = None
        self._analysis: dict = {}
        self._loaded_signature: tuple[int, int, int, int] | None = None
        self._thumbnail_queue: list[tuple[QtWidgets.QListWidgetItem, Path]] = []
        self._thumbnail_cache: dict[str, tuple[int, QtGui.QIcon]] = {}
        self._thumbnail_timer = QtCore.QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self._load_thumbnail_batch)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)
        root.addWidget(PageHeader("이미지 편집", "드래그/Ctrl로 여러 이미지 선택 · Delete 삭제 · Ctrl+Z 복구"))

        toolbar = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("이름·태그·그룹 검색 (한글 초성 가능)")
        self.search_edit.textChanged.connect(self._filter)
        add_btn = primary_button("＋ 이미지 추가")
        add_btn.clicked.connect(self._add)
        capture_btn = QtWidgets.QPushButton("⌖ 범위 캡처")
        capture_btn.clicked.connect(self._capture)
        edit_btn = QtWidgets.QPushButton("✎ 선택 이미지 편집")
        edit_btn.clicked.connect(self._edit)
        sync_btn = QtWidgets.QPushButton("폴더 동기화")
        sync_btn.clicked.connect(self._sync)
        folder_btn = QtWidgets.QPushButton("폴더 열기")
        folder_btn.clicked.connect(self._open_folder)
        archive_btn = danger_button("보관")
        archive_btn.clicked.connect(self._archive)
        for widget in (self.search_edit, add_btn, capture_btn, edit_btn, sync_btn, folder_btn, archive_btn):
            toolbar.addWidget(widget, 1 if widget is self.search_edit else 0)
        root.addLayout(toolbar)

        management = QtWidgets.QHBoxLayout()
        self.filter_combo = QtWidgets.QComboBox()
        for label, value in (
            ("전체 이미지", "all"),
            ("사용 중", "used"),
            ("미사용", "unused"),
            ("완전 중복", "duplicate"),
            ("시각적으로 유사", "similar"),
            ("파일 없음", "missing"),
        ):
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(lambda _index: self._filter(self.search_edit.text()))
        self.group_combo = QtWidgets.QComboBox()
        self.group_combo.addItem("모든 그룹", "")
        self.group_combo.currentIndexChanged.connect(lambda _index: self._filter(self.search_edit.text()))
        organize_btn = QtWidgets.QPushButton("폴더·태그 지정")
        organize_btn.clicked.connect(self._organize_selected)
        self.analysis_label = QtWidgets.QLabel()
        self.analysis_label.setObjectName("Muted")
        management.addWidget(self.filter_combo)
        management.addWidget(self.group_combo)
        management.addWidget(organize_btn)
        management.addWidget(self.analysis_label, 1)
        root.addLayout(management)

        splitter = QtWidgets.QSplitter()
        splitter.setChildrenCollapsible(False)
        list_card = Card()
        list_layout = QtWidgets.QVBoxLayout(list_card)
        list_layout.setContentsMargins(12, 12, 12, 12)
        self.asset_list = QtWidgets.QListWidget()
        self.asset_list.setViewMode(QtWidgets.QListView.IconMode)
        self.asset_list.setIconSize(QtCore.QSize(110, 78))
        self.asset_list.setGridSize(QtCore.QSize(165, 126))
        self.asset_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.asset_list.setMovement(QtWidgets.QListView.Static)
        self.asset_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.asset_list.currentItemChanged.connect(self._select)
        self.asset_list.itemDoubleClicked.connect(lambda _item: self._edit())
        list_layout.addWidget(self.asset_list)
        splitter.addWidget(list_card)

        detail_card = Card()
        detail_layout = QtWidgets.QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(18, 18, 18, 18)
        self.preview = QtWidgets.QLabel("이미지를 선택하세요")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setMinimumSize(350, 280)
        self.preview.setStyleSheet("background:#0D0F14; border:1px dashed #394154; border-radius:9px;")
        self.alias_label = QtWidgets.QLabel()
        self.alias_label.setStyleSheet("font-size: 16pt; font-weight: 800;")
        self.info_label = QtWidgets.QLabel()
        self.info_label.setObjectName("Muted")
        self.path_label = QtWidgets.QLabel()
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        detail_layout.addWidget(self.preview, 1)
        detail_layout.addWidget(self.alias_label)
        detail_layout.addWidget(self.info_label)
        detail_layout.addWidget(self.path_label)
        detail_actions = QtWidgets.QHBoxLayout()
        open_editor = primary_button("편집기 열기")
        open_editor.clicked.connect(self._edit)
        restore_version = QtWidgets.QPushButton("버전 기록·복구")
        restore_version.clicked.connect(self._restore_asset_version)
        detail_actions.addWidget(open_editor)
        detail_actions.addWidget(restore_version)
        detail_actions.addStretch(1)
        detail_layout.addLayout(detail_actions)
        splitter.addWidget(detail_card)
        splitter.setSizes([900, 460])
        root.addWidget(splitter, 1)

    def refresh(self) -> None:
        signature = self._asset_signature()
        if self.asset_list.count() and signature == self._loaded_signature:
            self._filter(self.search_edit.text())
            return
        index = self.repository.load_assets()
        self._analysis = self.repository.analyze_assets()
        selected_group = str(self.group_combo.currentData() or "")
        groups = sorted(
            {str(metadata.get("group") or "").strip() for metadata in index.values() if isinstance(metadata, dict)} - {""},
            key=str.casefold,
        )
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("모든 그룹", "")
        for group in groups:
            self.group_combo.addItem(f"폴더 · {group}", group)
        group_index = self.group_combo.findData(selected_group)
        self.group_combo.setCurrentIndex(max(0, group_index))
        self.group_combo.blockSignals(False)
        previous = self.current_alias
        scroll_value = self.asset_list.verticalScrollBar().value()
        self._thumbnail_timer.stop()
        self._thumbnail_queue.clear()
        self.asset_list.clear()
        for alias, metadata in sorted(index.items(), key=lambda item: item[0].casefold()):
            path = (self.repository.root / str(metadata.get("file") or "")).resolve()
            group = str(metadata.get("group") or "").strip()
            tags = [str(value) for value in metadata.get("tags") or [] if str(value).strip()]
            suffix = f"\n{group}" if group else (f"\n#{tags[0]}" if tags else "")
            item = QtWidgets.QListWidgetItem(alias + suffix)
            item.setData(QtCore.Qt.UserRole, alias)
            item.setData(QtCore.Qt.UserRole + 1, str(path))
            item.setData(
                QtCore.Qt.UserRole + 2,
                {
                    "group": group,
                    "tags": tags,
                    "variant_group": str(metadata.get("variant_group") or ""),
                    "variant_kind": str(metadata.get("variant_kind") or ""),
                },
            )
            references = self._analysis.get("references", {}).get(alias, [])
            item.setToolTip(f"{path}\n사용 매크로: {', '.join(references) if references else '없음'}")
            if path.exists():
                cache = self._thumbnail_cache.get(str(path))
                modified = path.stat().st_mtime_ns
                if cache and cache[0] == modified:
                    item.setIcon(cache[1])
                else:
                    self._thumbnail_queue.append((item, path))
            else:
                item.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_MessageBoxWarning))
            self.asset_list.addItem(item)
            if alias == previous:
                self.asset_list.setCurrentItem(item)
        self._loaded_signature = signature
        self._filter(self.search_edit.text())
        if self.asset_list.count() and not self.asset_list.currentItem():
            self.asset_list.setCurrentRow(0)
        if self._thumbnail_queue:
            self._thumbnail_timer.start(0)
        total = len(index)
        unused = len(self._analysis.get("unused") or [])
        duplicates = len(self._analysis.get("duplicate_aliases") or [])
        similar = len(self._analysis.get("similar_aliases") or [])
        self.analysis_label.setText(f"전체 {total} · 미사용 {unused} · 중복 {duplicates} · 유사 {similar}")
        QtCore.QTimer.singleShot(0, lambda value=scroll_value: self.asset_list.verticalScrollBar().setValue(value))

    def _asset_signature(self) -> tuple[int, int, int, int]:
        try:
            stat = self.repository.assets_index_path.stat()
            macro_stats = [path.stat() for path in self.repository.macros_dir.glob("*.json") if path.is_file()]
            return stat.st_mtime_ns, stat.st_size, len(macro_stats), max((item.st_mtime_ns for item in macro_stats), default=0)
        except OSError:
            return 0, 0, 0, 0

    def _load_thumbnail_batch(self) -> None:
        for _ in range(min(3, len(self._thumbnail_queue))):
            item, path = self._thumbnail_queue.pop(0)
            if not path.exists() or item.listWidget() is not self.asset_list:
                continue
            reader = QtGui.QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                size.scale(self.asset_list.iconSize(), QtCore.Qt.KeepAspectRatio)
                reader.setScaledSize(size)
            image = reader.read()
            if not image.isNull():
                icon = QtGui.QIcon(QtGui.QPixmap.fromImage(image))
                item.setIcon(icon)
                try:
                    self._thumbnail_cache[str(path)] = (path.stat().st_mtime_ns, icon)
                except OSError:
                    pass
        if self._thumbnail_queue:
            self._thumbnail_timer.start(1)

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        mode = str(self.filter_combo.currentData() or "all")
        group_filter = str(self.group_combo.currentData() or "")
        references = self._analysis.get("references") or {}
        unused = set(self._analysis.get("unused") or [])
        duplicates = set(self._analysis.get("duplicate_aliases") or [])
        similar = set(self._analysis.get("similar_aliases") or [])
        missing = set(self._analysis.get("missing") or [])
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            alias = str(item.data(QtCore.Qt.UserRole) or "")
            metadata = item.data(QtCore.Qt.UserRole + 2) or {}
            group = str(metadata.get("group") or "")
            tags = " ".join(str(value) for value in metadata.get("tags") or [])
            searchable = f"{alias} {group} {tags}".casefold()
            matches_text = not query or query in searchable or query in _initials(searchable)
            matches_group = not group_filter or group == group_filter
            matches_mode = {
                "all": True,
                "used": bool(references.get(alias)),
                "unused": alias in unused,
                "duplicate": alias in duplicates,
                "similar": alias in similar,
                "missing": alias in missing,
            }.get(mode, True)
            item.setHidden(not (matches_text and matches_group and matches_mode))

    def _select(self, current, _previous) -> None:
        if not current:
            self.current_alias = ""
            self.current_path = None
            return
        self.current_alias = str(current.data(QtCore.Qt.UserRole))
        self.current_path = Path(str(current.data(QtCore.Qt.UserRole + 1)))
        image = QtGui.QImage(str(self.current_path))
        pixmap = QtGui.QPixmap.fromImage(image)
        if pixmap.isNull():
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText("미리보기를 불러올 수 없습니다.")
            dimensions = "파일을 확인하세요"
        else:
            self.preview.setText("")
            self.preview.setPixmap(pixmap.scaled(440, 390, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            alpha = " · 투명도 포함" if image.hasAlphaChannel() else ""
            size = self.current_path.stat().st_size if self.current_path.exists() else 0
            dimensions = f"{image.width()} × {image.height()} px · {size / 1024:.1f} KB{alpha}"
        self.alias_label.setText(self.current_alias)
        metadata = current.data(QtCore.Qt.UserRole + 2) or {}
        group = str(metadata.get("group") or "미분류")
        tags = ", ".join(str(value) for value in metadata.get("tags") or []) or "없음"
        variant_group = str(metadata.get("variant_group") or "")
        variant_kind = str(metadata.get("variant_kind") or "")
        variant = f"{variant_group} / {variant_kind or '변형'}" if variant_group else "지정 안 함"
        references = self._analysis.get("references", {}).get(self.current_alias, [])
        usage = ", ".join(references) if references else "미사용 이미지"
        self.info_label.setText(f"{dimensions}\n폴더: {group} · 태그: {tags}\n변형 묶음: {variant}\n사용 매크로: {usage}")
        self.path_label.setText(str(self.current_path))

    def _add(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "이미지 추가", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not filename:
            return
        alias, ok = QtWidgets.QInputDialog.getText(self, "이미지 이름", "매크로에서 사용할 이름", text=Path(filename).stem)
        if not ok:
            return
        try:
            key = self.repository.add_asset(Path(filename), alias)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "추가 실패", str(exc))
            return
        self.current_alias = key
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"'{key}' 이미지를 추가했습니다.")

    def _capture(self) -> None:
        host = self.window()
        host.hide()
        QtCore.QTimer.singleShot(280, lambda: self._perform_capture(host))

    def _perform_capture(self, host: QtWidgets.QWidget) -> None:
        pixmap, geometry = capture_virtual_desktop()
        if pixmap.isNull() or not geometry.isValid():
            host.show()
            return
        picker = ScreenCaptureDialog(pixmap, geometry)
        accepted = picker.exec() == QtWidgets.QDialog.Accepted
        captured = picker.captured_image() if accepted else QtGui.QImage()
        host.show()
        host.raise_()
        host.activateWindow()
        if captured.isNull():
            return
        default = datetime.now().strftime("capture-%Y%m%d-%H%M%S")
        alias, ok = QtWidgets.QInputDialog.getText(self, "캡처 저장", "이미지 이름", text=default)
        if not ok or not alias.strip():
            return
        try:
            key = self.repository.add_asset_image(captured, alias)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "캡처 저장 실패", str(exc))
            return
        self.current_alias = key
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"'{key}' 범위 캡처를 저장했습니다.")

    def _edit(self) -> None:
        if not self.current_alias or self.current_path is None or not self.current_path.exists():
            self.status.emit("편집할 이미지를 선택하세요.")
            return
        dialog = ImageEditorDialog(self.current_path, self.current_alias, self.repository.history_dir, self)
        dialog.saved.connect(lambda _path: self.repository.refresh_asset_metadata(self.current_alias))
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._loaded_signature = None
            self.refresh()
            self.data_changed.emit()
            self.status.emit(f"'{self.current_alias}' 이미지를 저장했습니다. 원본은 작업 기록에 백업했습니다.")

    def _sync(self) -> None:
        try:
            count = self.repository.sync_assets()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "동기화 실패", str(exc))
            return
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"이미지 폴더에서 {count}개를 새로 등록했습니다.")

    def _open_folder(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.repository.assets_dir.resolve())))

    def _organize_selected(self) -> None:
        aliases = [str(item.data(QtCore.Qt.UserRole) or "") for item in self.asset_list.selectedItems()]
        if not aliases and self.current_alias:
            aliases = [self.current_alias]
        aliases = [alias for alias in aliases if alias]
        if not aliases:
            self.status.emit("폴더나 태그를 지정할 이미지를 선택하세요.")
            return
        index = self.repository.load_assets()
        first = index.get(aliases[0]) if isinstance(index.get(aliases[0]), dict) else {}
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"이미지 {len(aliases)}개 분류")
        dialog.setMinimumWidth(430)
        layout = QtWidgets.QFormLayout(dialog)
        group_edit = QtWidgets.QLineEdit(str(first.get("group") or ""))
        group_edit.setPlaceholderText("예: 로그인, 전투, 공통 버튼")
        tags_edit = QtWidgets.QLineEdit(", ".join(str(value) for value in first.get("tags") or []))
        tags_edit.setPlaceholderText("쉼표로 구분: 버튼, 파란색, 확인")
        variant_group_edit = QtWidgets.QLineEdit(str(first.get("variant_group") or ""))
        variant_group_edit.setPlaceholderText("예: 로그인 확인 버튼")
        variant_kind_combo = QtWidgets.QComboBox()
        for label, value in (("지정 안 함", ""), ("원본", "original"), ("누끼", "cutout"), ("흑백", "grayscale"), ("윤곽", "edge"), ("기타", "variant")):
            variant_kind_combo.addItem(label, value)
        variant_kind_combo.setCurrentIndex(max(0, variant_kind_combo.findData(str(first.get("variant_kind") or ""))))
        layout.addRow("폴더", group_edit)
        layout.addRow("태그", tags_edit)
        layout.addRow("변형 이미지 묶음", variant_group_edit)
        layout.addRow("변형 종류", variant_kind_combo)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        tags = [value.strip() for value in tags_edit.text().split(",") if value.strip()]
        self.repository.update_asset_organization(
            aliases,
            group_edit.text(),
            tags,
            variant_group_edit.text(),
            str(variant_kind_combo.currentData() or ""),
        )
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"이미지 {len(aliases)}개의 폴더·태그를 저장했습니다.")

    def _restore_asset_version(self) -> None:
        if not self.current_alias:
            self.status.emit("버전 기록을 볼 이미지를 선택하세요.")
            return
        versions = self.repository.list_asset_versions(self.current_alias)
        if not versions:
            QtWidgets.QMessageBox.information(self, "이미지 버전 기록", "아직 저장된 이전 이미지 버전이 없습니다.")
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"이미지 버전 기록 · {self.current_alias}")
        dialog.resize(700, 480)
        layout = QtWidgets.QVBoxLayout(dialog)
        hint = QtWidgets.QLabel("편집 전에 자동 저장된 이미지를 선택하세요. 현재 이미지는 복구 전에 다시 백업됩니다.")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        items = QtWidgets.QListWidget()
        items.setViewMode(QtWidgets.QListView.IconMode)
        items.setIconSize(QtCore.QSize(150, 105))
        items.setGridSize(QtCore.QSize(190, 145))
        for path in versions:
            modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M:%S")
            item = QtWidgets.QListWidgetItem(QtGui.QIcon(str(path)), modified)
            item.setData(QtCore.Qt.UserRole, str(path))
            item.setToolTip(str(path))
            items.addItem(item)
        items.setCurrentRow(0)
        layout.addWidget(items, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.RestoreDefaults | QtWidgets.QDialogButtonBox.Cancel)
        restore = buttons.button(QtWidgets.QDialogButtonBox.RestoreDefaults)
        restore.setText("선택 버전 복구")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.Accepted or items.currentItem() is None:
            return
        try:
            self.repository.restore_asset_version(
                self.current_alias, Path(str(items.currentItem().data(QtCore.Qt.UserRole)))
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "이미지 복구 실패", str(exc))
            return
        self._thumbnail_cache.pop(str(self.current_path), None)
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"'{self.current_alias}' 이미지의 이전 버전을 복구했습니다.")

    def _archive(self) -> None:
        self._archive_selected(confirm=True)

    def _archive_selected(self, confirm: bool) -> dict | None:
        aliases = [str(item.data(QtCore.Qt.UserRole) or "") for item in self.asset_list.selectedItems()]
        aliases = [alias for alias in aliases if alias]
        if not aliases and self.current_alias:
            aliases = [self.current_alias]
        if not aliases:
            return None
        if confirm:
            answer = QtWidgets.QMessageBox.question(self, "이미지 보관", f"선택한 {len(aliases)}개 이미지를 보관할까요?\n파일은 assets/.trash에 남습니다.")
            if answer != QtWidgets.QMessageBox.Yes:
                return None
        index = self.repository.load_assets()
        records: list[dict] = []
        for alias in aliases:
            metadata = index.get(alias)
            if not isinstance(metadata, dict):
                continue
            target = self.repository.archive_asset(alias)
            records.append(
                {
                    "kind": "asset",
                    "alias": alias,
                    "metadata": deepcopy(metadata),
                    "archive_path": str(target) if target else "",
                }
            )
        self.current_alias = ""
        self.current_path = None
        self._loaded_signature = None
        self.refresh()
        self.data_changed.emit()
        self.status.emit(f"이미지 {len(records)}개를 보관했습니다.")
        if not records:
            return None
        return records[0] if len(records) == 1 else {"kind": "batch", "items": records}

    def delete_selected(self) -> dict | None:
        return self._archive_selected(confirm=False)
