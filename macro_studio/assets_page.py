from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop
from .repository import MacroRepository
from .widgets import Card, PageHeader, danger_button, primary_button


class AssetsPage(QtWidgets.QWidget):
    data_changed = QtCore.Signal()
    status = QtCore.Signal(str)

    def __init__(self, repository: MacroRepository, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.repository = repository
        self.current_alias = ""
        self.current_path: Path | None = None
        self._loaded_signature: tuple[int, int] | None = None
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
        self.search_edit.setPlaceholderText("이미지 이름 검색")
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
        detail_actions.addWidget(open_editor)
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
        previous = self.current_alias
        scroll_value = self.asset_list.verticalScrollBar().value()
        self._thumbnail_timer.stop()
        self._thumbnail_queue.clear()
        self.asset_list.clear()
        for alias, metadata in sorted(self.repository.load_assets().items(), key=lambda item: item[0].casefold()):
            path = (self.repository.root / str(metadata.get("file") or "")).resolve()
            item = QtWidgets.QListWidgetItem(alias)
            item.setData(QtCore.Qt.UserRole, alias)
            item.setData(QtCore.Qt.UserRole + 1, str(path))
            item.setToolTip(str(path))
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
        QtCore.QTimer.singleShot(0, lambda value=scroll_value: self.asset_list.verticalScrollBar().setValue(value))

    def _asset_signature(self) -> tuple[int, int]:
        try:
            stat = self.repository.assets_index_path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return 0, 0

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
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))

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
        self.info_label.setText(dimensions)
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
