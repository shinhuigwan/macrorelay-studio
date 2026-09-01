from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import uuid
import zipfile

from PySide6 import QtCore, QtWidgets

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
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        package_id = f"ai-video-test-{stamp}-{uuid.uuid4().hex[:6]}"
        stage = self.root / ".automation" / "ai-video-tests" / package_id
        exports = self.root / "exports" / "ai-video-tests"
        stage.mkdir(parents=True, exist_ok=False)
        exports.mkdir(parents=True, exist_ok=True)
        target_video = stage / "recording.mp4"
        shutil.copy2(video, target_video)
        actions = compact_action_timeline(events)
        duration_ms = max((int(item.get("t") or 0) for item in actions), default=0)
        purpose = purpose.strip() or "영상 속 작업의 목적과 반복 자동화 가능 여부 판단"
        timeline = {
            "format": "macrorelay-ai-video-test-actions-v1",
            "purpose": purpose,
            "video": "recording.mp4",
            "time_unit": "milliseconds_from_recording_start",
            "duration_ms": duration_ms,
            "action_count": len(actions),
            "actions": actions,
        }
        (stage / "actions.json").write_text(
            json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "format": "macrorelay-ai-video-test-v1",
            "package_id": package_id,
            "purpose": purpose,
            "duration_ms": duration_ms,
            "action_count": len(actions),
            "files": ["recording.mp4", "actions.json", "manifest.json", "prompt.txt", "README.txt"],
            "privacy": {
                "typed_characters_redacted_in_actions": True,
                "video_may_contain_visible_personal_information": True,
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = self._prompt(purpose)
        (stage / "prompt.txt").write_text(prompt, encoding="utf-8")
        (stage / "README.txt").write_text(
            "recording.mp4는 실제 화면, actions.json은 같은 시작점을 기준으로 한 사용자 액션입니다.\n"
            "이 테스트 패키지는 매크로 JSON 생성용이 아니라 GPT의 작업 이해도와 자동화 가능성 판단용입니다.\n"
            "영상에는 화면에 표시된 개인정보가 포함될 수 있으므로 전송 전에 확인하세요.\n",
            encoding="utf-8",
        )
        archive = exports / f"MacroRelay-AI-Video-Test-{stamp}.zip"
        suffix = 2
        while archive.exists():
            archive = exports / f"MacroRelay-AI-Video-Test-{stamp}-{suffix}.zip"
            suffix += 1
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(stage.iterdir()):
                bundle.write(path, path.name)
        return archive, stage

    @staticmethod
    def _prompt(purpose: str) -> str:
        return f"""첨부한 MacroRelay 테스트 ZIP을 분석해 주세요.

사용자가 입력한 목적: {purpose}

ZIP의 recording.mp4는 화면 녹화이고 actions.json은 동일한 시작 시점을 기준으로 기록한 클릭, 키보드, 휠, 대상 창 정보입니다. 두 파일의 시간(ms)을 함께 비교하세요. 이 단계에서는 매크로 JSON이나 코드를 만들지 마세요.

다음 다섯 가지만 한국어로 답해 주세요.
1. 영상에서 사용자가 하려던 작업을 한 문단으로 요약
2. 시간 순서대로 추론한 핵심 행동
3. MacroRelay로 안정적으로 자동화 가능한 부분
4. 영상만으로 판단하기 어려운 조건, 반복 규칙, 성공·실패 기준
5. 현재 자료만으로 자동 매크로 제작이 가능한지 `가능 / 일부 가능 / 불가능` 중 하나와 이유

액션 기록에 없는 동작을 확정적으로 지어내지 말고, 추론은 추론이라고 표시하세요.
"""


class AIVideoTestPurposeDialog(QtWidgets.QDialog):
    def __init__(self, event_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 영상 테스트 · 목적 입력")
        self.setMinimumWidth(560)
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("녹화가 완료되었습니다")
        title.setStyleSheet("font-size:17pt; font-weight:800;")
        root.addWidget(title)
        root.addWidget(QtWidgets.QLabel(f"영상과 액션 {event_count}개를 같은 시간축으로 저장했습니다."))
        root.addWidget(QtWidgets.QLabel("GPT가 무엇을 판단해야 하는지 한 줄로 적어주세요."))
        self.purpose = QtWidgets.QLineEdit()
        self.purpose.setPlaceholderText("예: 퀘스트를 진행하는 반복 작업인지 판단")
        self.purpose.setText("영상 속 작업의 목적과 반복 자동화 가능 여부 판단")
        self.purpose.selectAll()
        root.addWidget(self.purpose)
        note = QtWidgets.QLabel("입력 내용은 분석 방향만 알려주며, 이 테스트에서는 노드나 JSON을 만들지 않습니다.")
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox()
        create = buttons.addButton("테스트 패키지 만들기", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QtWidgets.QDialogButtonBox.RejectRole)
        create.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class AIVideoTestRecordingController(AIRecordingController):
    """Short continuous video + action timeline, intentionally without node generation."""

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
            capture_action_images=False,
        )
        self._purpose_dialog: AIVideoTestPurposeDialog | None = None
        self._pending_test: tuple[list[dict], Path | None] | None = None

    def start(self) -> None:
        super().start()
        if self.bar is not None:
            self.bar.setWindowTitle("AI 30초 영상 테스트")
            self.bar.label.setText("영상+액션 연속 녹화 · 최대 30초 · F10 종료")
            self.bar.mode_badge.setText("GPT 판단 테스트")
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
            self.failed.emit("AI 영상 테스트 패키지 생성을 취소했습니다.")
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
            self.failed.emit(f"AI 영상 테스트 패키지 생성 실패: {exc}")
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
