from __future__ import annotations

import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
import uuid
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
        # Keep handles for processes launched by this controller.  Dropping a
        # live Popen immediately produces ResourceWarning messages and also
        # prevents us from reaping the child cleanly when Studio shuts down.
        self._owned_processes: dict[Path, subprocess.Popen] = {}

    def load(self) -> dict[str, Any]:
        return load_config(self.root)

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        for key in ("enabled", "relay_url", "prefer_cloud", "device_name", "allow_remote_run", "allow_remote_stop", "allowed_macros"):
            if key in values:
                config[key] = values[key]
        save_config(config, self.root)
        return config

    def reset_identity(self) -> dict[str, Any]:
        """Rotate the device credentials and force a fresh mobile pairing.

        The relay intentionally stores only a hash of the device secret.  If
        a restored backup or a changed DPAPI vault contains another secret,
        the old device can never be repaired remotely.  Rotating the local
        identity creates a new, short-lived pairing code without exposing the
        secret or requiring a database edit on the relay.
        """
        config = self.load()
        self.stop_agent()
        config["device_id"] = uuid.uuid4().hex
        config["device_secret"] = secrets.token_urlsafe(32)
        config["enabled"] = True
        save_config(config, self.root)
        return config

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # Avoid launching tasklist every few seconds: a direct kernel handle
            # check keeps the always-on watchdog effectively idle.
            synchronize = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                return False
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
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
        if len(command) > 1 and command[1].lower().endswith(".py") and not Path(command[1]).is_file():
            return False
        pid_path.parent.mkdir(parents=True, exist_ok=True)
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
        self._owned_processes[pid_path] = process
        pid_path.write_text(str(process.pid), encoding="ascii")
        time.sleep(0.15)
        alive = self._pid_alive(process.pid)
        if not alive:
            process.poll()
            self._owned_processes.pop(pid_path, None)
        return alive

    def _stop(self, pid_path: Path) -> bool:
        owned_process = self._owned_processes.pop(pid_path, None)
        pid = self._read_pid(pid_path)
        if not pid and owned_process is not None:
            pid = owned_process.pid
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
        if owned_process is not None:
            try:
                owned_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # The PID-based status below remains authoritative.  Retain
                # the handle if the OS has not completed shutdown yet so a
                # later stop call can reap it without a warning.
                self._owned_processes[pid_path] = owned_process
        pid_path.unlink(missing_ok=True)
        return not self._pid_alive(pid)

    def _python_executable(self) -> str:
        """Resolve a real Python interpreter for the detached agent.

        A packaged Studio may have ``sys.executable`` pointing to the GUI
        launcher rather than Python.  Prefer it only when it is recognizably a
        Python executable, then fall back to the bundled runtimes.
        """
        current = Path(sys.executable)
        name = current.name.lower()
        if current.is_file() and (name.startswith("python") or name in {"py.exe", "pyw.exe"}):
            return str(current)
        candidates = (
            self.root / ".venv" / "Scripts" / "pythonw.exe",
            self.root / ".venv" / "Scripts" / "python.exe",
            self.root / "runtime" / "python.exe",
            self.root / "runtime" / "opencv" / "cp312" / "python" / "python.exe",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return str(current)

    def start_agent(self) -> bool:
        return self._start(self.agent_pid, [self._python_executable(), str(self.root / "remote_agent.py"), "--root", str(self.root)])

    def stop_agent(self) -> bool:
        return self._stop(self.agent_pid)

    def start_local_relay(self, port: int = 8765) -> bool:
        return self._start(
            self.relay_pid,
            [
                self._python_executable(),
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

    def uses_local_relay(self, config: dict[str, Any] | None = None) -> bool:
        """Return whether the configured endpoint is the relay bundled with Studio."""
        relay_url = str((config or self.load()).get("relay_url") or "http://127.0.0.1:8765")
        try:
            host = (urllib.parse.urlsplit(relay_url).hostname or "").lower()
        except ValueError:
            return False
        return host in {"127.0.0.1", "localhost", "::1"}

    def ensure_running(self) -> dict[str, Any]:
        """Keep remote access alive while Studio is open, independently of a macro run."""
        config = self.load()
        if not config.get("enabled"):
            return self.status()
        if self.uses_local_relay(config):
            parsed = urllib.parse.urlsplit(str(config.get("relay_url") or "http://127.0.0.1:8765"))
            self.start_local_relay(parsed.port or 8765)
        self.start_agent()
        return self.status()

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
        try:
            parsed = urllib.parse.urlsplit(relay_url)
            host = (parsed.hostname or "").lower()
            if host in {"127.0.0.1", "localhost", "::1"}:
                host = self.lan_ip()
                netloc = host
                if parsed.port:
                    netloc = f"{host}:{parsed.port}"
                relay_url = urllib.parse.urlunsplit(
                    (parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment)
                )
        except ValueError:
            pass
        return relay_url.rstrip("/") + "/"

    def open_mobile(self) -> None:
        webbrowser.open(self.mobile_url())
