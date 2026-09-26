"""Conservative host rebinding for a Deck backup restored on another PC."""

from __future__ import annotations

import copy
import hashlib
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows development
    winreg = None


IMAGE_ACTIONS = {"image_search", "screen_condition", "multi_image_search", "animation_search"}
_EXE_WITH_ARGS = re.compile(r'^\s*(?:"([^\"]+\.exe)"|(.+?\.exe))(?=\s|$)', re.IGNORECASE)


def host_fingerprint() -> str:
    """Compare hosts without placing the computer name in a shared backup."""
    return hashlib.sha256(platform.node().casefold().encode("utf-8")).hexdigest()[:16]


def resolve_executable(command: str) -> str:
    """Find the same installed program by executable name, never by old PC path."""
    expanded = os.path.expandvars(str(command or "").strip())
    match = _EXE_WITH_ARGS.match(expanded)
    if not match:
        return ""
    original = match.group(1) or match.group(2) or ""
    if Path(original).is_file():
        return original
    name = Path(original.replace("\\", "/")).name
    found = shutil.which(name)
    if found and Path(found).is_file():
        return found
    if winreg is not None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                    candidate = str(winreg.QueryValueEx(key, None)[0]).strip('"')
                    if Path(candidate).is_file():
                        return candidate
            except (OSError, ValueError):
                continue
    return ""


def rebind_command(command: str) -> tuple[str, bool, bool]:
    """Return (command, changed, unresolved) without altering arguments."""
    value = str(command or "")
    match = _EXE_WITH_ARGS.match(value)
    if not match:
        return value, False, False
    original = match.group(1) or match.group(2) or ""
    if Path(os.path.expandvars(original)).is_file():
        return value, False, False
    replacement = resolve_executable(value)
    if not replacement:
        return value, False, True
    suffix = value[match.end():]
    return f'"{replacement}"{suffix}', True, False


def _repair_window_reference(container: dict[str, Any], window_key: str, exe_key: str) -> bool:
    window = str(container.get(window_key) or "")
    exe = Path(str(container.get(exe_key) or "").replace("\\", "/")).name
    if not window.lower().startswith("ahk_id ") or not exe:
        return False
    container[window_key] = f"ahk_exe {exe}"
    return True


def adapt_backup_for_host(payload: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], dict[str, int]]:
    """Rebind what can be resolved; report coordinates that require calibration."""
    result = copy.deepcopy(payload)
    source = result.get("source_environment") or {}
    other_host = force or bool(source.get("host_fingerprint") and source["host_fingerprint"] != host_fingerprint())
    report = {"rebound_programs": 0, "rebound_windows": 0, "scaled_image_searches": 0,
              "fallback_search_regions": 0, "unresolved_programs": 0, "screen_coordinates": 0}
    if not other_host:
        return result, report

    def adapt_action(action: dict[str, Any]) -> None:
        if action.get("kind") == "open_target":
            value, changed, unresolved = rebind_command(str(action.get("target") or ""))
            if changed:
                action["target"] = value
                report["rebound_programs"] += 1
            if unresolved:
                report["unresolved_programs"] += 1
        for window_key, exe_key in (("target_window", "target_exe"), ("window", "window_exe")):
            report["rebound_windows"] += int(_repair_window_reference(action, window_key, exe_key))

    deck_config = result.get("deck_config") or {}
    if isinstance(deck_config, dict):
        # A saved desktop position may be off-screen on the destination PC.
        deck_config.pop("geometry", None)
        config = deck_config.get("config") or {}
        presets = config.get("slot_presets") or {}
        if isinstance(presets, dict):
            for preset in presets.values():
                if isinstance(preset, dict):
                    for slot in preset.get("slots") or []:
                        if isinstance(slot, dict) and isinstance(slot.get("action"), dict):
                            adapt_action(slot["action"])
    hotkeys = result.get("hotkeys") or {}
    if isinstance(hotkeys, dict):
        for slot in hotkeys.get("slots") or []:
            if isinstance(slot, dict) and isinstance(slot.get("action"), dict):
                adapt_action(slot["action"])

    macros = result.get("macros") or {}
    if isinstance(macros, dict):
        for macro in macros.values():
            if not isinstance(macro, dict):
                continue
            for step in macro.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if step.get("action") == "run_program":
                    value, changed, unresolved = rebind_command(str(step.get("command") or step.get("program") or ""))
                    if changed:
                        step["command"] = value
                        report["rebound_programs"] += 1
                    if unresolved:
                        report["unresolved_programs"] += 1
                if step.get("action") in IMAGE_ACTIONS and str(step.get("engine") or "opencv") == "opencv":
                    if step.get("search_profile") != "precise":
                        step["search_profile"] = "precise"
                        report["scaled_image_searches"] += 1
                if step.get("action") in IMAGE_ACTIONS and step.get("region_mode") in {"client", "window"}:
                    if (step.get("region") or step.get("search_regions")) and not step.get("fallback_full_region"):
                        step["fallback_full_region"] = True
                        report["fallback_search_regions"] += 1
                if str(step.get("coordinate_scope") or "").lower() == "screen" and step.get("action") in {"mouse_click", "inactive_click"}:
                    report["screen_coordinates"] += 1
                for window_key, exe_key in (("window", "window_exe"), ("region_window", "region_window_exe")):
                    report["rebound_windows"] += int(_repair_window_reference(step, window_key, exe_key))
                click = step.get("click")
                if isinstance(click, dict):
                    report["rebound_windows"] += int(_repair_window_reference(click, "window", "window_exe"))
    return result, report
