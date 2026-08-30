from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from PySide6 import QtCore, QtGui, QtWidgets

from .ai_automation import AIRecordingPackageBuilder, load_ai_recording
from .automation import RecordingBar, SmartRecordingController
from .image_editor import ImageEditorDialog, ScreenCaptureDialog, capture_virtual_desktop


class AIExecutionConditionDialog(QtWidgets.QDialog):
    """Simple post-recording choice; trigger mechanics stay hidden."""

    def __init__(self, repository, events: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.events = events
        self.trigger_image = QtGui.QImage()
        self.trigger_scene = QtGui.QImage()
        self.trigger_window: dict = {}
        self.setWindowTitle("AI 자동 매크로 만들기")
        self.setMinimumWidth(640)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("녹화가 끝났습니다")
        title.setStyleSheet("font-size:18pt; font-weight:800;")
        root.addWidget(title)
        root.addWidget(QtWidgets.QLabel(f"✓ 작업 녹화 완료 · {len(events)}개 동작 기록됨"))

        condition = QtWidgets.QGroupBox("1. 언제 실행할까요?")
        condition_layout = QtWidgets.QVBoxLayout(condition)
        self.manual_radio = QtWidgets.QRadioButton("내가 직접 실행")
        self.auto_radio = QtWidgets.QRadioButton("특정 화면이 나타나면 자동 실행")
        self.manual_radio.setChecked(True)
        condition_layout.addWidget(self.manual_radio)
        condition_layout.addWidget(self.auto_radio)
        capture_row = QtWidgets.QHBoxLayout()
        self.capture_button = QtWidgets.QPushButton("⌖ 화면에서 지정")
        self.capture_button.clicked.connect(self._capture_trigger)
        self.edit_button = QtWidgets.QPushButton("✨ 누끼·상세 편집")
        self.edit_button.setEnabled(False)
        self.edit_button.setToolTip("자동 누끼, 색상 제거, 투명화 붓과 자르기로 시작 화면 이미지를 정리합니다.")
        self.edit_button.clicked.connect(self._edit_trigger_image)
        self.capture_status = QtWidgets.QLabel("시작 화면을 지정하세요.")
        self.capture_status.setObjectName("Muted")
        capture_row.addSpacing(24)
        capture_row.addWidget(self.capture_button)
        capture_row.addWidget(self.edit_button)
        capture_row.addWidget(self.capture_status, 1)
        condition_layout.addLayout(capture_row)
        self.preview = QtWidgets.QLabel()
        self.preview.setFixedHeight(120)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet("background:#0D131D; border:1px solid #29374A; border-radius:8px;")
        condition_layout.addWidget(self.preview)
        root.addWidget(condition)

        failure = QtWidgets.QGroupBox("2. 못 찾았을 때")
        failure_layout = QtWidgets.QGridLayout(failure)
        self.retry = QtWidgets.QSpinBox()
        self.retry.setRange(0, 20)
        self.retry.setValue(3)
        self.retry.setSuffix("회")
        self.failure_stop = QtWidgets.QRadioButton("매크로 종료")
        self.failure_restart = QtWidgets.QRadioButton("처음부터 다시 실행")
        self.failure_stop.setChecked(True)
        self.failure_notify = QtWidgets.QCheckBox("실패 시 알림")
        failure_layout.addWidget(QtWidgets.QLabel("다시 시도"), 0, 0)
        failure_layout.addWidget(self.retry, 0, 1)
        failure_layout.addWidget(self.failure_stop, 1, 0, 1, 2)
        failure_layout.addWidget(self.failure_restart, 2, 0, 1, 2)
        failure_layout.addWidget(self.failure_notify, 3, 0, 1, 2)
        root.addWidget(failure)

        self.auto_radio.toggled.connect(self._sync_mode)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        create = buttons.addButton("AI 분석 패키지 생성", QtWidgets.QDialogButtonBox.AcceptRole)
        create.setObjectName("Primary")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._sync_mode()

    def _sync_mode(self) -> None:
        enabled = self.auto_radio.isChecked()
        self.capture_button.setVisible(enabled)
        self.edit_button.setVisible(enabled)
        self.capture_status.setVisible(enabled)
        self.preview.setVisible(enabled)

    def _capture_trigger(self) -> None:
        parent = self.parentWidget()
        parent_was_visible = bool(parent is not None and parent.isVisible())
        parent_opacity = float(parent.windowOpacity()) if parent is not None else 1.0
        image = QtGui.QImage()
        screen_rect = QtCore.QRect()
        pixmap = QtGui.QPixmap()
        geometry = QtCore.QRect()
        self.hide()
        if parent_was_visible:
            # Hiding and showing the parent of a running modal dialog can leave
            # the native Windows parent disabled even after the dialog closes.
            # Make it transparent instead so Qt's modal ownership never breaks.
            parent.setWindowOpacity(0.0)
        try:
            wait = QtCore.QEventLoop(self)
            QtCore.QTimer.singleShot(120, wait.quit)
            wait.exec()
            pixmap, geometry = capture_virtual_desktop()
            if pixmap.isNull():
                QtWidgets.QMessageBox.warning(None, "화면 캡처", "화면 이미지를 가져오지 못했습니다.")
                return
            picker = ScreenCaptureDialog(pixmap, geometry, accept_on_release=True)
            accepted = picker.exec() == QtWidgets.QDialog.Accepted
            image = picker.captured_image() if accepted else QtGui.QImage()
            screen_rect = picker.selected_screen_rect() if accepted else QtCore.QRect()
            picker.deleteLater()
        finally:
            if parent_was_visible and parent is not None:
                parent.setWindowOpacity(parent_opacity)
                parent.raise_()
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(QtCore.Qt.ActiveWindowFocusReason)
        if image.isNull():
            return
        self.trigger_image = image
        self.trigger_window = self._window_at(screen_rect.center())
        scene_rect = QtCore.QRect()
        origin = self.trigger_window.get("client_origin") if isinstance(self.trigger_window.get("client_origin"), list) else []
        size = self.trigger_window.get("client_size") if isinstance(self.trigger_window.get("client_size"), list) else []
        if len(origin) >= 2 and len(size) >= 2:
            scene_rect = QtCore.QRect(
                int(origin[0]) - geometry.left(), int(origin[1]) - geometry.top(), int(size[0]), int(size[1])
            ).intersected(pixmap.rect())
        self.trigger_scene = pixmap.toImage().copy(scene_rect) if scene_rect.isValid() else pixmap.toImage()
        preview = QtGui.QPixmap.fromImage(image).scaled(
            560, 108, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        self.preview.setPixmap(preview)
        self.edit_button.setEnabled(True)
        if self.trigger_window:
            label = str(self.trigger_window.get("exe") or self.trigger_window.get("title") or "대상 프로그램")
            self.capture_status.setText(f"✓ 시작 화면 이미지 준비 완료 · {label}")
            self.capture_status.setStyleSheet("color:#65E0B5; font-weight:700;")
        else:
            self.capture_status.setText("⚠ 대상 프로그램 확인 필요 · 다시 지정해 주세요")
            self.capture_status.setStyleSheet("color:#FFB35C; font-weight:700;")

    def _edit_trigger_image(self) -> None:
        if self.trigger_image.isNull():
            QtWidgets.QMessageBox.information(self, "이미지 상세 편집", "먼저 화면에서 시작 이미지를 지정하세요.")
            return
        edit_root = self.repository.root / ".automation" / "trigger-edits"
        edit_root.mkdir(parents=True, exist_ok=True)
        temporary = edit_root / f"trigger-{uuid.uuid4().hex}.png"
        if not self.trigger_image.save(str(temporary), "PNG"):
            QtWidgets.QMessageBox.warning(self, "이미지 상세 편집", "임시 편집 이미지를 만들지 못했습니다.")
            return
        dialog = ImageEditorDialog(temporary, "AI 시작 화면", self.repository.history_dir, self)
        dialog.setWindowTitle("시작 화면 · 누끼 및 상세 편집")
        result = dialog.exec()
        if result == QtWidgets.QDialog.Accepted:
            edited = QtGui.QImage(str(temporary))
            if not edited.isNull():
                self.trigger_image = edited.convertToFormat(QtGui.QImage.Format_ARGB32)
                preview = QtGui.QPixmap.fromImage(self.trigger_image).scaled(
                    560, 108, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                )
                self.preview.setPixmap(preview)
                self.capture_status.setText("✓ 누끼·상세 편집 적용 완료")
                self.capture_status.setStyleSheet("color:#65E0B5; font-weight:700;")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    def _window_at(self, point: QtCore.QPoint) -> dict:
        candidates: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for event in reversed(self.events):
            window = event.get("window") if isinstance(event.get("window"), dict) else {}
            key = (str(window.get("exe") or ""), str(window.get("class") or ""), str(window.get("title") or ""))
            if not any(key) or key in seen:
                continue
            seen.add(key)
            candidates.append(window)
        for window in candidates:
            origin = window.get("client_origin") if isinstance(window.get("client_origin"), list) else []
            size = window.get("client_size") if isinstance(window.get("client_size"), list) else []
            if len(origin) >= 2 and len(size) >= 2:
                rect = QtCore.QRect(int(origin[0]), int(origin[1]), int(size[0]), int(size[1]))
                if rect.contains(point):
                    return dict(window)
            values = window.get("window_rect") if isinstance(window.get("window_rect"), list) else []
            if len(values) >= 4 and QtCore.QRect(
                int(values[0]), int(values[1]), int(values[2]) - int(values[0]), int(values[3]) - int(values[1])
            ).contains(point):
                return dict(window)
        return dict(candidates[0]) if len(candidates) == 1 else {}

    def _accept_checked(self) -> None:
        if self.auto_radio.isChecked() and (self.trigger_image.isNull() or not self.trigger_window):
            QtWidgets.QMessageBox.information(self, "시작 화면", "자동 실행에 사용할 화면을 먼저 지정하세요.")
            return
        self.accept()

    def configuration(self) -> dict:
        config: dict = {
            "type": "image_appear" if self.auto_radio.isChecked() else "manual",
            "failure_policy": {
                "retry_count": self.retry.value(),
                "retry_delay": 500,
                "after_failure": "restart" if self.failure_restart.isChecked() else "stop",
                "notify": self.failure_notify.isChecked(),
            },
        }
        if self.auto_radio.isChecked():
            config.update({
                "image": self.trigger_image.copy(), "scene": self.trigger_scene.copy(), "window": dict(self.trigger_window),
            })
        return config


class AIRecordingController(SmartRecordingController):
    package_completed = QtCore.Signal(str, str, list)

    def __init__(self, repository, parent=None) -> None:
        super().__init__(repository, parent)
        self.output = repository.root / ".automation" / f"ai-recording-{uuid.uuid4().hex}.jsonl"
        self.frame_dir = repository.root / ".automation" / f"ai-video-frames-{uuid.uuid4().hex}"
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self._video_timer = QtCore.QTimer(self)
        self._video_timer.setInterval(750)
        self._video_timer.timeout.connect(self._capture_video_frame)
        self._frame_index = 0
        self._frame_ring: list[tuple[int, QtGui.QPixmap]] = []
        self._saved_frame_times: set[int] = set()
        self._last_action_time = -1
        self._keep_video_until = -1
        self._video_segments: list[dict[str, int]] = []
        self._condition_dialog: AIExecutionConditionDialog | None = None
        self._pending_condition_events: list[dict] = []
        self._pending_condition_video: Path | None = None

    def start(self) -> None:
        helper = self.repository.root / "smart_recorder.py"
        if not helper.is_file():
            self.failed.emit("AI 녹화 도우미 파일을 찾을 수 없습니다.")
            return
        self.process = QtCore.QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments(
            [
                str(helper),
                "--out",
                str(self.output),
                "--exclude-pid",
                str(os.getpid()),
                "--delay",
                "2",
                "--capture-vk",
                str(0x77),
                "--stop-vk",
                str(0x79),
                "--hold-vk",
                "0",
                "--initial-active",
                "--redact-text",
                "--sample-width",
                "960",
                "--sample-height",
                "540",
            ]
        )
        self.process.setWorkingDirectory(str(self.repository.root))
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.bar = RecordingBar(self.host)
        self.bar.setWindowTitle("AI 자동 매크로 제작 녹화")
        self.bar.label.setText("2초 후 자동 녹화 · 평소처럼 작업 · F8 중요 화면 · F10 종료")
        self.bar.mode_badge.setText("AI 자동")
        self.bar.set_gate_active(True)
        self.bar.timer.stop()
        self.bar.label.setText("2초 후 자동 녹화 · 평소처럼 작업 · F8 중요 화면 · F10 종료")
        self.bar.stop_requested.connect(self.stop)
        self.bar.capture_requested.connect(self.request_image_capture)
        self.bar.show()
        self.process.start()
        self._capture_poll.start()
        self._video_timer.start()

    def _latest_key_time(self) -> int:
        if not self.output.is_file():
            return -100_000
        try:
            lines = self.output.read_text(encoding="utf-8-sig", errors="replace").splitlines()[-80:]
        except OSError:
            return -100_000
        latest = -100_000
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("type") == "key":
                latest = max(latest, int(item.get("t") or 0))
        return latest

    def _capture_video_frame(self) -> None:
        if self.process is None or self.process.state() == QtCore.QProcess.NotRunning:
            return
        pixmap, _geometry = capture_virtual_desktop()
        if pixmap.isNull():
            return
        width = min(1280, pixmap.width())
        scaled = pixmap.scaledToWidth(width, QtCore.Qt.SmoothTransformation)
        elapsed = max(0, int(self.bar.elapsed.elapsed() - 2000) if self.bar is not None else 0)
        # Protect only the short typing window. Later screens remain useful for
        # understanding success/failure while printable key values themselves
        # are never written to the timeline.
        latest_key = self._latest_key_time()
        if 0 <= elapsed - latest_key <= 5000:
            protected = QtGui.QPixmap(scaled.size())
            protected.fill(QtGui.QColor("#0B1018"))
            painter = QtGui.QPainter(protected)
            painter.setPen(QtGui.QColor("#DCE5F3"))
            painter.setFont(QtGui.QFont("Malgun Gothic", 15, QtGui.QFont.Bold))
            painter.drawText(protected.rect(), QtCore.Qt.AlignCenter, "민감한 키 입력 구간 · 화면 보호")
            painter.end()
            scaled = protected
        self._frame_ring.append((elapsed, scaled.copy()))
        self._frame_ring = [(stamp, frame) for stamp, frame in self._frame_ring if stamp >= elapsed - 2200]
        latest_action = self._latest_recorded_action_time()
        if latest_action > self._last_action_time:
            self._last_action_time = latest_action
            start = max(0, latest_action - 2000)
            end = latest_action + 2000
            if self._video_segments and start <= self._video_segments[-1]["end_ms"] + 250:
                self._video_segments[-1]["end_ms"] = max(self._video_segments[-1]["end_ms"], end)
            else:
                self._video_segments.append({"start_ms": start, "end_ms": end})
            self._keep_video_until = max(self._keep_video_until, end)
            self._flush_frame_ring()
        elif elapsed <= self._keep_video_until:
            self._save_video_frame(elapsed, scaled)

    def _latest_recorded_action_time(self) -> int:
        if not self.output.is_file():
            return -1
        try:
            lines = self.output.read_text(encoding="utf-8-sig", errors="replace").splitlines()[-120:]
        except OSError:
            return -1
        latest = -1
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("type") in {"mouse", "key", "mouse_drag", "capture_request"}:
                latest = max(latest, int(item.get("t") or 0))
        return latest

    def _save_video_frame(self, elapsed: int, pixmap: QtGui.QPixmap) -> None:
        if elapsed in self._saved_frame_times:
            return
        self._saved_frame_times.add(elapsed)
        self._frame_index += 1
        target = self.frame_dir / f"frame-{self._frame_index:06d}-{elapsed:09d}.jpg"
        pixmap.save(str(target), "JPG", 74)

    def _flush_frame_ring(self) -> None:
        for elapsed, pixmap in self._frame_ring:
            if self._last_action_time >= 0 and elapsed > self._keep_video_until:
                continue
            self._save_video_frame(elapsed, pixmap)

    def _encode_video(self) -> Path | None:
        helper = self.repository.root / "ai_video.py"
        if not helper.is_file() or not any(self.frame_dir.glob("*.jpg")):
            return None
        output = self.frame_dir.parent / f"{self.frame_dir.name}.mp4"
        try:
            python, packages = self.repository._ensure_opencv_runtime()
        except Exception:
            python, packages = Path(sys.executable), Path()
        environment = os.environ.copy()
        if packages.is_dir():
            current = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = str(packages) + (os.pathsep + current if current else "")
        try:
            result = subprocess.run(
                [str(python), str(helper), str(self.frame_dir), str(output), "--fps", "1.333333"],
                cwd=str(self.repository.root),
                env=environment,
                check=False,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return output if result.returncode == 0 and output.is_file() else None

    def _process_error(self, error: QtCore.QProcess.ProcessError) -> None:
        if error == QtCore.QProcess.FailedToStart:
            self._video_timer.stop()
            self._capture_poll.stop()
            self.failed.emit("AI 매크로 녹화 프로세스를 시작하지 못했습니다.")

    def _finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self._video_timer.stop()
        self._capture_poll.stop()
        self._flush_frame_ring()
        if self.bar is not None:
            self.bar.close()
            self.bar.deleteLater()
            self.bar = None
        events = sorted(
            [*load_ai_recording(self.output), *self._manual_captures],
            key=lambda item: int(item.get("t") or 0),
        )
        try:
            self.output.unlink(missing_ok=True)
        except OSError:
            pass
        if not events:
            detail = "기록된 동작이 없습니다. AI 녹화가 시작된 뒤 작업하고 F10으로 종료하세요."
            if exit_code not in {0, 15} and not self._manual_stop:
                detail += f" (종료 코드 {exit_code})"
            self.failed.emit(detail)
            shutil.rmtree(self.frame_dir, ignore_errors=True)
            self.deleteLater()
            return
        video = self._encode_video()
        self._show_execution_condition(events, video)

    def _show_execution_condition(self, events: list[dict], video: Path | None) -> None:
        """Open post-recording setup without a nested modal event loop.

        QDialog.exec() disables the native Windows owner until its nested event
        loop unwinds. The capture picker temporarily hides this dialog, and a
        completion notice is opened immediately afterwards; that combination
        could leave the Studio owner disabled even though every dialog had
        disappeared. Keeping this workflow asynchronous avoids that stale
        native modal state entirely.
        """
        self._pending_condition_events = list(events)
        self._pending_condition_video = video
        dialog = AIExecutionConditionDialog(self.repository, events, self.host)
        self._condition_dialog = dialog
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.NonModal)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dialog.finished.connect(self._execution_condition_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @QtCore.Slot(int)
    def _execution_condition_finished(self, result: int) -> None:
        dialog = self._condition_dialog
        if dialog is None:
            return
        events = self._pending_condition_events
        video = self._pending_condition_video
        trigger_config = dialog.configuration() if result == QtWidgets.QDialog.Accepted else {"type": "manual"}
        self._condition_dialog = None
        self._pending_condition_events = []
        self._pending_condition_video = None
        # Let the dialog's native window close before package generation and
        # before the completion notice is created.
        QtCore.QTimer.singleShot(
            0,
            lambda: self._build_recording_package(events, video, trigger_config),
        )

    def _build_recording_package(
        self,
        events: list[dict],
        video: Path | None,
        trigger_config: dict,
    ) -> None:
        try:
            archive, stage = AIRecordingPackageBuilder(self.repository.root).build(
                events, video, trigger_config=trigger_config, video_segments=self._video_segments
            )
        except Exception as exc:
            self.failed.emit(f"AI 분석 패키지 생성 실패: {exc}")
            shutil.rmtree(self.frame_dir, ignore_errors=True)
            if video is not None:
                video.unlink(missing_ok=True)
            self.deleteLater()
            return
        shutil.rmtree(self.frame_dir, ignore_errors=True)
        if video is not None:
            video.unlink(missing_ok=True)
        self._pending_delivery = (str(archive), str(stage), events)
        # Deliver on a clean event-loop turn after package generation.
        QtCore.QTimer.singleShot(0, self._deliver_package)

    def _deliver_package(self) -> None:
        pending = getattr(self, "_pending_delivery", None)
        self._pending_delivery = None
        if pending:
            archive, stage, events = pending
            self.package_completed.emit(archive, stage, events)
        self.deleteLater()
