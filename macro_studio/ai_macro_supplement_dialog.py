"""Gemini integration:

1. Copy this file and ai_macro_supplement.py into macro_studio.
2. In RecordingReviewDialog._open_ai_macro_dialog, replace ONLY the import:
   from .ai_macro_supplement_dialog import AiMacroSupplementDialog as AiMacroDialog
3. Retain existing macro_ready/save-success/save-failure wiring.
4. No automatic execution, no changes to original recording review buttons.

PNG import reuses repository.add_asset_image. For Studio's native capture/detail
editor, connect edit_image_requested(request_id, base_record) to its non-modal
editor; after save call supply_registered_image(request_id, base_record, alias).
This explicit callback avoids guessing version-specific editor constructors.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from .ai_macro_dialog import AiMacroDialog
from .ai_macro_plan import load_plan, load_private_recording, PlanError, VISUAL, VERSION, SUPPORTED, _aliases
from .ai_macro_supplement import requests_from_plan, add_check_record, export_revision
from .image_editor import ScreenCaptureDialog, capture_virtual_desktop


class CaptureMetadataDialog(QtWidgets.QDialog):
    """보완 캡처 후 라벨(이름/목적) 및 설명 메모를 입력받는 대화창"""

    def __init__(self, default_label: str = "", default_note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("보완 캡처 라벨 및 설명 입력")
        self.resize(500, 260)
        layout = QtWidgets.QVBoxLayout(self)

        info_lbl = QtWidgets.QLabel(
            "캡처한 이미지의 라벨(목적)과 보완 설명을 입력하세요.\n"
            "이 정보는 manifest에 포함되어 Antigravity AI가 조건과 영역을 정확히 파악하도록 전달됩니다."
        )
        info_lbl.setStyleSheet("color: #A0AEC0; margin-bottom: 6px; font-size: 11px;")
        layout.addWidget(info_lbl)

        layout.addWidget(QtWidgets.QLabel("<b>항목 이름 / 라벨 (목적):</b>"))
        self.label_edit = QtWidgets.QLineEdit(default_label)
        self.label_edit.setPlaceholderText("예) 상승 영역 모두 포함된 이미지")
        self.label_edit.selectAll()
        layout.addWidget(self.label_edit)

        layout.addWidget(QtWidgets.QLabel("<b>보완 설명 / 상세 메모:</b>"))
        self.note_edit = QtWidgets.QPlainTextEdit(default_note)
        self.note_edit.setPlaceholderText(
            "예) 이 영역이 상승 이미지의 영역입니다. 숫자가 바뀌어도 안쪽 상승 아이콘으로 성공 판단"
        )
        self.note_edit.setMaximumHeight(85)
        layout.addWidget(self.note_edit)

        btn_box = QtWidgets.QHBoxLayout()
        btn_ok = QtWidgets.QPushButton("✔ 등록 완료")
        btn_ok.setStyleSheet("background: #2B6CB0; color: white; font-weight: bold; padding: 7px 18px; border-radius: 4px;")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QtWidgets.QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

    def get_data(self):
        label = self.label_edit.text().strip()
        note = self.note_edit.toPlainText().strip()
        return label, note


class AiMacroSupplementDialog(AiMacroDialog):
    edit_image_requested = QtCore.Signal(str, str)

    def __init__(self, repository, recorded_steps, parent=None):
        super().__init__(repository, recorded_steps, parent)
        self._private = None
        self._requests = []
        self._responses = {}
        self.setMinimumSize(780, 620)
        self.resize(780, 680)
        self.status.setTextFormat(QtCore.Qt.PlainText)

        # 우측 확장형 보완 패널
        self.panel = QtWidgets.QGroupBox("추가 이미지 · 설명 보완 (우측 확장 패널)")
        self.panel.setMinimumWidth(460)
        self.panel.setMaximumWidth(580)
        box = QtWidgets.QVBoxLayout(self.panel)

        header_row = QtWidgets.QHBoxLayout()
        panel_title = QtWidgets.QLabel("<b>📋 보완 요청 및 항목 편집</b>")
        btn_close_panel = QtWidgets.QPushButton("◀ 패널 접기")
        btn_close_panel.setStyleSheet("background: #2D3748; color: #A0AEC0; padding: 4px 10px; border-radius: 4px; font-weight: bold;")
        btn_close_panel.setToolTip("우측 보완 패널을 접고 창 너비를 기본 크기로 축소합니다.")
        btn_close_panel.clicked.connect(self.close_supplement_panel)
        header_row.addWidget(panel_title)
        header_row.addStretch(1)
        header_row.addWidget(btn_close_panel)
        box.addLayout(header_row)

        list_header = QtWidgets.QLabel("<b>보완 요청 및 추가 항목 목록</b> (더블클릭 시 라벨 수정):")
        box.addWidget(list_header)

        self.requests = QtWidgets.QListWidget()
        self.requests.setMaximumHeight(110)
        self.requests.currentRowChanged.connect(self._select_request)
        self.requests.itemDoubleClicked.connect(self._edit_request_label)
        box.addWidget(self.requests)

        self.reason = QtWidgets.QPlainTextEdit()
        self.reason.setReadOnly(True)
        self.reason.setMaximumHeight(75)
        box.addWidget(self.reason)

        self.base = QtWidgets.QComboBox()
        self.base.setToolTip("추가 이미지가 표시되는 동일한 프로그램·검색 영역의 기존 기록")
        box.addWidget(QtWidgets.QLabel("대상 프로그램·검색 설정을 가져올 기준 기록"))
        box.addWidget(self.base)

        # 캡처 및 항목 관리 버튼 행
        row1 = QtWidgets.QHBoxLayout()
        self.btn_capture = QtWidgets.QPushButton("📷 화면 직접 캡처 및 추가")
        self.btn_capture.setStyleSheet(
            "background: #1A365D; color: #63B3ED; font-weight: 700; padding: 6px 12px; "
            "border-radius: 4px; border: 1px solid #2B6CB0;"
        )
        self.btn_capture.setToolTip("화면에서 원하는 영역을 직접 마우스로 드래그하여 캡처하고 라벨·설명을 입력합니다.")
        self.btn_capture.clicked.connect(self._capture_screen)
        row1.addWidget(self.btn_capture)

        btn_add = QtWidgets.QPushButton("➕ 새 항목 추가")
        btn_add.setToolTip("직접 새 보완 항목(설명/메모)을 목록에 추가합니다.")
        btn_add.clicked.connect(self._add_manual_request)
        row1.addWidget(btn_add)

        btn_del = QtWidgets.QPushButton("🗑 선택 항목 삭제")
        btn_del.setToolTip("선택한 보완 항목을 목록에서 삭제합니다.")
        btn_del.clicked.connect(self._delete_request)
        row1.addWidget(btn_del)
        box.addLayout(row1)

        # 기존 파일/에셋 연결 버튼 행
        row2 = QtWidgets.QHBoxLayout()
        for label, callback, tip in [
            ("📁 PNG 파일 추가", self._pick_png, "PC의 PNG 파일을 선택하여 보완 이미지로 등록합니다."),
            ("🖼 등록 이미지 연결", self._pick_asset, "이미 등록된 에셋 이미지 중 하나를 선택하여 연결합니다."),
            ("연결 해제", self._clear_images, "선택한 항목에 연결된 이미지를 초기화합니다."),
        ]:
            button = QtWidgets.QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            row2.addWidget(button)
        box.addLayout(row2)

        self.preview = QtWidgets.QLabel("추가 이미지는 클릭 없는 확인용으로 사용합니다.")
        self.preview.setTextFormat(QtCore.Qt.PlainText)
        self.preview.setMaximumHeight(95)
        box.addWidget(self.preview)

        self.answer = QtWidgets.QPlainTextEdit()
        self.answer.setPlaceholderText("선택한 항목의 설명: 예) 숫자는 매번 변하고 초록 화살표 3개가 성공 조건")
        self.answer.setMaximumHeight(65)
        self.answer.textChanged.connect(self._save_answer)
        box.addWidget(self.answer)

        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setPlaceholderText("전체 수정 요청·판단 기준·실패 시 처리 방법")
        self.notes.setMaximumHeight(65)
        self.notes.textChanged.connect(self._update_resend_button_state)
        box.addWidget(self.notes)

        self.resend_btn = QtWidgets.QPushButton("보완 이미지·설명을 포함한 AI 패키지 저장 (Antigravity용)")
        self.resend_btn.clicked.connect(self._export_revision)
        box.addWidget(self.resend_btn)

        # 좌측 메인 레이아웃에 우측 확장 토글 버튼 배치
        self.btn_open_supplement = QtWidgets.QPushButton("▶ 3. 추가 이미지 · 설명 보완 패널 열기 (우측 확장)")
        self.btn_open_supplement.setStyleSheet("background: #2D3748; color: #63B3ED; font-weight: 700; padding: 7px; border-radius: 4px;")
        self.btn_open_supplement.setToolTip("클릭하면 우측으로 보완 패널이 확장되어 직접 캡처 및 설명을 추가할 수 있습니다.")
        self.btn_open_supplement.clicked.connect(self.toggle_supplement_panel)
        if hasattr(self, "main_layout"):
            self.main_layout.insertWidget(4, self.btn_open_supplement)
        else:
            self.layout().insertWidget(4, self.btn_open_supplement)

        # root_layout(수평) 우측에 패널 배치
        if hasattr(self, "root_layout"):
            self.root_layout.addWidget(self.panel, stretch=0)
        else:
            self.layout().addWidget(self.panel)
        self.panel.hide()

        self._ensure_private_ready(interactive=False)

    def _init_private_from_recorded_steps(self):
        if not self.recorded_steps:
            return
        records = {}
        for index, original in enumerate(self.recorded_steps, 1):
            if not isinstance(original, dict) or original.get("action") not in SUPPORTED:
                continue
            record_id = f"r{index:03d}"
            step = deepcopy(original)
            refs = []
            for image_index, alias in enumerate(_aliases(step), 1):
                path = self.repository.asset_path(alias)
                if path and Path(path).is_file():
                    try:
                        content = Path(path).read_bytes()
                        if content.startswith(b"\x89PNG\r\n\x1a\n"):
                            digest = hashlib.sha256(content).hexdigest()
                            refs.append({
                                "alias": alias,
                                "file": f"images/{record_id}-{image_index}.png",
                                "sha256": digest,
                            })
                    except Exception:
                        pass
            records[record_id] = {"step": step, "images": refs}
        self._private = {
            "schema": VERSION,
            "recording_id": uuid.uuid4().hex,
            "records": records,
        }

    def _ensure_private_ready(self, interactive: bool = False) -> bool:
        if self._private is not None:
            return True
        if self.recorded_steps:
            self._init_private_from_recorded_steps()
            if self._private is not None:
                self._populate_records_to_ui()
                return True
        if not interactive:
            return False
        local = self.local_recording
        if local is None:
            selected, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "PC에 보관한 private-recording.json 선택", "", "JSON (*.json)")
            if not selected:
                return False
            local = Path(selected)
        try:
            self._private = load_private_recording(local)
            self.local_recording = Path(local)
            self._populate_records_to_ui()
            return True
        except Exception as exc:
            self.status.setText(f"기록 로드 실패: {exc}")
            return False

    def _populate_records_to_ui(self):
        if not self._private:
            return
        if not self._requests:
            self._requests = [{"id": "manual", "label": "추가 보완", "reason": "필요한 이미지나 설명을 추가할 수 있습니다."}]
        self.base.clear()
        for rid, saved in self._private.get("records", {}).items():
            step = saved.get("step", {})
            if step.get("action") in VISUAL:
                self.base.addItem(f"{rid} · {step.get('label', step.get('action'))}", rid)
        self.requests.clear()
        for request in self._requests:
            self.requests.addItem(request["label"])
        if self.requests.count() > 0:
            self.requests.setCurrentRow(0)

    def open_supplement_panel(self):
        self._ensure_private_ready(interactive=False)
        self.panel.show()
        target_w = max(1300, self.width())
        self.resize(target_w, max(self.height(), 660))
        self.btn_open_supplement.setText("◀ 3. 추가 이미지 · 설명 보완 패널 닫기 (우측 접기)")
        self.btn_open_supplement.setStyleSheet("background: #553C9A; color: #FAF5FF; font-weight: 700; padding: 7px; border-radius: 4px;")
        self._update_resend_button_state()

    def close_supplement_panel(self):
        self.panel.hide()
        self.resize(780, self.height())
        self.btn_open_supplement.setText("▶ 3. 추가 이미지 · 설명 보완 패널 열기 (우측 확장)")
        self.btn_open_supplement.setStyleSheet("background: #2D3748; color: #63B3ED; font-weight: 700; padding: 7px; border-radius: 4px;")

    def toggle_supplement_panel(self):
        if self.panel.isVisible():
            self.close_supplement_panel()
        else:
            self.open_supplement_panel()

    def export(self):
        previous = self.local_recording
        super().export()
        if self.local_recording != previous:
            self._private = None
            self._requests = []
            self._responses = {}
            self.notes.clear()
            self._ensure_private_ready(interactive=False)
            self._update_resend_button_state()

    def import_plan(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "plan.json 선택", "", "JSON (*.json)")
        if not filename:
            return
        try:
            local = self.local_recording
            if local is None:
                selected, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "같은 녹화의 private-recording.json", "", "JSON (*.json)")
                if not selected:
                    return
                local = Path(selected)
            private = load_private_recording(local)
            plan = load_plan(filename)
            requests = requests_from_plan(plan, private)
            # Commit session only after the recording identity has been checked.
            self.local_recording = Path(local)
            self._private = private
            self.draft = None
            self.accept_draft.setEnabled(False)
            self.table.setRowCount(0)
            self._requests = requests or [{"id": "manual", "label": "추가 보완", "reason": "필요한 이미지나 설명을 추가할 수 있습니다."}]
            self._responses = {}
            self.base.clear()
            for rid, saved in private["records"].items():
                step = saved["step"]
                if step.get("action") in VISUAL:
                    self.base.addItem(f"{rid} · {step.get('label', step['action'])}", rid)
            self.requests.clear()
            for request in self._requests:
                self.requests.addItem(request["label"])
            self.requests.setCurrentRow(0)
            self._update_resend_button_state()
            if requests:
                self.open_supplement_panel()
                self.status.setText("추가 자료가 필요합니다. 우측 보완 패널에서 항목을 확인하고 이미지·설명을 보완해 ZIP을 다시 전달하세요.")
                return
            self.close_supplement_panel()
            from .ai_macro_plan import compile_plan
            self.draft = compile_plan(plan, private, self.repository.asset_path)
            self.table.setRowCount(len(self.draft["steps"]))
            for row, step in enumerate(self.draft["steps"]):
                for col, value in enumerate([row+1, step["label"], step.get("on_success", "종료"), step.get("on_fail", "종료")]):
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))
            self.accept_draft.setEnabled(True)
            self.status.setText("계획 검증 완료. 빌더로 보내거나 자료를 더 보완할 수 있습니다.")
        except Exception as exc:
            self.draft = None
            self.accept_draft.setEnabled(False)
            self.status.setText(f"가져오기 실패: {exc}")

    def _current(self):
        row = self.requests.currentRow()
        if not 0 <= row < len(self._requests):
            if self._requests:
                self.requests.setCurrentRow(0)
                return self._requests[0]
            raise PlanError("보완할 항목을 선택하거나 [➕ 새 항목 추가]를 누르세요.")
        return self._requests[row]

    def _select_request(self, row):
        if not 0 <= row < len(self._requests):
            return
        request = self._requests[row]
        response = self._responses.get(request["id"], {})
        self.reason.setPlainText(request["reason"])
        blocker = QtCore.QSignalBlocker(self.answer)
        self.answer.setPlainText(response.get("note", ""))
        del blocker
        ids = response.get("records", [])
        self.preview.setText("연결 기록: " + (", ".join(ids) or "없음"))
        if ids and self._private:
            alias = self._private["records"][ids[-1]]["images"][0]["alias"]
            path = self.repository.asset_path(alias)
            if path:
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    self.preview.setPixmap(pixmap.scaled(260, 90, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                    self.preview.setToolTip("연결 기록: " + ", ".join(ids))

    def _save_answer(self):
        row = self.requests.currentRow()
        if 0 <= row < len(self._requests):
            self._responses.setdefault(self._requests[row]["id"], {})["note"] = self.answer.toPlainText()
        self._update_resend_button_state()

    def _clear_images(self):
        try:
            self._responses.setdefault(self._current()["id"], {})["records"] = []
            self._select_request(self.requests.currentRow())
            self._update_resend_button_state()
        except Exception as exc:
            self.status.setText(str(exc))

    def _detect_client_rect(self, base_step: dict, screen_rect: QtCore.QRect) -> QtCore.QRect | None:
        win_text = str(base_step.get("region_window") or base_step.get("window") or base_step.get("window_title") or "").strip()
        exe_text = str(base_step.get("region_window_exe") or base_step.get("window_exe") or "").strip()
        if win_text or exe_text:
            try:
                from .image_search_test import _find_window
                hwnd = _find_window(exe_text, win_text)
                if hwnd:
                    import ctypes, ctypes.wintypes as wt
                    user32 = ctypes.windll.user32
                    crect = wt.RECT()
                    origin_pt = wt.POINT(0, 0)
                    if user32.GetClientRect(hwnd, ctypes.byref(crect)) and user32.ClientToScreen(hwnd, ctypes.byref(origin_pt)):
                        cw = int(crect.right - crect.left)
                        ch = int(crect.bottom - crect.top)
                        c_rect = QtCore.QRect(int(origin_pt.x), int(origin_pt.y), cw, ch)
                        if c_rect.isValid() and c_rect.intersects(screen_rect):
                            return c_rect
            except Exception:
                pass
        return None

    def _capture_screen(self):
        """직접 화면 드래그 캡처를 수행하고 라벨/설명을 입력받아 보완 항목으로 등록합니다."""
        if not self._ensure_private_ready():
            return

        base_rid = self.base.currentData()
        if not base_rid or base_rid not in self._private.get("records", {}):
            for rid, saved in self._private.get("records", {}).items():
                if saved.get("step", {}).get("action") in VISUAL:
                    base_rid = rid
                    break
        if not base_rid and self._private.get("records"):
            base_rid = next(iter(self._private["records"].keys()))
            self._private["records"][base_rid]["step"].setdefault("action", "screen_condition")
        if not base_rid:
            base_rid = "r001"
            self._private.setdefault("records", {})[base_rid] = {
                "step": {"action": "screen_condition", "label": "화면 기준"},
                "images": []
            }

        # 캡처를 위해 창 잠시 투명화
        self.setWindowOpacity(0.0)
        QtCore.QThread.msleep(180)
        QtWidgets.QApplication.processEvents()
        picker = None
        try:
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull() or not geometry.isValid():
                self.status.setText("화면 캡처에 실패했습니다.")
                return
            picker = ScreenCaptureDialog(
                pixmap, geometry, parent=None,
                hint_text="[보완 캡처] 화면 영역을 마우스로 드래그 후 Enter (취소: Esc)"
            )
            res = picker.exec()
            if res != QtWidgets.QDialog.Accepted:
                return
            qimg = picker.captured_image()
            rect = picker.selected_screen_rect()
            if qimg.isNull() or not rect.isValid() or rect.width() < 4 or rect.height() < 4:
                return
        finally:
            self.setWindowOpacity(1.0)
            self.raise_()
            self.activateWindow()

        # 클라이언트 상대 좌표 계산
        base_step = self._private["records"][base_rid]["step"]
        client_rect = self._detect_client_rect(base_step, rect)
        if client_rect is not None and client_rect.isValid():
            rel_region = [
                max(0, rect.left() - client_rect.left()),
                max(0, rect.top() - client_rect.top()),
                rect.right() - client_rect.left(),
                rect.bottom() - client_rect.top(),
            ]
            region_str = f"클라이언트 상대 영역: [{rel_region[0]}, {rel_region[1]}, {rel_region[2]}, {rel_region[3]}]"
        else:
            rel_region = [rect.left(), rect.top(), rect.right(), rect.bottom()]
            region_str = f"화면 영역: [{rel_region[0]}, {rel_region[1]}, {rel_region[2]}, {rel_region[3]}]"

        # 라벨 및 설명 메모 입력 다이얼로그 표시
        default_label = f"보완_캡처_{len(self._requests) + 1}"
        default_note = f"{region_str} - 이 영역의 상태가 필요합니다."
        dlg = CaptureMetadataDialog(default_label, default_note, parent=self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        label, note = dlg.get_data()
        if not label:
            label = default_label
        if not note:
            note = default_note

        try:
            alias = self.repository.add_asset_image(qimg, "ai-check-" + uuid.uuid4().hex)

            # 현재 항목이 비어있는 기본 placeholder('manual')인 경우 해당 항목 갱신
            curr_row = self.requests.currentRow()
            if (
                0 <= curr_row < len(self._requests)
                and self._requests[curr_row]["id"] == "manual"
                and not self._responses.get("manual", {}).get("records")
            ):
                req_id = "manual"
                self._requests[curr_row]["label"] = label
                self._requests[curr_row]["reason"] = note
                self.requests.item(curr_row).setText(label)
            else:
                req_id = f"manual-{uuid.uuid4().hex[:6]}"
                self._requests.append({"id": req_id, "label": label, "reason": note})
                self.requests.addItem(label)
                self.requests.setCurrentRow(self.requests.count() - 1)

            private, rid = add_check_record(
                self._private, base_rid, alias, label, self.repository.asset_path, search_region=rel_region
            )
            self._private = private
            self._responses.setdefault(req_id, {}).setdefault("records", []).append(rid)
            self._responses[req_id]["note"] = note

            self.draft = None
            self.accept_draft.setEnabled(False)
            self._select_request(self.requests.currentRow())
            self._update_resend_button_state()
            self.status.setText(
                f"✔ '{label}' ({rid}) 캡처 및 등록 완료!\n"
                f"추가할 항목이 더 있으면 캡처를 계속하거나, 아래 [보완 전달 ZIP 저장]을 누르세요."
            )
        except Exception as exc:
            self.status.setText(f"캡처 등록 실패: {exc}")

    def _add_manual_request(self):
        label, ok = QtWidgets.QInputDialog.getText(
            self, "새 보완 항목", "보완 항목 이름(라벨):", text=f"보완_항목_{len(self._requests) + 1}"
        )
        if not ok or not label.strip():
            return
        label = label.strip()
        reason, ok2 = QtWidgets.QInputDialog.getMultiLineText(
            self, "보완 설명", "설명 또는 요구사항 목적:", text=""
        )
        if not ok2:
            return
        req_id = f"manual-{uuid.uuid4().hex[:6]}"
        self._requests.append({"id": req_id, "label": label, "reason": reason.strip() or "사용자 추가 보완 항목"})
        self.requests.addItem(label)
        self.requests.setCurrentRow(self.requests.count() - 1)
        self._update_resend_button_state()

    def _delete_request(self):
        row = self.requests.currentRow()
        if not 0 <= row < len(self._requests):
            return
        if len(self._requests) <= 1:
            req = self._requests[0]
            req["label"] = "추가 보완"
            req["reason"] = "필요한 이미지나 설명을 추가할 수 있습니다."
            self.requests.item(0).setText("추가 보완")
            self._responses.pop(req["id"], None)
            self._select_request(0)
            self._update_resend_button_state()
            return
        req = self._requests.pop(row)
        self._responses.pop(req["id"], None)
        self.requests.takeItem(row)
        new_row = max(0, min(row, self.requests.count() - 1))
        self.requests.setCurrentRow(new_row)
        self._update_resend_button_state()

    def _edit_request_label(self, item):
        row = self.requests.row(item)
        if not 0 <= row < len(self._requests):
            return
        req = self._requests[row]
        new_label, ok = QtWidgets.QInputDialog.getText(
            self, "라벨(목적) 수정", "항목 이름 / 라벨 (목적):", text=req["label"]
        )
        if ok and new_label.strip():
            req["label"] = new_label.strip()
            item.setText(req["label"])
            records = self._responses.get(req["id"], {}).get("records", [])
            if records and self._private:
                for rid in records:
                    if rid in self._private.get("records", {}):
                        self._private["records"][rid]["step"]["label"] = req["label"]
            self._update_resend_button_state()

    def _update_resend_button_state(self):
        if not hasattr(self, "resend_btn"):
            return
        total_supplied = sum(len(resp.get("records", [])) for resp in self._responses.values())
        has_notes = any(resp.get("note", "").strip() for resp in self._responses.values()) or bool(self.notes.toPlainText().strip())
        if total_supplied > 0 or has_notes:
            self.resend_btn.setStyleSheet(
                "background: #553C9A; color: #FAF5FF; font-weight: 700; font-size: 13px; "
                "padding: 10px; border-radius: 6px; border: 1px solid #9F7AEA;"
            )
            self.resend_btn.setText(f"📦 보완 이미지({total_supplied}건)·설명을 포함한 AI 패키지 저장 (준비 완료)")
        else:
            self.resend_btn.setStyleSheet("")
            self.resend_btn.setText("보완 이미지·설명을 포함한 AI 패키지 저장 (Antigravity용)")

    def _pick_png(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "캡처·편집한 원본 PNG", "", "PNG (*.png)")
        if not filename:
            return
        try:
            request = self._current()
            if not self.base.currentData():
                raise PlanError("동일한 대상 프로그램의 기준 이미지 기록이 필요합니다.")
            path = Path(filename)
            if path.stat().st_size > 20_000_000:
                raise PlanError("이미지는 20MB 이하여야 합니다.")
            reader = QtGui.QImageReader(str(path))
            size = reader.size()
            if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 16_000_000:
                raise PlanError("이미지 크기가 잘못됐거나 1600만 픽셀을 초과합니다.")
            image = reader.read()
            if image.isNull():
                raise PlanError("정상적인 PNG 이미지가 아닙니다.")
            alias = self.repository.add_asset_image(image, "ai-check-" + uuid.uuid4().hex)
            self.supply_registered_image(request["id"], self.base.currentData(), alias)
        except Exception as exc:
            self.status.setText(f"이미지 추가 실패: {exc}")

    def _pick_asset(self):
        try:
            aliases = sorted(self.repository.load_assets())
            alias, ok = QtWidgets.QInputDialog.getItem(self, "등록 이미지", "이미지 선택", aliases, 0, False)
            if ok and alias:
                self.supply_registered_image(self._current()["id"], self.base.currentData(), alias)
        except Exception as exc:
            self.status.setText(f"연결 실패: {exc}")

    def supply_registered_image(self, request_id, base_record, alias, search_region=None):
        """Native editor integration callback. Call on Qt GUI thread after save."""
        if request_id not in {r["id"] for r in self._requests} or self._private is None:
            raise PlanError("현재 보완 요청이 아닙니다.")
        label = next(r["label"] for r in self._requests if r["id"] == request_id)
        private, rid = add_check_record(
            self._private, base_record, alias, label, self.repository.asset_path, search_region=search_region
        )
        self._private = private
        self._responses.setdefault(request_id, {}).setdefault("records", []).append(rid)
        self.draft = None
        self.accept_draft.setEnabled(False)
        self._select_request(self.requests.currentRow())
        self._update_resend_button_state()
        self.status.setText(f"✔ {rid} 확인용 이미지 연결 완료. 보완 패키지를 저장하거나 Antigravity에 알려주세요.")

    def _export_revision(self):
        if self._private is None:
            local = self.local_recording
            if local is None:
                selected, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "PC에 보관한 private-recording.json 선택", "", "JSON (*.json)")
                if not selected:
                    return
                local = Path(selected)
            try:
                self._private = load_private_recording(local)
                self.local_recording = Path(local)
            except Exception as exc:
                self.status.setText(f"기록 파일 로드 실패: {exc}")
                return

        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "보완본 패키지 저장 위치")
        if not directory:
            return
        try:
            purpose_text = self.purpose.toPlainText().strip() if hasattr(self, "purpose") else ""
            if not purpose_text:
                purpose_text = "매크로 구동 및 상태 판단 보완"
            notes_text = self.notes.toPlainText().strip()
            package, local = export_revision(
                self._private, self._requests, self._responses,
                purpose_text, notes_text, directory, self.repository.asset_path
            )
            self.local_recording = local
            self.draft = None
            self.accept_draft.setEnabled(False)
            try:
                QtGui.QGuiApplication.clipboard().setText(str(local.parent))
            except Exception:
                pass
            self.status.setText(
                f"🎉 보완 패키지 저장 완료! (폴더 경로가 클립보드에 자동 복사됨)\n"
                f"파일: {package}\n\n"
                f"👉 Antigravity에 알려주시면 맞춤 플랜이 생성되며, 생성 후 [⚡ Antigravity 최신 플랜 즉시 동기화]를 누르세요."
            )
            QtWidgets.QMessageBox.information(
                self, "보완 패키지 저장 완료",
                f"보완 패키지가 성공적으로 생성되었습니다.\n(폴더 경로가 클립보드에 자동 복사되었습니다)\n\n"
                f"위치:\n{package}\n\n"
                f"Antigravity에 이 내용을 전달하세요."
            )
        except Exception as exc:
            self.status.setText(f"보완본 저장 실패: {exc}")
            QtWidgets.QMessageBox.warning(self, "저장 실패", f"보완본 저장 실패:\n{exc}")
