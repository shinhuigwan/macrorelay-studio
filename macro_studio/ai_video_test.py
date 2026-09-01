from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from PySide6 import QtCore, QtWidgets

from .ai_automation import AIRecordingPackageBuilder
from .ai_recording import AIRecordingController


_BINARY_EVENT_FIELDS = {
    "image_sample_bmp",
    "image_after_bmp",
    "image_previous_bmps",
    "image_bmp",
    "screenshot_bmp",
}


def _clean_value(value):
    if isinstance(value, dict):
        return {
            str(key): _clean_value(item)
            for key, item in value.items()
            if str(key) not in _BINARY_EVENT_FIELDS
            and not str(key).endswith(("_bmp", "_b64", "_bytes"))
        }
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def compact_action_timeline(events: list[dict]) -> list[dict]:
    """Keep GPT-useful timing and target data without embedding screenshots."""
    actions: list[dict] = []
    ignored = {"gate_state", "mode_state", "mouse_after", "recorder_state"}
    for event in sorted(events, key=lambda item: int(item.get("t") or 0)):
        if not isinstance(event, dict) or str(event.get("type") or "") in ignored:
            continue
        clean = _clean_value(event)
        if clean.get("type") == "key" and clean.get("char") not in {None, "", "[REDACTED]"}:
            clean["char"] = "[REDACTED]"
        clean["order"] = len(actions) + 1
        actions.append(clean)
    return actions


class AIVideoTestPackageBuilder:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def build(self, events: list[dict], video: Path, purpose: str) -> tuple[Path, Path]:
        if not video.is_file():
            raise FileNotFoundError("녹화 영상을 만들지 못했습니다. OpenCV 구성요소 상태를 확인하세요.")
        package_id = f"ai-analysis-{uuid.uuid4().hex[:10]}"
        return AIRecordingPackageBuilder(self.root).build(
            events,
            video,
            package_id=package_id,
            trigger_config={"type": "manual"},
            purpose=purpose,
        )


class AIVideoTestPurposeDialog(QtWidgets.QDialog):
    def __init__(self, event_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 분석 녹화 · 목적 입력")
        self.setMinimumWidth(560)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("녹화가 완료되었습니다")
        title.setStyleSheet("font-size:17pt; font-weight:800;")
        root.addWidget(title)
        root.addWidget(QtWidgets.QLabel(f"영상과 액션 {event_count}개를 같은 시간축으로 저장했습니다."))
        root.addWidget(QtWidgets.QLabel("GPT가 완성할 자동화의 목적을 한 줄로 적어주세요."))
        self.purpose = QtWidgets.QLineEdit()
        self.purpose.setPlaceholderText("예: 이벤트 보상을 확인하고 모두 수령한 뒤 종료")
        self.purpose.setText("녹화한 작업을 안정적인 자동 매크로로 생성")
        self.purpose.selectAll()
        root.addWidget(self.purpose)
        note = QtWidgets.QLabel(
            "Studio가 영상·액션·무손실 PNG·전체 노드 명세·고정 GPT 지침을 ZIP에 자동 포함합니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox()
        create = buttons.addButton("AI 노드 생성 패키지 만들기", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QtWidgets.QDialogButtonBox.RejectRole)
        create.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class AIVideoTestRecordingController(AIRecordingController):
    """Short continuous video plus lossless action evidence for GPT node generation."""

    def __init__(self, repository, parent=None) -> None:
        super().__init__(
            repository,
            parent,
            ask_execution_condition=False,
            record_video=True,
            continuous_video=True,
            video_fps=5.0,
            video_max_width=1920,
            max_duration_ms=30_000,
            protect_typing=False,
            right_click_condition=False,
            workflow_controls=False,
            capture_action_images=True,
        )
        self._purpose_dialog: AIVideoTestPurposeDialog | None = None
        self._pending_test: tuple[list[dict], Path | None] | None = None

    def start(self) -> None:
        super().start()
        if self.bar is not None:
            self.bar.setWindowTitle("AI 30초 분석 녹화")
            self.bar.label.setText("영상+액션 연속 녹화 · 최대 30초 · F10 종료")
            self.bar.mode_badge.setText("AI 노드 생성")
            self.bar.branch_button.setVisible(False)
            self.bar.capture_button.setVisible(False)
            self.bar.setFixedWidth(730)
        self._capture_poll.stop()

    def _build_recording_package(self, events: list[dict], video: Path | None, trigger_config: dict) -> None:
        self._pending_test = (list(events), video)
        dialog = AIVideoTestPurposeDialog(len(events), self.host)
        self._purpose_dialog = dialog
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.NonModal)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dialog.finished.connect(self._purpose_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @QtCore.Slot(int)
    def _purpose_finished(self, result: int) -> None:
        dialog = self._purpose_dialog
        pending = self._pending_test
        if dialog is None or pending is None:
            return
        purpose = dialog.purpose.text().strip()
        self._purpose_dialog = None
        self._pending_test = None
        events, video = pending
        if result != QtWidgets.QDialog.Accepted:
            self._cleanup(video)
            self.failed.emit("AI 분석 패키지 생성을 취소했습니다.")
            self.deleteLater()
            return
        QtCore.QTimer.singleShot(0, lambda: self._finish_test_package(events, video, purpose))

    def _finish_test_package(self, events: list[dict], video: Path | None, purpose: str) -> None:
        try:
            if video is None:
                raise RuntimeError("영상 인코딩에 실패했습니다. 설정의 OpenCV 구성요소를 확인하세요.")
            archive, stage = AIVideoTestPackageBuilder(self.repository.root).build(events, video, purpose)
        except Exception as exc:
            self._cleanup(video)
            self.failed.emit(f"AI 분석 패키지 생성 실패: {exc}")
            self.deleteLater()
            return
        self._cleanup(video)
        self._pending_delivery = (str(archive), str(stage), events)
        QtCore.QTimer.singleShot(0, self._deliver_package)

    def _cleanup(self, video: Path | None) -> None:
        shutil.rmtree(self.frame_dir, ignore_errors=True)
        if video is not None:
            try:
                video.unlink(missing_ok=True)
            except OSError:
                pass
