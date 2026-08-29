from __future__ import annotations

import csv
import ctypes
import io
import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class EventTriggerEngine:
    """Low-frequency event detector used while Studio is open."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.states: dict[str, bool] = {}
        self.last_checks: dict[str, float] = {}
        self.last_schedule: dict[str, str] = {}

    @staticmethod
    def _process_names() -> set[str]:
        if os.name != "nt":
            return set()
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {row[0].casefold() for row in csv.reader(io.StringIO(result.stdout)) if row}

    @staticmethod
    def _window_titles() -> list[str]:
        if os.name != "nt":
            return []
        titles: list[str] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, _param):
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
                if buffer.value.strip():
                    titles.append(buffer.value)
            return True

        ctypes.windll.user32.EnumWindows(callback_type(callback), 0)
        return titles

    @staticmethod
    def _request(port: int, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        parsed = json.loads(b"".join(chunks).decode("utf-8", errors="replace") or "{}")
        return parsed if isinstance(parsed, dict) else {}

    def _start_engine(self, script_name: str, port: int, ocr: bool = False) -> bool:
        try:
            if self._request(port, {"cmd": "status"}, 0.2).get("ok"):
                return True
        except Exception:
            pass
        try:
            python, packages = self.repository._ensure_ocr_runtime() if ocr else self.repository._ensure_opencv_runtime()
        except Exception:
            return False
        script = self.repository.root / script_name
        if not script.is_file():
            return False
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(packages)
        subprocess.Popen(
            [str(python), str(script), "--server", "--port", str(port), "--idle-timeout", "600"],
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(25):
            time.sleep(0.04)
            try:
                if self._request(port, {"cmd": "status"}, 0.2).get("ok"):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _screen_region(raw: Any) -> list[int] | None:
        region = list(raw) if isinstance(raw, (list, tuple)) else [0, 0, 0, 0]
        if len(region) >= 4 and int(region[2]) > int(region[0]) and int(region[3]) > int(region[1]):
            return [int(value) for value in region[:4]]
        try:
            width = ctypes.windll.user32.GetSystemMetrics(78)
            height = ctypes.windll.user32.GetSystemMetrics(79)
            left = ctypes.windll.user32.GetSystemMetrics(76)
            top = ctypes.windll.user32.GetSystemMetrics(77)
            return [left, top, left + width, top + height]
        except Exception:
            return None

    def _image_matches(self, trigger: dict[str, Any]) -> bool:
        alias = str(trigger.get("asset") or "").strip()
        path = self.repository.asset_path(alias) if alias else None
        if path is None or not self._start_engine("vision_engine.py", 9235):
            return False
        region = self._screen_region(trigger.get("region"))
        if region is None:
            return False
        response = self._request(9235, {
            "cmd": "search", "image": str(path), "regions": [region], "threshold": float(trigger.get("threshold") or 0.86),
            "profile": str(trigger.get("profile") or "fast"), "timeout": 0, "poll": 50,
            "capture_context": f"trigger:{alias}",
        })
        return bool(response.get("ok") and response.get("found"))

    def _ocr_matches(self, trigger: dict[str, Any]) -> bool:
        if not self._start_engine("ocr_engine.py", 9234, ocr=True):
            return False
        region = self._screen_region(trigger.get("region"))
        if region is None:
            return False
        response = self._request(9234, {
            "cmd": "ocr", "region": region, "capture_mode": "screen",
            "profile": str(trigger.get("profile") or "number"), "lang": str(trigger.get("lang") or "eng+kor"),
            "ocr_action": "extract_number", "engine_preference": str(trigger.get("engine") or "auto"),
        }, timeout=15)
        try:
            value = float(response.get("extracted_number", response.get("extracted_value", response.get("number", 0))) or 0)
            expected = float(trigger.get("value") or 0)
        except (TypeError, ValueError):
            return False
        operator = str(trigger.get("operator") or ">=")
        return {">=": value >= expected, "<=": value <= expected, ">": value > expected, "<": value < expected, "==": value == expected, "!=": value != expected}.get(operator, False)

    def poll(self) -> list[tuple[str, str]]:
        now = datetime.now()
        process_names: set[str] | None = None
        window_titles: list[str] | None = None
        fired: list[tuple[str, str]] = []
        for summary in self.repository.list_macros():
            macro = self.repository.load_macro(summary.name)
            triggers = macro.get("triggers") if isinstance(macro.get("triggers"), list) else []
            for index, trigger in enumerate(triggers):
                if not isinstance(trigger, dict) or not trigger.get("enabled", True):
                    continue
                kind = str(trigger.get("type") or "")
                key = f"{summary.name}:{index}:{kind}"
                interval = max(0.5, float(trigger.get("interval") or (3 if kind in {"image_appears", "ocr_threshold"} else 1)))
                if time.monotonic() - self.last_checks.get(key, 0) < interval:
                    continue
                self.last_checks[key] = time.monotonic()
                matched = False
                if kind in {"process_start", "process_stop"}:
                    process_names = process_names if process_names is not None else self._process_names()
                    present = str(trigger.get("process") or "").casefold() in process_names
                    matched = present if kind == "process_start" else not present
                elif kind == "window_appears":
                    window_titles = window_titles if window_titles is not None else self._window_titles()
                    needle = str(trigger.get("title") or "").casefold()
                    matched = bool(needle and any(needle in title.casefold() for title in window_titles))
                elif kind == "schedule":
                    days = trigger.get("days") if isinstance(trigger.get("days"), list) else list(range(7))
                    target = str(trigger.get("time") or "00:00")
                    slot = now.strftime("%Y-%m-%d %H:%M")
                    matched = now.weekday() in {int(day) for day in days} and now.strftime("%H:%M") == target and self.last_schedule.get(key) != slot
                    if matched:
                        self.last_schedule[key] = slot
                elif kind == "image_appears":
                    matched = self._image_matches(trigger)
                elif kind == "ocr_threshold":
                    matched = self._ocr_matches(trigger)
                initialized = key in self.states
                previous = self.states.get(key, False)
                self.states[key] = matched
                # A stopped process is the normal baseline on first observation,
                # not a stop event.  Other positive conditions may intentionally
                # fire when Studio starts while they are already true.
                initial_stop_baseline = kind == "process_stop" and not initialized
                if matched and not initial_stop_baseline and (kind == "schedule" or not previous):
                    fired.append((summary.name, kind))
        return fired
