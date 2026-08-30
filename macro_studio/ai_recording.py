from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from PySide6 import QtCore, QtGui

from .ai_automation import AIRecordingPackageBuilder, load_ai_recording
from .automation import RecordingBar, SmartRecordingController
from .image_editor import capture_virtual_desktop


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
                "0",
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
        self.bar.label.setText("2초 후 자동 녹화 · 평소처럼 작업하세요 · F10 종료")
        self.bar.mode_badge.setText("AI 자동")
        self.bar.set_gate_active(True)
        self.bar.stop_requested.connect(self.stop)
        self.bar.capture_requested.connect(lambda: None)
        self.bar.show()
        self.process.start()
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
        # The package must never expose printable keyboard input. Conservatively
        # redact the whole low-resolution video for five seconds after a key.
        # Lossless click PNG candidates are stored separately and contain no
        # recorded key values.
        # Once printable input begins, keep the remainder of the low-resolution
        # semantic video protected. The lossless click PNGs separately redact
        # focused input rectangles while preserving nearby buttons.
        if self._latest_key_time() >= 0:
            protected = QtGui.QPixmap(scaled.size())
            protected.fill(QtGui.QColor("#0B1018"))
            painter = QtGui.QPainter(protected)
            painter.setPen(QtGui.QColor("#DCE5F3"))
            painter.setFont(QtGui.QFont("Malgun Gothic", 15, QtGui.QFont.Bold))
            painter.drawText(protected.rect(), QtCore.Qt.AlignCenter, "민감한 키 입력 구간 · 화면 보호")
            painter.end()
            scaled = protected
        self._frame_index += 1
        target = self.frame_dir / f"frame-{self._frame_index:06d}-{elapsed:09d}.jpg"
        scaled.save(str(target), "JPG", 74)

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
            self.failed.emit("AI 매크로 녹화 프로세스를 시작하지 못했습니다.")

    def _finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self._video_timer.stop()
        if self.bar is not None:
            self.bar.close()
            self.bar.deleteLater()
            self.bar = None
        events = load_ai_recording(self.output)
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
        try:
            archive, stage = AIRecordingPackageBuilder(self.repository.root).build(events, video)
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
        self.package_completed.emit(str(archive), str(stage), events)
        self.deleteLater()
