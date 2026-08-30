from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .assets_page import AssetsPage
from .builder import BuilderPage
from .data_page import DataPage
from .export_page import ExportPage
from .hotkeys_page import HotkeysPage
from .repository import MacroRepository
from .settings_page import SettingsPage
from .shortcuts import STUDIO_SHORTCUT_SPECS
from .theme import COLORS
from .trigger_engine import EventTriggerEngine


NAV_ITEMS = [
    ("builder", "◇  매크로 빌더"),
    ("assets", "▧  이미지 편집"),
    ("data", "▦  데이터 테이블"),
    ("hotkeys", "⌨  Quick Slots"),
    ("export", "⇧  내보내기"),
    ("settings", "⚙  설정"),
]

NAV_SHORTCUT_IDS = {
    "builder": "tab_macro",
    "assets": "tab_image",
    "data": "tab_data",
    "hotkeys": "tab_hotkey",
    "export": "tab_export",
    "settings": "tab_hotkey_settings",
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, repository: MacroRepository) -> None:
        super().__init__()
        self.repository = repository
        self._undo_deletions: list[dict] = []
        self._running_macro_processes: dict[
            int, tuple[str, object, Path | None, Path | None, Path | None]
        ] = {}
        self._seen_click_traces: dict[int, str] = {}
        self._run_control_paths: dict[int, Path] = {}
        self._run_variable_paths: dict[int, Path] = {}
        self._run_trace_paths: dict[int, Path] = {}
        self._trace_signatures: dict[int, tuple[int, int]] = {}
        self._run_resource_stats: dict[int, dict[str, float]] = {}
        self._failure_capture_steps: set[tuple[int, int]] = set()
        self._run_monitor = QtCore.QTimer(self)
        self._run_monitor.setInterval(100)
        self._run_monitor.timeout.connect(self._poll_running_macros)
        self._event_trigger_engine = EventTriggerEngine(repository)
        self._event_trigger_queue: list[tuple[str, str]] = []
        self._event_trigger_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="macrorelay-trigger")
        self._event_trigger_future: Future[list[tuple[str, str]]] | None = None
        self._event_trigger_timer = QtCore.QTimer(self)
        # Trigger entries apply their own low-frequency interval (500 ms for
        # AI start-screen conditions). This short dispatcher tick avoids
        # adding a full extra second of UI-side latency.
        self._event_trigger_timer.setInterval(250)
        self._event_trigger_timer.timeout.connect(self._poll_event_triggers)
        self.setWindowTitle("MacroRelay Studio")
        self.setMinimumSize(1120, 700)
        self.resize(1780, 980)
        self.settings = QtCore.QSettings("MacroRelay", "Studio")
        self._sidebar_collapsed = bool(self.settings.value("sidebar_collapsed", False, type=bool))
        self._resolved_shortcuts: dict[str, str] = {}
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        if str(self.settings.value("layout_version", "")) != "2.14-wide":
            self.resize(1780, max(900, self.height()))
            self.settings.setValue("layout_version", "2.14-wide")

        root = QtWidgets.QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.pages = {
            "builder": BuilderPage(repository),
            "assets": AssetsPage(repository),
            "data": DataPage(repository),
            "hotkeys": HotkeysPage(repository),
            "export": ExportPage(repository),
            "settings": SettingsPage(repository),
        }
        for key, _label in NAV_ITEMS:
            self.stack.addWidget(self.pages[key])
        self._connect_pages()
        self._event_trigger_timer.start()
        self._studio_shortcuts: list[QtGui.QShortcut] = []
        self._fixed_record_stop_shortcut = QtGui.QShortcut(QtGui.QKeySequence("F10"), self)
        self._fixed_record_stop_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self._fixed_record_stop_shortcut.activated.connect(
            lambda: self._invoke_page_method("builder", "_stop_smart_recording")
        )
        self._register_studio_shortcuts(self.repository.load_hotkey_actions())
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.statusBar().showMessage(f"데이터 위치: {repository.root}")
        self.switch_page("builder")

    def _build_sidebar(self) -> QtWidgets.QWidget:
        sidebar = QtWidgets.QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        layout = QtWidgets.QVBoxLayout(sidebar)
        self.sidebar_layout = layout
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(5)
        header = QtWidgets.QHBoxLayout()
        self.sidebar_brand = QtWidgets.QLabel("MACRO\nRELAY")
        self.sidebar_brand.setStyleSheet(f"font-size:18pt; font-weight:800; color:{COLORS['accent']}; padding: 4px 8px 18px 8px;")
        self.sidebar_toggle = QtWidgets.QPushButton("‹")
        self.sidebar_toggle.setFixedSize(38, 38)
        self.sidebar_toggle.setToolTip("좌측 메뉴 접기/펼치기")
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        header.addWidget(self.sidebar_brand, 1)
        header.addWidget(self.sidebar_toggle, 0, QtCore.Qt.AlignTop)
        self.sidebar_caption = QtWidgets.QLabel("STUDIO · AUTOMATION")
        self.sidebar_caption.setObjectName("Muted")
        self.sidebar_caption.setStyleSheet("padding: 0 8px 14px 8px;")
        layout.addLayout(header)
        layout.addWidget(self.sidebar_caption)
        self.nav_group = QtWidgets.QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QtWidgets.QPushButton] = {}
        for key, label in NAV_ITEMS:
            button = QtWidgets.QPushButton(label)
            button.setObjectName("Nav")
            button.setProperty("nav_base", label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=key: self.switch_page(page))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)
            if key == "export":
                layout.addStretch(1)
        self.sidebar_footer = QtWidgets.QLabel("MacroRelay · JSON/AHK 호환")
        self.sidebar_footer.setObjectName("Muted")
        self.sidebar_footer.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.sidebar_footer)
        self._apply_sidebar_state(save=False)
        return sidebar

    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        self._apply_sidebar_state()

    def _apply_sidebar_state(self, save: bool = True) -> None:
        if not hasattr(self, "sidebar"):
            return
        collapsed = self._sidebar_collapsed
        self.sidebar.setFixedWidth(56 if collapsed else 260)
        if hasattr(self, "sidebar_layout"):
            margins = (6, 12, 6, 12) if collapsed else (14, 20, 14, 16)
            self.sidebar_layout.setContentsMargins(*margins)
        self.sidebar_brand.setVisible(not collapsed)
        self.sidebar_caption.setVisible(not collapsed)
        self.sidebar_footer.setVisible(not collapsed)
        self.sidebar_toggle.setText("☰" if collapsed else "‹")
        self._refresh_nav_labels()
        if save:
            self.settings.setValue("sidebar_collapsed", collapsed)

    def _refresh_nav_labels(self) -> None:
        nav_labels = dict(NAV_ITEMS)
        for key, button in getattr(self, "nav_buttons", {}).items():
            base = nav_labels.get(key, str(button.property("nav_base") or button.text()))
            sequence = self._resolved_shortcuts.get(NAV_SHORTCUT_IDS.get(key, ""), "")
            button.setToolTip(f"{base}{f' · {sequence}' if sequence else ''}")
            if self._sidebar_collapsed:
                button.setText(base.split(maxsplit=1)[0])
            else:
                button.setText(f"{base}    {sequence}" if sequence else base)

    def _connect_pages(self) -> None:
        for page in self.pages.values():
            signal = getattr(page, "status", None)
            if signal is not None:
                signal.connect(self.show_status)
            run_signal = getattr(page, "run_macro", None)
            if run_signal is not None:
                run_signal.connect(self.run_macro)
            step_signal = getattr(page, "run_macro_step", None)
            if step_signal is not None:
                step_signal.connect(self.run_macro_step)
            resume_step_signal = getattr(page, "run_macro_from_step", None)
            if resume_step_signal is not None:
                resume_step_signal.connect(self.run_macro_from_step)
            dry_run_signal = getattr(page, "run_macro_dry_run", None)
            if dry_run_signal is not None:
                dry_run_signal.connect(self.run_macro_dry_run)
            changed = getattr(page, "data_changed", None)
            if changed is not None:
                changed.connect(self.refresh_all)
        self.pages["settings"].shortcut_config_changed.connect(self._register_studio_shortcuts)
        self.pages["builder"].open_export.connect(self._open_export_for_macro)
        self.pages["builder"].stop_macros.connect(self.stop_running_macros)
        self.pages["builder"].edit_committed.connect(self._undo_deletions.clear)

    @QtCore.Slot(str)
    def _open_export_for_macro(self, name: str) -> None:
        self.switch_page("export")
        self.pages["export"].select_macro(name)

    @QtCore.Slot(dict)
    def _register_studio_shortcuts(self, saved: dict | None = None) -> None:
        for shortcut in getattr(self, "_studio_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._studio_shortcuts = []
        saved = saved or {}
        resolved = {
            action_id: str(saved.get(action_id, default)).strip()
            for action_id, _label, default in STUDIO_SHORTCUT_SPECS
        }
        callbacks = {
            "tab_macro": lambda: self.switch_page("builder"),
            "tab_image": lambda: self.switch_page("assets"),
            "tab_browser": lambda: self._prepare_builder_action("browser_action"),
            "tab_hotkey": lambda: self.switch_page("hotkeys"),
            "tab_export": lambda: self.switch_page("export"),
            "tab_hotkey_settings": self._open_shortcut_settings,
            "tab_data": lambda: self.switch_page("data"),
            "helper_typing": lambda: self._prepare_builder_action("type_text"),
            "helper_delay": lambda: self._prepare_builder_action("wait"),
            "helper_coord": lambda: self._prepare_builder_action("coord_mode"),
            "helper_inactive": lambda: self._prepare_builder_action("inactive_click"),
            "helper_image": lambda: self._prepare_builder_action("image_search"),
            "helper_dom": lambda: self._prepare_builder_action("browser_action"),
            "helper_ocr": lambda: self._prepare_builder_action("ocr"),
            "helper_table": lambda: self._prepare_builder_action("table_copy"),
            "helper_flow": lambda: self._prepare_builder_action("flow_control"),
            "helper_submacro": lambda: self._prepare_builder_action("call_submacro"),
            "helper_text_condition": lambda: self._prepare_builder_action("text_condition"),
            "action_create_macro": self._create_macro_shortcut,
            "action_add_node": self._add_builder_node,
            "action_save_step": lambda: self._invoke_page_method("builder", "_save_step"),
            "action_run_macro": lambda: self._invoke_page_method("builder", "_run_current"),
            "action_duplicate_node": self._duplicate_current_node,
            "action_fit_nodes": lambda: self._invoke_page_method("builder", "node_canvas.fit_all"),
            "action_capture_cursor": lambda: self._invoke_page_method("builder", "capture_current_action_coordinates"),
            "action_open_step_editor": lambda: self._invoke_page_method("builder", "_open_action_settings"),
            "action_refresh_macros": lambda: self._invoke_page_method("builder", "refresh"),
            "action_smart_record": lambda: self._invoke_page_method("builder", "_start_smart_recording"),
            "action_quick_automation": lambda: self._invoke_page_method("builder", "_quick_action_wizard"),
            "action_diagnose_automation": lambda: self._invoke_page_method("builder", "_diagnose_automation"),
            "action_test_selected_step": lambda: self._invoke_page_method("builder", "_test_selected_step"),
            "action_add_asset": lambda: self._invoke_page_method("assets", "_add"),
            "action_capture_asset": lambda: self._invoke_page_method("assets", "_capture"),
            "action_edit_asset": lambda: self._invoke_page_method("assets", "_edit"),
            "action_refresh_assets": lambda: self._invoke_page_method("assets", "_sync"),
            "action_open_table_dialog": lambda: self.switch_page("data"),
            "action_refresh_tables": lambda: self._invoke_page_method("data", "refresh"),
            "action_delete_current": self._archive_current_selection,
            "action_export": lambda: self._invoke_page_method("export", "_export"),
            "action_preview": lambda: self._invoke_page_method("export", "_preview"),
            "action_test_run": lambda: self._invoke_page_method("export", "_run"),
        }
        for action_id, _label, default in STUDIO_SHORTCUT_SPECS:
            sequence = resolved.get(action_id, "")
            callback = callbacks.get(action_id)
            if not sequence or callback is None:
                continue
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
            shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self._studio_shortcuts.append(shortcut)
        self._apply_shortcut_labels(resolved)

    def _apply_shortcut_labels(self, resolved: dict[str, str]) -> None:
        self._resolved_shortcuts = dict(resolved)
        self._refresh_nav_labels()
        builder = self.pages.get("builder")
        apply_labels = getattr(builder, "set_shortcut_labels", None)
        if callable(apply_labels):
            apply_labels(resolved)

    def _open_shortcut_settings(self) -> None:
        self.switch_page("settings")
        self.pages["settings"].open_shortcut_settings()

    def _invoke_page_method(self, page: str, method_path: str) -> None:
        self.switch_page(page)
        target = self.pages[page]
        for part in method_path.split("."):
            target = getattr(target, part)
        if callable(target):
            target()

    def _add_builder_node(self) -> None:
        self.switch_page("builder")
        self.pages["builder"]._add_step()

    def _duplicate_current_node(self) -> None:
        self.switch_page("builder")
        builder = self.pages["builder"]
        builder._duplicate_node_from_graph(builder.node_canvas.selected_index())

    def _prepare_builder_action(self, action: str) -> None:
        self.switch_page("builder")
        builder = self.pages["builder"]
        builder.select_action(action)
        builder.action_combo.setFocus(QtCore.Qt.ShortcutFocusReason)
        self.show_status(f"'{action}' 노드 추가를 준비했습니다.")

    @QtCore.Slot(str)
    def open_macro_in_builder(self, name: str) -> None:
        self.stack.setCurrentWidget(self.pages["builder"])
        self.nav_buttons["builder"].setChecked(True)
        self.pages["builder"].refresh(name)

    def _create_macro_shortcut(self) -> None:
        self.switch_page("builder")
        self.pages["builder"]._create_macro()

    def _archive_current_selection(self) -> None:
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(
            focus,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
                QtWidgets.QKeySequenceEdit,
            ),
        ):
            return
        current = self.stack.currentWidget()
        delete_selected = getattr(current, "delete_selected", None)
        if not callable(delete_selected):
            self.show_status("현재 화면에는 단축키로 보관할 선택 항목이 없습니다.")
            return
        try:
            record = delete_selected()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "삭제 실패", str(exc))
            return
        if isinstance(record, dict):
            self._undo_deletions.append(record)
            self._undo_deletions = self._undo_deletions[-20:]
            self.show_status("선택 항목을 보관했습니다. Ctrl+Z로 복구할 수 있습니다.")

    def _restore_last_deletion(self) -> None:
        if not self._undo_deletions:
            self.show_status("복구할 최근 삭제 기록이 없습니다.")
            return
        record = self._undo_deletions[-1]
        kind = str(record.get("kind") or "")
        try:
            if kind == "batch":
                items = [dict(item) for item in record.get("items") or [] if isinstance(item, dict)]
                if not items:
                    raise ValueError("복구할 묶음 기록이 비어 있습니다.")
                restored_names: list[str] = []
                restored_assets: list[str] = []
                for item in items:
                    item_kind = str(item.get("kind") or "")
                    if item_kind == "macro":
                        target = self.repository.restore_macro(Path(str(item["archive_path"])), str(item["name"]))
                        restored_names.append(target.stem)
                    elif item_kind == "asset":
                        archived = str(item.get("archive_path") or "")
                        restored_assets.append(
                            self.repository.restore_asset(
                                str(item["alias"]),
                                dict(item["metadata"]),
                                Path(archived) if archived else None,
                            )
                        )
                    else:
                        raise ValueError("묶음에 지원하지 않는 삭제 기록이 있습니다.")
                if restored_names:
                    self.switch_page("builder")
                    self.pages["builder"].refresh(restored_names[-1])
                    message = f"매크로 {len(restored_names)}개를 복구했습니다."
                else:
                    page = self.pages["assets"]
                    page.current_alias = restored_assets[-1]
                    page._loaded_signature = None
                    self.switch_page("assets")
                    message = f"이미지 {len(restored_assets)}개를 복구했습니다."
            elif kind == "macro":
                target = self.repository.restore_macro(Path(str(record["archive_path"])), str(record["name"]))
                self.switch_page("builder")
                self.pages["builder"].refresh(target.stem)
                message = f"'{target.stem}' 매크로를 복구했습니다."
            elif kind == "macro_step":
                name = str(record["name"])
                self.repository.save_macro(name, dict(record["payload"]))
                self.switch_page("builder")
                self.pages["builder"].refresh(name)
                message = f"'{name}'의 삭제한 노드를 복구했습니다."
            elif kind == "asset":
                archived = str(record.get("archive_path") or "")
                alias = self.repository.restore_asset(
                    str(record["alias"]),
                    dict(record["metadata"]),
                    Path(archived) if archived else None,
                )
                page = self.pages["assets"]
                page.current_alias = alias
                page._loaded_signature = None
                self.switch_page("assets")
                message = f"'{alias}' 이미지를 복구했습니다."
            elif kind == "quick_slot":
                self.switch_page("hotkeys")
                restored = self.pages["hotkeys"].restore_deleted_slot(int(record["index"]), dict(record["slot"]))
                if not restored:
                    raise RuntimeError("Quick Slot 복구를 적용하지 못했습니다.")
                message = f"Quick Slot {int(record['index']) + 1}을 복구했습니다."
            elif kind == "quick_slots":
                self.switch_page("hotkeys")
                slots = [dict(item) for item in record.get("slots") or [] if isinstance(item, dict)]
                restored = self.pages["hotkeys"].restore_deleted_slots(slots)
                if not restored:
                    raise RuntimeError("Quick Slots 복구를 적용하지 못했습니다.")
                message = f"Quick Slot {len(slots)}개를 복구했습니다."
            else:
                raise ValueError("지원하지 않는 삭제 기록입니다.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "복구 실패", str(exc))
            return
        self._undo_deletions.pop()
        self.show_status(message)

    @staticmethod
    def _is_editing_widget(widget) -> bool:
        if isinstance(
            widget,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
                QtWidgets.QKeySequenceEdit,
            ),
        ):
            return True
        return isinstance(widget, QtWidgets.QComboBox) and widget.isEditable()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QtCore.QEvent.KeyPress and not event.isAutoRepeat():
            current = self.stack.currentWidget() if hasattr(self, "stack") else None
            managed = current in (self.pages.get("builder"), self.pages.get("assets"), self.pages.get("hotkeys")) if hasattr(self, "pages") else False
            if managed and not self._is_editing_widget(QtWidgets.QApplication.focusWidget()):
                modifiers = event.modifiers()
                if event.key() == QtCore.Qt.Key_Delete and modifiers == QtCore.Qt.NoModifier:
                    self._archive_current_selection()
                    return True
                if event.key() == QtCore.Qt.Key_Z and modifiers == QtCore.Qt.ControlModifier:
                    if self._undo_deletions:
                        self._restore_last_deletion()
                    elif current is self.pages.get("builder"):
                        current.undo_edit()
                    return True
                if event.key() == QtCore.Qt.Key_Y and modifiers == QtCore.Qt.ControlModifier:
                    if current is self.pages.get("builder"):
                        current.redo_edit()
                    return True
        return super().eventFilter(watched, event)

    @QtCore.Slot(str)
    def switch_page(self, key: str) -> None:
        if key not in self.pages:
            return
        self.stack.setCurrentWidget(self.pages[key])
        self.nav_buttons[key].setChecked(True)
        refresh = getattr(self.pages[key], "refresh", None)
        if callable(refresh):
            refresh()

    @QtCore.Slot()
    def refresh_all(self) -> None:
        # 현재 페이지가 자신의 변경을 이미 반영합니다.
        # 숨겨진 페이지는 열 때만 refresh하여 이미지 디코딩 지연을 막습니다.
        return

    @QtCore.Slot(str)
    def run_macro(self, name: str) -> None:
        if self._running_macro_processes:
            self.show_status("이미 매크로가 실행 중입니다. 먼저 ■ 정지를 눌러 주세요.")
            return
        self._append_run_log(f"실행 요청 | {name}")
        try:
            process = self.repository.run_macro(name)
        except Exception as exc:
            self._append_run_log(f"실행 실패 | {name} | {exc}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "실행 실패", str(exc))
            return
        self._track_macro_process(name, process)

    def _poll_event_triggers(self) -> None:
        future = self._event_trigger_future
        if future is None:
            self._event_trigger_future = self._event_trigger_executor.submit(self._event_trigger_engine.poll)
            return
        if not future.done():
            return
        self._event_trigger_future = None
        try:
            fired = future.result()
        except Exception as exc:
            self._append_run_log(f"이벤트 트리거 확인 실패 | {exc}", "ERROR")
            return
        for item in fired:
            if item not in self._event_trigger_queue:
                self._event_trigger_queue.append(item)
                self._append_run_log(f"이벤트 트리거 감지 | {item[0]} | {item[1]}")
        if self._running_macro_processes or not self._event_trigger_queue:
            return
        name, kind = self._event_trigger_queue.pop(0)
        self._append_run_log(f"이벤트 자동 실행 | {name} | {kind}")
        self.run_macro(name)

    @QtCore.Slot(str, int)
    def run_macro_step(self, name: str, step_index: int) -> None:
        if self._running_macro_processes:
            self.show_status("이미 매크로가 실행 중입니다. 먼저 ■ 정지를 눌러 주세요.")
            return
        display_name = f"{name} · {step_index}번 단계 테스트"
        self._append_run_log(f"단계 테스트 요청 | {name} | {step_index}번")
        try:
            process = self.repository.run_macro_step(name, step_index)
        except Exception as exc:
            self._append_run_log(f"단계 테스트 실패 | {display_name} | {exc}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "단계 테스트 실패", str(exc))
            return
        self._track_macro_process(display_name, process)

    @QtCore.Slot(str, int)
    def run_macro_from_step(self, name: str, step_index: int) -> None:
        if self._running_macro_processes:
            self.show_status("이미 매크로가 실행 중입니다. 먼저 ■ 정지를 눌러 주세요.")
            return
        self._append_run_log(f"선택 노드부터 실행 요청 | {name} | {step_index}번")
        try:
            process = self.repository.run_macro_from_step(name, step_index)
        except Exception as exc:
            self._append_run_log(f"선택 노드부터 실행 실패 | {name} | {exc}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "실행 실패", str(exc))
            return
        self._track_macro_process(name, process)

    @QtCore.Slot(str)
    def run_macro_dry_run(self, name: str) -> None:
        if self._running_macro_processes:
            self.show_status("이미 매크로가 실행 중입니다. 먼저 ■ 정지를 눌러 주세요.")
            return
        self._append_run_log(f"드라이런 요청 | {name}")
        try:
            process = self.repository.run_macro_dry_run(name)
        except Exception as exc:
            self._append_run_log(f"드라이런 실패 | {name} | {exc}", "ERROR")
            QtWidgets.QMessageBox.warning(self, "드라이런 실패", str(exc))
            return
        self._track_macro_process(f"{name} · 드라이런", process)

    def _track_macro_process(self, name: str, process: object) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        self._append_run_log(f"프로세스 시작 | {name} | PID {pid}")
        resume_step = int(getattr(process, "macrorelay_resume_step", 0) or 0)
        if resume_step > 0:
            self._append_run_log(f"체크포인트 자동 재개 | {name} | {resume_step}번 노드", "WARN")
        if pid:
            result_path = getattr(process, "macrorelay_result_path", None)
            progress_path = getattr(process, "macrorelay_progress_path", None)
            click_path = getattr(process, "macrorelay_click_path", None)
            control_path = getattr(process, "macrorelay_control_path", None)
            trace_path = getattr(process, "macrorelay_trace_path", None)
            variable_path = getattr(process, "macrorelay_variable_path", None)
            self._running_macro_processes[pid] = (
                name,
                process,
                result_path if isinstance(result_path, Path) else None,
                progress_path if isinstance(progress_path, Path) else None,
                click_path if isinstance(click_path, Path) else None,
            )
            self._run_resource_stats[pid] = {"last": 0.0, "cpu_total": 0.0, "samples": 0.0, "cpu_max": 0.0, "memory_max": 0.0}
            if isinstance(control_path, Path):
                self._run_control_paths[pid] = control_path
            if isinstance(trace_path, Path):
                self._run_trace_paths[pid] = trace_path
            if isinstance(variable_path, Path):
                self._run_variable_paths[pid] = variable_path
            builder = self.pages.get("builder")
            clear_states = getattr(builder, "clear_execution_states", None)
            if callable(clear_states):
                clear_states()
            if not self._run_monitor.isActive():
                self._run_monitor.start()
        self._update_macro_run_state()
        self.show_status(f"'{name}' 매크로를 실행했습니다. PID {pid or '-'}")

    @staticmethod
    def _terminate_macro_process(pid: int, process: object) -> None:
        try:
            if process.poll() is not None:
                return
        except Exception:
            pass
        if os.name == "nt" and pid > 0:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return
        try:
            process.terminate()
            process.wait(timeout=0.8)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    @QtCore.Slot()
    def stop_running_macros(self) -> None:
        running = tuple(self._running_macro_processes.items())
        if not running:
            self.show_status("현재 실행 중인 매크로가 없습니다.")
            self._update_macro_run_state()
            return
        stopped = 0
        for pid, (name, process, _result_path, _progress_path, _click_path) in running:
            self._append_run_log(f"사용자 정지 요청 | {name} | PID {pid}", "WARN")
            self._terminate_macro_process(pid, process)
            self._set_running_node(name, 0)
            self._running_macro_processes.pop(pid, None)
            self._seen_click_traces.pop(pid, None)
            self._run_control_paths.pop(pid, None)
            self._run_variable_paths.pop(pid, None)
            self._run_trace_paths.pop(pid, None)
            self._trace_signatures.pop(pid, None)
            self._run_resource_stats.pop(pid, None)
            self._failure_capture_steps = {key for key in self._failure_capture_steps if key[0] != pid}
            self._append_run_log(f"사용자 정지 완료 | {name} | PID {pid}", "WARN")
            stopped += 1
        self._run_monitor.stop()
        self._update_macro_run_state()
        self.show_status(f"실행 중인 매크로 {stopped}개를 정지했습니다.")

    def _set_debug_command(self, command: str) -> bool:
        command = str(command or "").strip().upper()
        if command not in {"RUN", "PAUSE", "STEP", "STOP"} or not self._running_macro_processes:
            return False
        written = False
        for path in self._run_control_paths.values():
            try:
                path.write_text(command, encoding="utf-8")
                written = True
            except OSError:
                continue
        return written

    @QtCore.Slot()
    def pause_running_macros(self) -> None:
        if self._set_debug_command("PAUSE"):
            self.show_status("다음 노드 실행 전에 일시정지합니다.")
            self._append_run_log("디버거 일시정지 요청")
        else:
            self.show_status("일시정지할 매크로가 없습니다.")

    @QtCore.Slot()
    def step_running_macros(self) -> None:
        if self._set_debug_command("STEP"):
            self.show_status("다음 노드 한 단계만 실행합니다.")
            self._append_run_log("디버거 한 단계 실행 요청")
        else:
            self.show_status("한 단계 실행할 매크로가 없습니다.")

    @QtCore.Slot()
    def resume_running_macros(self) -> None:
        if self._set_debug_command("RUN"):
            self.show_status("매크로 실행을 재개합니다.")
            self._append_run_log("디버거 실행 재개 요청")
        else:
            self.show_status("재개할 매크로가 없습니다.")

    def set_running_variable(self, name: str, value: str) -> bool:
        variable = str(name or "").strip().lstrip("$")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable) or not self._run_variable_paths:
            return False
        clean_value = str(value).replace("\r", " ").replace("\n", " ")
        written = False
        for path in self._run_variable_paths.values():
            try:
                existing: dict[str, str] = {}
                if path.is_file():
                    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                        if "=" in line and not line.lstrip().startswith((";", "#")):
                            key, old_value = line.split("=", 1)
                            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
                                existing[key.strip()] = old_value
                existing[variable] = clean_value
                body = "[variables]\n" + "".join(f"{key}={item}\n" for key, item in existing.items())
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(body, encoding="utf-8-sig")
                os.replace(temporary, path)
                written = True
            except OSError:
                continue
        if written:
            self._append_run_log(f"디버거 변수 변경 예약 | {variable}={clean_value}")
            self.show_status(f"다음 체크포인트에서 변수 {variable} 값을 {clean_value}(으)로 변경합니다.")
        return written

    def _update_macro_run_state(self) -> None:
        builder = self.pages.get("builder")
        setter = getattr(builder, "set_macro_running", None)
        if callable(setter):
            setter(bool(self._running_macro_processes))

    def _poll_running_macros(self) -> None:
        finished: list[int] = []
        for pid, (name, process, result_path, progress_path, click_path) in tuple(
            self._running_macro_processes.items()
        ):
            try:
                return_code = process.poll()
            except Exception as exc:
                self._append_run_log(f"상태 확인 실패 | {name} | PID {pid} | {exc}", "ERROR")
                finished.append(pid)
                continue
            self._set_running_node(name, self._read_macro_progress(progress_path))
            self._update_execution_trace(pid, name)
            self._sample_run_resources(pid)
            click_found = self._update_recent_click_preview(pid, click_path)
            if return_code is None:
                continue
            self._set_running_node(name, 0)
            status, code, detail = self._read_macro_result(result_path)
            if int(return_code) != 0 and status not in {"FAILED", "PARTIAL"}:
                status, code, detail = "FAILED", f"EXIT_{return_code}", f"프로세스 오류 코드 {return_code}"
            elif status in {"", "RUNNING"}:
                status, code, detail = "SUCCESS", "COMPLETED", "매크로 실행 완료"
            level = {"SUCCESS": "OK", "PARTIAL": "WARN", "FAILED": "ERROR"}.get(status, "INFO")
            self._append_run_log(
                f"프로세스 종료 | {name} | PID {pid} | 상태 {status} | {code} | {detail} | 코드 {return_code}",
                level,
            )
            if status == "FAILED":
                if not any(key[0] == pid for key in self._failure_capture_steps):
                    failure_capture = self._capture_failure_screen(name)
                    if failure_capture is not None:
                        self._append_trace_event(pid, 0, "CAPTURE", "실패 화면", str(failure_capture))
                        self._append_run_log(f"실패 화면 저장 | {failure_capture}", "ERROR")
                self.show_status(f"'{name}' 실행 실패 · {detail} · 로그를 확인하세요.")
            elif status == "PARTIAL":
                self.show_status(f"'{name}' 일부 완료 · {detail}")
            else:
                self.show_status(f"'{name}' 실행을 완료했습니다.")
            if "단계 테스트" in name and not click_found:
                builder = self.pages.get("builder")
                missing = getattr(builder, "show_click_preview_missing", None)
                if callable(missing):
                    missing()
            finished.append(pid)
        for pid in finished:
            self._running_macro_processes.pop(pid, None)
            self._seen_click_traces.pop(pid, None)
            self._run_control_paths.pop(pid, None)
            self._run_variable_paths.pop(pid, None)
            self._run_trace_paths.pop(pid, None)
            self._trace_signatures.pop(pid, None)
            self._run_resource_stats.pop(pid, None)
            self._failure_capture_steps = {key for key in self._failure_capture_steps if key[0] != pid}
        if not self._running_macro_processes:
            self._run_monitor.stop()
        self._update_macro_run_state()

    def _sample_run_resources(self, pid: int) -> None:
        stats = self._run_resource_stats.get(pid)
        if stats is None:
            return
        now = time.monotonic()
        if now - stats.get("last", 0.0) < 1.0:
            return
        stats["last"] = now
        try:
            import psutil  # type: ignore

            process = psutil.Process(pid)
            processes = [process, *process.children(recursive=True)]
            cpu = sum(item.cpu_percent(interval=None) for item in processes if item.is_running())
            memory = sum(item.memory_info().rss for item in processes if item.is_running()) / (1024 * 1024)
        except Exception:
            return
        stats["samples"] = stats.get("samples", 0.0) + 1.0
        stats["cpu_total"] = stats.get("cpu_total", 0.0) + cpu
        stats["cpu_max"] = max(stats.get("cpu_max", 0.0), cpu)
        stats["memory_max"] = max(stats.get("memory_max", 0.0), memory)
        average = stats["cpu_total"] / max(1.0, stats["samples"])
        self._append_trace_event(
            pid,
            0,
            "RESOURCE",
            "프로세스 자원",
            f"cpu={cpu:.2f}; cpu_avg={average:.2f}; cpu_max={stats['cpu_max']:.2f}; memory_mb={memory:.2f}; memory_max_mb={stats['memory_max']:.2f}",
        )

    def _set_running_node(self, display_name: str, step: int) -> None:
        builder = self.pages.get("builder")
        if builder is None:
            return
        macro_name = str(display_name).split(" · ", 1)[0]
        if str(getattr(builder, "current_name", "")) != macro_name:
            return
        setter = getattr(builder, "set_running_step", None)
        if callable(setter):
            setter(int(step))

    def _update_recent_click_preview(self, pid: int, path: Path | None) -> bool:
        if path is None or not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8-sig").strip()
            x_text, y_text, kind = raw.split("|", 2)
            x, y = int(x_text), int(y_text)
        except (OSError, TypeError, ValueError):
            return False
        if self._seen_click_traces.get(pid) == raw:
            return True
        self._seen_click_traces[pid] = raw
        step = self._read_macro_progress(
            self._running_macro_processes.get(pid, ("", None, None, None, None))[3]
            if pid in self._running_macro_processes
            else None
        )
        self._append_trace_event(pid, step, "DETAIL", "실제 클릭", f"click_x={x}; click_y={y}; kind={kind.strip()}")
        pixmap = self._capture_click_area(x, y)
        builder = self.pages.get("builder")
        show_preview = getattr(builder, "show_recent_click_preview", None)
        if pixmap is not None and callable(show_preview):
            show_preview(pixmap, x, y, kind.strip())
        return True

    def _append_trace_event(self, pid: int, step: int, status: str, label: str, detail: str) -> None:
        path = self._run_trace_paths.get(pid)
        if path is None:
            return
        clean_label = str(label).replace("|", "/").replace("\n", " ")
        clean_detail = str(detail).replace("|", "/").replace("\n", " ")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp}|{int(step)}|{status}|{clean_label}|{clean_detail}\n")
        except OSError:
            pass

    def _update_execution_trace(self, pid: int, display_name: str) -> None:
        path = self._run_trace_paths.get(pid)
        if path is None or not path.is_file():
            return
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self._trace_signatures.get(pid) == signature:
                return
            self._trace_signatures[pid] = signature
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            return
        starts: dict[int, datetime] = {}
        states: dict[int, dict[str, object]] = {}
        newly_failed: list[int] = []
        for raw in lines:
            parts = raw.split("|", 4)
            if len(parts) != 5:
                continue
            stamp_text, step_text, status, _label, detail = parts
            try:
                step = int(step_text)
                stamp = datetime.fromisoformat(stamp_text)
            except (TypeError, ValueError):
                continue
            if step <= 0:
                continue
            normalized = status.strip().upper()
            if normalized == "START":
                starts[step] = stamp
                states[step] = {"status": "RUNNING", "duration_ms": 0, "detail": ""}
            elif normalized in {"SUCCESS", "FAIL"}:
                started = starts.get(step, stamp)
                states[step] = {
                    "status": normalized,
                    "duration_ms": max(0, round((stamp - started).total_seconds() * 1000)),
                    "detail": states.get(step, {}).get("detail", ""),
                }
                if normalized == "FAIL" and (pid, step) not in self._failure_capture_steps:
                    newly_failed.append(step)
            elif normalized == "DETAIL":
                state = states.setdefault(step, {"status": "RUNNING", "duration_ms": 0, "detail": ""})
                state["detail"] = detail
        builder = self.pages.get("builder")
        macro_name = str(display_name).split(" · ", 1)[0]
        if str(getattr(builder, "current_name", "")) != macro_name:
            return
        setter = getattr(builder, "set_execution_states", None)
        if callable(setter):
            setter(states)
        for step in newly_failed:
            self._failure_capture_steps.add((pid, step))
            failure_capture = self._capture_failure_screen(f"{display_name}-node-{step}")
            if failure_capture is not None:
                self._append_trace_event(pid, step, "CAPTURE", "실패 화면", str(failure_capture))
                self._append_run_log(f"{step}번 노드 실패 화면 저장 | {failure_capture}", "ERROR")

    def _capture_failure_screen(self, name: str) -> Path | None:
        screens = QtGui.QGuiApplication.screens()
        if not screens:
            return None
        geometry = QtCore.QRect()
        for screen in screens:
            geometry = geometry.united(screen.geometry())
        if not geometry.isValid():
            return None
        canvas = QtGui.QPixmap(geometry.size())
        canvas.fill(QtGui.QColor("#000000"))
        painter = QtGui.QPainter(canvas)
        for screen in screens:
            shot = screen.grabWindow(0)
            painter.drawPixmap(screen.geometry().topLeft() - geometry.topLeft(), shot)
        painter.end()
        folder = self.repository.exports_dir / "failure_captures"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        destination = folder / f"{stamp}-{self.repository.safe_name(name)}.png"
        return destination if canvas.save(str(destination), "PNG") else None

    @staticmethod
    def _capture_click_area(x: int, y: int) -> QtGui.QPixmap | None:
        point = QtCore.QPoint(x, y)
        screens = QtGui.QGuiApplication.screens()
        screen = next((item for item in screens if item.geometry().contains(point)), None)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.geometry()
        local_x = x - geometry.x()
        local_y = y - geometry.y()
        width = min(360, geometry.width())
        height = min(220, geometry.height())
        left = max(0, min(local_x - width // 2, max(0, geometry.width() - width)))
        top = max(0, min(local_y - height // 2, max(0, geometry.height() - height)))
        pixmap = screen.grabWindow(0, left, top, width, height)
        if pixmap.isNull():
            return None
        marker_x = local_x - left
        marker_y = local_y - top
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        shadow = QtGui.QPen(QtGui.QColor(8, 12, 20, 220), 6)
        accent = QtGui.QPen(QtGui.QColor("#32e6d0"), 3)
        for pen in (shadow, accent):
            painter.setPen(pen)
            painter.drawLine(marker_x - 24, marker_y, marker_x + 24, marker_y)
            painter.drawLine(marker_x, marker_y - 24, marker_x, marker_y + 24)
            painter.drawEllipse(QtCore.QPoint(marker_x, marker_y), 13, 13)
        painter.end()
        return pixmap

    @staticmethod
    def _read_macro_progress(path: Path | None) -> int:
        if path is None or not path.is_file():
            return 0
        try:
            value = path.read_text(encoding="utf-8-sig").strip()
            return max(0, int(value))
        except (OSError, TypeError, ValueError):
            return 0

    @staticmethod
    def _read_macro_result(path: Path | None) -> tuple[str, str, str]:
        if path is None or not path.is_file():
            return "", "", ""
        try:
            raw = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            return "", "", ""
        parts = raw.split("|", 2)
        if len(parts) != 3:
            return "", "", raw
        return parts[0].strip().upper(), parts[1].strip(), parts[2].strip()

    def _append_run_log(self, message: str, level: str = "INFO") -> None:
        try:
            path = self.repository.exports_dir / "studio_run.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} | {level:<5} | {message}\n")
        except OSError:
            pass

    @QtCore.Slot(str)
    def show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._run_monitor.stop()
        self._event_trigger_timer.stop()
        if self._event_trigger_future is not None:
            self._event_trigger_future.cancel()
        self._event_trigger_executor.shutdown(wait=False, cancel_futures=True)
        shutdown_automation = getattr(self.pages.get("builder"), "shutdown_automation", None)
        if callable(shutdown_automation):
            shutdown_automation()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("sidebar_collapsed", self._sidebar_collapsed)
        super().closeEvent(event)
