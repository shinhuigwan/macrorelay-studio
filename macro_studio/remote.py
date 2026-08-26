from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from remote_common import load_config, save_config


class RemoteController:
    """Starts and observes the optional relay/agent background processes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.agent_pid = self.runtime / "remote_agent.pid"
        self.relay_pid = self.runtime / "remote_relay.pid"
        self.agent_status_path = self.runtime / "remote_agent_status.json"

    def load(self) -> dict[str, Any]:
        return load_config(self.root)

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        for key in ("enabled", "relay_url", "device_name", "allow_remote_run", "allow_remote_stop", "allowed_macros"):
            if key in values:
                config[key] = values[key]
        save_config(config, self.root)
        return config

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.returncode == 0 and f'"{pid}"' in result.stdout
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _read_pid(path: Path) -> int:
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return 0

    def _start(self, pid_path: Path, command: list[str]) -> bool:
        old_pid = self._read_pid(pid_path)
        if self._pid_alive(old_pid):
            return True
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        process = subprocess.Popen(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        pid_path.write_text(str(process.pid), encoding="ascii")
        time.sleep(0.15)
        return self._pid_alive(process.pid)

    def _stop(self, pid_path: Path) -> bool:
        pid = self._read_pid(pid_path)
        if not pid:
            return True
        if self._pid_alive(pid):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                os.kill(pid, 15)
        pid_path.unlink(missing_ok=True)
        return not self._pid_alive(pid)

    def start_agent(self) -> bool:
        return self._start(self.agent_pid, [sys.executable, str(self.root / "remote_agent.py"), "--root", str(self.root)])

    def stop_agent(self) -> bool:
        return self._stop(self.agent_pid)

    def start_local_relay(self, port: int = 8765) -> bool:
        return self._start(
            self.relay_pid,
            [
                sys.executable,
                str(self.root / "remote" / "relay_server.py"),
                "--host",
                "0.0.0.0",
                "--port",
                str(port),
                "--database",
                str(self.runtime / "remote_relay.db"),
            ],
        )

    def stop_local_relay(self) -> bool:
        return self._stop(self.relay_pid)

    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(self.agent_status_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            pass
        payload["agent_running"] = self._pid_alive(self._read_pid(self.agent_pid))
        payload["relay_running"] = self._pid_alive(self._read_pid(self.relay_pid))
        return payload

    @staticmethod
    def lan_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            sock.close()

    def mobile_url(self) -> str:
        relay_url = str(self.load().get("relay_url") or "http://127.0.0.1:8765")
        if "127.0.0.1" in relay_url or "localhost" in relay_url:
            relay_url = relay_url.replace("127.0.0.1", self.lan_ip()).replace("localhost", self.lan_ip())
        return relay_url.rstrip("/") + "/"

    def open_mobile(self) -> None:
        webbrowser.open(self.mobile_url())

