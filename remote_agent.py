"""Low-resource outbound agent for MacroRelay Remote."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from macro_studio.repository import MacroRepository
from remote_common import agent_headers, load_config, post_agent_event, request_json


class RemoteAgent:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.repository = MacroRepository(self.root)
        self.config = load_config(self.root)
        self.stop_event = threading.Event()
        self.process: subprocess.Popen[Any] | None = None
        self.running_name = ""
        self.result_path: Path | None = None
        self.progress_path: Path | None = None
        self.last_error = ""
        self.pairing_code = ""
        self.pair_expires = 0.0
        self.registered = False
        self._status_path = self.root / "runtime" / "remote_agent_status.json"

    @property
    def relay_url(self) -> str:
        return str(self.config.get("relay_url") or "http://127.0.0.1:8765")

    def _write_local_status(self, connected: bool) -> None:
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "connected": connected,
            "pairing_code": self.pairing_code,
            "pair_expires": self.pair_expires,
            "relay_url": self.relay_url,
            "device_name": self.config.get("device_name"),
            "running_macro": self.running_name,
            "last_error": self.last_error,
            "updated": time.time(),
        }
        temp = self._status_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self._status_path)

    def _macro_rows(self) -> list[dict[str, Any]]:
        allowed = {str(name) for name in self.config.get("allowed_macros", []) if str(name)}
        rows = []
        for summary in self.repository.list_macros():
            if allowed and summary.name not in allowed:
                continue
            rows.append({
                "name": summary.name,
                "description": summary.description,
                "steps": summary.steps,
                "modified": summary.modified.isoformat(),
            })
        return rows

    def state(self) -> dict[str, Any]:
        self._refresh_process()
        progress = 0
        if self.progress_path and self.progress_path.is_file():
            try:
                progress = int(self.progress_path.read_text(encoding="utf-8-sig").strip() or 0)
            except (OSError, ValueError):
                progress = 0
        return {
            "running": bool(self.process and self.process.poll() is None),
            "running_macro": self.running_name,
            "progress_step": progress,
            "macros": self._macro_rows(),
            "capabilities": {
                "run": bool(self.config.get("allow_remote_run", True)),
                "stop": bool(self.config.get("allow_remote_stop", True)),
            },
            "agent_version": "1.0",
        }

    def register(self) -> bool:
        response = request_json(
            self.relay_url,
            "POST",
            "/api/agent/register",
            {
                "device_id": self.config.get("device_id"),
                "device_secret": self.config.get("device_secret"),
                "device_name": self.config.get("device_name"),
            },
            timeout=8,
        )
        if not response.get("ok"):
            self.registered = False
            self.last_error = str(response.get("error") or "relay connection failed")
            self._write_local_status(False)
            return False
        self.pairing_code = str(response.get("pairing_code") or "")
        self.pair_expires = float(response.get("pair_expires") or 0)
        self.registered = True
        self.last_error = ""
        self._post_status()
        self._write_local_status(True)
        return True

    def _post_status(self) -> bool:
        response = request_json(
            self.relay_url,
            "POST",
            "/api/agent/status",
            self.state(),
            agent_headers(self.config),
            timeout=8,
        )
        return bool(response.get("ok"))

    def _refresh_process(self) -> None:
        if not self.process or self.process.poll() is None:
            return
        code = int(self.process.returncode or 0)
        status, detail = self._read_result()
        name = self.running_name
        post_agent_event(
            self.config,
            "macro_complete" if code == 0 and status != "FAIL" else "macro_failed",
            f"{name}: {'완료' if code == 0 and status != 'FAIL' else '실패'}",
            {"macro": name, "exit_code": code, "status": status, "detail": detail},
        )
        self.process = None
        self.running_name = ""
        self.result_path = None
        self.progress_path = None

    def _read_result(self) -> tuple[str, str]:
        if not self.result_path or not self.result_path.is_file():
            return ("UNKNOWN", "결과 파일 없음")
        try:
            lines = self.result_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError as exc:
            return ("UNKNOWN", str(exc))
        return (lines[0].strip() if lines else "UNKNOWN", "\n".join(lines[1:]).strip())

    def _run_macro(self, name: str) -> dict[str, Any]:
        if not self.config.get("allow_remote_run", True):
            return {"ok": False, "error": "remote_run_disabled"}
        allowed = {str(value) for value in self.config.get("allowed_macros", []) if str(value)}
        if allowed and name not in allowed:
            return {"ok": False, "error": "macro_not_allowed"}
        if name not in {item.name for item in self.repository.list_macros()}:
            return {"ok": False, "error": "macro_not_found"}
        self._refresh_process()
        if self.process and self.process.poll() is None:
            return {"ok": False, "error": "macro_already_running", "macro": self.running_name}
        try:
            process = self.repository.run_macro(name)
        except Exception as exc:  # GUI agent must return errors to the phone.
            return {"ok": False, "error": "run_failed", "detail": str(exc)}
        self.process = process
        self.running_name = name
        self.result_path = getattr(process, "macrorelay_result_path", None)
        self.progress_path = getattr(process, "macrorelay_progress_path", None)
        post_agent_event(self.config, "macro_start", f"{name}: 실행 시작", {"macro": name})
        return {"ok": True, "macro": name, "pid": process.pid}

    def _stop_macro(self) -> dict[str, Any]:
        if not self.config.get("allow_remote_stop", True):
            return {"ok": False, "error": "remote_stop_disabled"}
        self._refresh_process()
        if not self.process or self.process.poll() is not None:
            return {"ok": True, "stopped": False}
        name = self.running_name
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.process.terminate()
        self.process = None
        self.running_name = ""
        post_agent_event(self.config, "macro_stopped", f"{name}: 원격 정지", {"macro": name})
        return {"ok": True, "stopped": True, "macro": name}

    def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "")
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        if action in {"status", "list_macros"}:
            return {"ok": True, "state": self.state()}
        if action == "run_macro":
            return self._run_macro(str(payload.get("name") or ""))
        if action == "stop_macro":
            return self._stop_macro()
        return {"ok": False, "error": "unsupported_command"}

    def run(self) -> int:
        failures = 0
        next_status = 0.0
        while not self.stop_event.is_set():
            self.config = load_config(self.root)
            if not self.config.get("enabled"):
                self._write_local_status(False)
                self.stop_event.wait(2)
                continue
            if failures or not self.registered:
                if not self.register():
                    failures += 1
                    self.stop_event.wait(min(30, 2 ** min(failures, 5)))
                    continue
                failures = 0
            now = time.monotonic()
            if now >= next_status:
                if not self._post_status():
                    failures += 1
                    self.registered = False
                    continue
                next_status = now + 5
                self._write_local_status(True)
            response = request_json(
                self.relay_url,
                "GET",
                "/api/agent/commands?timeout=4",
                headers=agent_headers(self.config),
                timeout=8,
            )
            if not response.get("ok"):
                failures += 1
                self.registered = False
                continue
            for command in response.get("commands") or []:
                result = self.handle(command)
                request_json(
                    self.relay_url,
                    "POST",
                    f"/api/agent/commands/{int(command.get('id') or 0)}",
                    result,
                    agent_headers(self.config),
                    timeout=8,
                )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MacroRelay Remote PC agent")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    agent = RemoteAgent(args.root)
    try:
        return agent.run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
