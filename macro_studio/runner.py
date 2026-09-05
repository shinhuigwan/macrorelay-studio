from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunnerStatus:
    running: bool
    pid: int | None
    active_slots: int
    startup_enabled: bool


class QuickSlotsRunner:
    """Build and control the event-driven AutoHotkey quick-slot runner."""

    DEFAULT_EMERGENCY_HOTKEY = "Ctrl+Alt+Pause"
    _MODIFIERS = {"Ctrl": "^", "Alt": "!", "Shift": "+", "Meta": "#", "Win": "#"}
    _KEY_ALIASES = {
        "Esc": "Escape",
        "Del": "Delete",
        "Ins": "Insert",
        "PgUp": "PgUp",
        "PgDown": "PgDn",
        "Space": "Space",
        "Return": "Enter",
        "Backspace": "Backspace",
    }

    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self.root = repository.root
        self.runtime_dir = self.root / "runtime"
        self.slots_dir = self.runtime_dir / "slots"
        self.script_path = self.runtime_dir / "MacroRelay Runner.ahk"
        self.pid_path = self.runtime_dir / "runner.pid"
        self.log_path = self.runtime_dir / "runner.log"

    @property
    def startup_path(self) -> Path:
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "MacroRelay Runner.vbs"

    @staticmethod
    def _ahk_quote(value: str | Path) -> str:
        return str(value).replace('"', '""')

    @classmethod
    def to_ahk_hotkey(cls, sequence: str) -> str:
        sequence = sequence.strip()
        if not sequence:
            raise ValueError("단축키가 비어 있습니다.")
        if "," in sequence:
            raise ValueError("연속 키 조합은 지원하지 않습니다. 한 번에 누르는 단축키를 사용하세요.")
        parts = [part.strip() for part in sequence.split("+") if part.strip()]
        modifiers: list[str] = []
        keys: list[str] = []
        for part in parts:
            if part in cls._MODIFIERS:
                modifiers.append(cls._MODIFIERS[part])
            else:
                keys.append(part)
        if len(keys) != 1:
            raise ValueError(f"지원하지 않는 단축키 형식입니다: {sequence}")
        key = cls._KEY_ALIASES.get(keys[0], keys[0])
        if len(key) == 1:
            key = key.lower()
        return "".join(modifiers) + key

    def validate(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        seen: dict[str, int] = {}
        emergency = str((payload.get("runner") or {}).get("emergency_hotkey") or self.DEFAULT_EMERGENCY_HOTKEY)
        emergency_key = emergency.casefold()
        for index, slot in enumerate(payload.get("slots") or []):
            macro = str(slot.get("macro") or "").strip()
            hotkey = str(slot.get("hotkey") or "").strip()
            if bool(macro) != bool(hotkey):
                errors.append(f"슬롯 {index + 1}: 매크로와 단축키를 모두 지정해야 합니다.")
                continue
            if not macro:
                continue
            if not self.repository.macro_path(macro).exists():
                errors.append(f"슬롯 {index + 1}: '{macro}' 매크로를 찾을 수 없습니다.")
            try:
                self.to_ahk_hotkey(hotkey)
            except ValueError as exc:
                errors.append(f"슬롯 {index + 1}: {exc}")
            folded = hotkey.casefold()
            if folded in seen:
                errors.append(f"슬롯 {index + 1}: 슬롯 {seen[folded]}과 단축키가 중복됩니다.")
            else:
                seen[folded] = index + 1
            if folded == emergency_key:
                errors.append(f"슬롯 {index + 1}: 긴급 중지 단축키({emergency})와 중복됩니다.")
        return errors

    def build(self, payload: dict[str, Any]) -> Path:
        errors = self.validate(payload)
        if errors:
            raise ValueError("\n".join(errors))
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.slots_dir.mkdir(parents=True, exist_ok=True)
        active: list[tuple[int, str, str, Path]] = []
        for index, slot in enumerate(payload.get("slots") or []):
            macro = str(slot.get("macro") or "").strip()
            hotkey = str(slot.get("hotkey") or "").strip()
            if not macro or not hotkey:
                continue
            mode = str(slot.get("mode") or "hybrid")
            target = self.slots_dir / f"slot_{index + 1:02d}.ahk"
            self.repository.export(macro, output=target, browser_fast=mode in {"hybrid", "browser"})
            active.append((index + 1, macro, self.to_ahk_hotkey(hotkey), target))
        active_paths = {item[3] for item in active}
        for stale in self.slots_dir.glob("slot_*.ahk"):
            if stale not in active_paths:
                stale.unlink(missing_ok=True)
        runner_settings = payload.get("runner") or {}
        emergency = str(runner_settings.get("emergency_hotkey") or self.DEFAULT_EMERGENCY_HOTKEY)
        emergency_ahk = self.to_ahk_hotkey(emergency)
        script = self._render_script(active, emergency_ahk)
        self.script_path.write_text(script, encoding="utf-8-sig")
        return self.script_path

    def _render_script(self, active: list[tuple[int, str, str, Path]], emergency_ahk: str) -> str:
        studio_path = self.root / "run_studio.bat"
        icon_path = self.root / "branding" / "macrorelay-runner.ico"
        lines = [
            "; Generated by MacroRelay Studio. Edit Quick Slots in Studio only.",
            "#SingleInstance Force",
            "#NoEnv",
            "#Persistent",
            "SendMode Input",
            "SetBatchLines, -1",
            "ListLines, Off",
            "SetWorkingDir %A_ScriptDir%",
            f'MacroRelayPidFile := "{self._ahk_quote(self.pid_path)}"',
            f'MacroRelayLogFile := "{self._ahk_quote(self.log_path)}"',
            f'MacroRelayStudio := "{self._ahk_quote(studio_path)}"',
            "MacroRelayChildren := []",
            "Process, Exist",
            "MacroRelayPid := ErrorLevel",
            "FileDelete, %MacroRelayPidFile%",
            "FileAppend, %MacroRelayPid%, %MacroRelayPidFile%, UTF-8",
            "OnExit, MacroRelayCleanup",
            "Menu, Tray, NoStandard",
            "Menu, Tray, Add, MacroRelay Studio 열기, MacroRelayOpenStudio",
            "Menu, Tray, Default, MacroRelay Studio 열기",
            "Menu, Tray, Add, Quick Slots 일시 중지, MacroRelaySuspend",
            "Menu, Tray, Add, 다시 불러오기, MacroRelayReload",
            "Menu, Tray, Add",
            "Menu, Tray, Add, 실행 중인 매크로 모두 중지, MacroRelayEmergencyStop",
            "Menu, Tray, Add, Runner 종료, MacroRelayExit",
            f"Menu, Tray, Tip, MacroRelay Runner - Quick Slots {len(active)}개 활성",
            f'IconPath := "{self._ahk_quote(icon_path)}"',
            "if FileExist(IconPath)",
            "    Menu, Tray, Icon, %IconPath%",
            'MacroRelayLog("Runner started | slots=' + str(len(active)) + '")',
            "return",
            "",
        ]
        for slot_index, macro, hotkey, script_path in active:
            lines.extend(
                [
                    f"; Slot {slot_index}: {macro}",
                    f"{hotkey}::",
                    f'    MacroRelayRun("{self._ahk_quote(script_path)}", "{self._ahk_quote(macro)}")',
                    "return",
                    "",
                ]
            )
        lines.extend(
            [
                f"{emergency_ahk}::",
                "Suspend, Permit",
                "MacroRelayEmergencyStop:",
                "MacroRelayStopAll()",
                "return",
                "",
                "MacroRelayOpenStudio:",
                "Run, %MacroRelayStudio%",
                "return",
                "",
                "MacroRelaySuspend:",
                "Suspend, Toggle",
                "Menu, Tray, ToggleCheck, Quick Slots 일시 중지",
                "return",
                "",
                "MacroRelayReload:",
                "Reload",
                "return",
                "",
                "MacroRelayExit:",
                "ExitApp",
                "return",
                "",
                "MacroRelayCleanup:",
                "FileDelete, %MacroRelayPidFile%",
                "return",
                "",
                "MacroRelayRun(script, name) {",
                "    global MacroRelayChildren",
                "    if !FileExist(script) {",
                '        MacroRelayLog("Missing slot script | " . name)',
                "        TrayTip, MacroRelay Runner, 실행 파일을 찾을 수 없습니다: %name%, 3, 17",
                "        return",
                "    }",
                '    Run, "%A_AhkPath%" "%script%", %A_ScriptDir%, UseErrorLevel, childPid',
                "    if (ErrorLevel) {",
                '        MacroRelayLog("Launch failed | " . name)',
                "        TrayTip, MacroRelay Runner, 실행하지 못했습니다: %name%, 3, 17",
                "        return",
                "    }",
                "    MacroRelayChildren.Push(childPid)",
                '    MacroRelayLog("Launch | " . name . " | pid=" . childPid)',
                "}",
                "",
                "MacroRelayStopAll() {",
                "    global MacroRelayChildren",
                "    for index, pid in MacroRelayChildren",
                "        Process, Close, %pid%",
                "    MacroRelayChildren := []",
                '    MacroRelayLog("Emergency stop")',
                "    TrayTip, MacroRelay Runner, 실행 중인 매크로를 모두 중지했습니다., 2, 1",
                "}",
                "",
                "MacroRelayLog(message) {",
                "    global MacroRelayLogFile",
                "    FileGetSize, logSize, %MacroRelayLogFile%",
                "    if (logSize > 65536)",
                "        FileDelete, %MacroRelayLogFile%",
                "    FormatTime, stamp,, yyyy-MM-dd HH:mm:ss",
                '    FileAppend, % stamp . " | " . message . "`n", %MacroRelayLogFile%, UTF-8',
                "}",
                "",
            ]
        )
        return "\n".join(lines)

    def _read_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8-sig").strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_running(pid: int | None) -> bool:
        if not pid:
            return False
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    def status(self, payload: dict[str, Any] | None = None) -> RunnerStatus:
        payload = payload or self.repository.load_hotkeys()
        pid = self._read_pid()
        running = self._pid_running(pid)
        active_slots = sum(bool(slot.get("macro") and slot.get("hotkey")) for slot in payload.get("slots") or [])
        return RunnerStatus(running, pid if running else None, active_slots, self.startup_path.exists())

    def start(self, payload: dict[str, Any] | None = None) -> subprocess.Popen[Any]:
        payload = payload or self.repository.load_hotkeys()
        script = self.build(payload)
        executable = self.repository._read_text_path("ahk_path.txt")
        if not executable or not executable.exists():
            raise FileNotFoundError("AutoHotkey 실행 파일을 찾을 수 없습니다.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen([str(executable), str(script)], creationflags=flags)

    def stop(self) -> bool:
        pid = self._read_pid()
        if not self._pid_running(pid):
            self.pid_path.unlink(missing_ok=True)
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=flags,
        )
        self.pid_path.unlink(missing_ok=True)
        return True

    def restart(self, payload: dict[str, Any] | None = None) -> subprocess.Popen[Any]:
        self.stop()
        time.sleep(0.15)
        return self.start(payload)

    def set_startup(self, enabled: bool) -> None:
        if not enabled:
            self.startup_path.unlink(missing_ok=True)
            return
        if not self.script_path.exists():
            self.build(self.repository.load_hotkeys())
        executable = self.repository._read_text_path("ahk_path.txt")
        if not executable or not executable.exists():
            raise FileNotFoundError("AutoHotkey 실행 파일을 찾을 수 없습니다.")
        self.startup_path.parent.mkdir(parents=True, exist_ok=True)
        command = f'"{executable}" "{self.script_path}"'.replace('"', '""')
        content = 'Set shell = CreateObject("WScript.Shell")\n' f'shell.Run "{command}", 0, False\n'
        self.startup_path.write_text(content, encoding="utf-8-sig")
